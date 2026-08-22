from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TASK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK_DIR))

from agents.editor import EditorialAgent  # noqa: E402
from agents.method import MethodDesignAgent  # noqa: E402
from agents.verifier import ConflictType, SUPPORTED_CONFLICT_TYPES  # noqa: E402
from backends import DeterministicBackend, OpenAICompatibleBackend  # noqa: E402
from coordinator import ResearchWritingCoordinator  # noqa: E402
from protocol import (  # noqa: E402
    AGENT_MESSAGE_SCHEMA,
    AgentMessage,
    DocumentStore,
    MessageBus,
    MessageStatus,
    MessageType,
    Priority,
    TopologyViolationError,
    WriteConflictError,
)


class AlwaysFailBackend:
    """Test double proving that a model outage does not stop an agent."""

    name = "always-fail"
    model = "unavailable-model"
    last_retries = 2

    async def generate(self, **_: object) -> str:
        raise RuntimeError("simulated provider outage")


class CapturingBackend:
    """Records the real-model prompt while returning deterministic content."""

    name = "capturing-backend"
    model = "capturing-model"
    last_retries = 0

    def __init__(self) -> None:
        self.user_prompts: list[str] = []

    async def generate(
        self, *, system_prompt: str, user_prompt: str, fallback: str
    ) -> str:
        del system_prompt
        self.user_prompts.append(user_prompt)
        return fallback


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runtime = TASK_DIR / "tests" / "_runtime"
        if self.runtime.exists():
            shutil.rmtree(self.runtime)
        self.runtime.mkdir(parents=True)

    async def asyncTearDown(self) -> None:
        if self.runtime.exists():
            shutil.rmtree(self.runtime)

    async def test_message_schema_roundtrip_and_idempotency(self) -> None:
        bus = MessageBus(self.runtime / "messages.jsonl", console=False)
        message = AgentMessage.create(
            message_type=MessageType.TASK_ASSIGN,
            sender="coordinator",
            receiver="method_agent",
            payload={"summary": "test", "sections": ["研究方法"]},
            conversation_id="conversation-test",
            priority=Priority.HIGH,
        )
        record = message.to_dict()
        required = set(AGENT_MESSAGE_SCHEMA["required"])
        self.assertTrue(required.issubset(record))
        self.assertEqual(record["summary"], "test")
        self.assertEqual(record["status"], MessageStatus.SENT.value)
        self.assertGreater(record["token_count"], 0)
        self.assertEqual(AgentMessage.from_dict(record).to_dict(), record)
        self.assertTrue(await bus.send(message))
        self.assertFalse(await bus.send(message))
        self.assertEqual(len(bus.messages), 1)

    async def test_message_id_rejects_non_hex_characters(self) -> None:
        message = AgentMessage.create(
            message_type=MessageType.TASK_ASSIGN,
            sender="coordinator",
            receiver="method_agent",
            payload={"summary": "test"},
            conversation_id="conversation-test",
        )
        record = message.to_dict()
        record["message_id"] = "msg-zzzzzzzzzzzz"
        with self.assertRaisesRegex(ValueError, "message_id"):
            AgentMessage.from_dict(record)

    async def test_star_topology_rejects_direct_leaf_message(self) -> None:
        bus = MessageBus(self.runtime / "messages.jsonl", console=False)
        message = AgentMessage.create(
            message_type=MessageType.INFO_REQUEST,
            sender="method_agent",
            receiver="experiment_agent",
            payload={"summary": "direct communication"},
            conversation_id="conversation-test",
        )
        with self.assertRaises(TopologyViolationError):
            await bus.send(message)

    async def test_section_ownership_and_compare_and_swap_retry(self) -> None:
        store = DocumentStore(["section"], ownership={"section": "method_agent"})
        first = await store.compare_and_swap(
            "section", expected_version=0, new_content="v1", writer="method_agent"
        )
        self.assertEqual(first.version, 1)
        with self.assertRaises(PermissionError):
            await store.compare_and_swap(
                "section", expected_version=1, new_content="forbidden", writer="editor_agent"
            )
        with self.assertRaises(WriteConflictError):
            await store.compare_and_swap(
                "section", expected_version=0, new_content="stale", writer="method_agent"
            )
        updated, retries = await store.compare_and_swap_with_retry(
            "section",
            base_version=0,
            writer="method_agent",
            rewrite=lambda latest: latest.content + "+v2",
        )
        self.assertEqual(retries, 1)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.content, "v1+v2")

    async def test_verifier_declares_all_seven_conflict_families(self) -> None:
        self.assertEqual(len(ConflictType), 7)
        self.assertEqual(set(SUPPORTED_CONFLICT_TYPES), {item.value for item in ConflictType})

    async def test_agent_uses_deterministic_fallback_after_model_failure(self) -> None:
        agent = MethodDesignAgent(AlwaysFailBackend())
        message = AgentMessage.create(
            message_type=MessageType.TASK_ASSIGN,
            sender="coordinator",
            receiver="method_agent",
            payload={"summary": "draft", "topic": "test"},
            conversation_id="conversation-test",
        )
        result = await agent.handle(message)
        self.assertTrue(result.sections)
        self.assertIn("simulated provider outage", agent.last_error or "")
        self.assertEqual(agent.last_retries, 2)

    async def test_openai_backend_reports_successful_retry_count(self) -> None:
        backend = OpenAICompatibleBackend(
            api_key="test",
            base_url="https://example.invalid/v1",
            model="test-model",
            max_retries=2,
        )
        attempts = 0

        def fake_request(_backend: OpenAICompatibleBackend, _system: str, _user: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary outage")
            return "ok"

        with (
            patch.object(OpenAICompatibleBackend, "_request", fake_request),
            patch("backends.asyncio.sleep", return_value=None),
        ):
            result = await backend.generate(system_prompt="system", user_prompt="user", fallback="fallback")

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 3)
        self.assertEqual(backend.last_retries, 2)

    async def test_editor_real_prompt_contains_sections_and_resolutions(self) -> None:
        backend = CapturingBackend()
        agent = EditorialAgent(backend)
        payload = {
            "snapshot": {
                "立项依据与研究现状": {"content": "LITERATURE_SENTINEL"},
                "研究内容与技术路线": {"content": "METHOD_SENTINEL"},
                "实验方案与预期成果": {"content": "EXPERIMENT_SENTINEL"},
            },
            "resolutions": [{"conflict_id": "C001", "decision": "RESOLUTION_SENTINEL"}],
        }

        result = await agent.draft(payload)

        self.assertIn("最终申请书", result.sections)
        self.assertEqual(len(backend.user_prompts), 1)
        prompt = backend.user_prompts[0]
        for marker in (
            "LITERATURE_SENTINEL",
            "METHOD_SENTINEL",
            "EXPERIMENT_SENTINEL",
            "RESOLUTION_SENTINEL",
        ):
            self.assertIn(marker, prompt)

    async def test_end_to_end_revision_recheck_and_finalization(self) -> None:
        coordinator = ResearchWritingCoordinator(
            backend=DeterministicBackend(), task_dir=self.runtime, console=False
        )
        summary = await coordinator.run()

        self.assertEqual(summary["conflicts_detected"], 3)
        self.assertEqual(summary["conflicts_resolved"], 3)
        self.assertTrue(summary["parallel_drafting_proved"])
        self.assertEqual(summary["model"], "mock-research-writer-v1")

        expected_files = [
            "outputs/final_proposal.md",
            "logs/messages.jsonl",
            "logs/run_log.json",
            "logs/blackboard_snapshot.json",
            "logs/conflicts_and_resolutions.json",
            "schemas/message_schema.json",
            "schemas/agent_output_schema.json",
        ]
        for relative in expected_files:
            self.assertTrue((self.runtime / relative).exists(), relative)

        proposal = (self.runtime / "outputs" / "final_proposal.md").read_text(encoding="utf-8")
        for chapter in range(1, 11):
            self.assertIn(f"## {chapter}.", proposal)
        self.assertIn("8 × A100", proposal)
        self.assertIn("960 GPU小时", proposal)
        self.assertIn("SLA违约率降低 **25%**", proposal)

        messages = [
            json.loads(line)
            for line in (self.runtime / "logs" / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        emitted = {message["message_type"] for message in messages}
        required_flow = {
            MessageType.TASK_ASSIGN.value,
            MessageType.RESULT_SUBMIT.value,
            MessageType.INFO_REQUEST.value,
            MessageType.CONFLICT_NOTICE.value,
            MessageType.REVISION_REQUEST.value,
            MessageType.REVISION_SUBMIT.value,
            MessageType.CONFLICT_RESOLUTION.value,
            MessageType.FINAL_RESULT.value,
            MessageType.ACK.value,
        }
        self.assertTrue(required_flow.issubset(emitted))
        for message in messages:
            self.assertTrue(set(AGENT_MESSAGE_SCHEMA["required"]).issubset(message))

        conflicts = json.loads(
            (self.runtime / "logs" / "conflicts_and_resolutions.json").read_text(encoding="utf-8")
        )
        self.assertEqual(conflicts["post_revision_conflicts"], [])
        self.assertTrue(all(item["status"] == "RESOLVED" for item in conflicts["resolved_conflicts"]))

        run_log = json.loads((self.runtime / "logs" / "run_log.json").read_text(encoding="utf-8"))
        self.assertTrue(run_log["parallel_drafting_proved"])
        self.assertTrue(run_log["agent_runs"])
        for event in run_log["agent_runs"]:
            required_fields = {
                "agent", "start_time", "end_time", "duration_ms", "model",
                "input_tokens", "output_tokens", "errors", "retries",
            }
            self.assertTrue(required_fields.issubset(event))


if __name__ == "__main__":
    unittest.main()
