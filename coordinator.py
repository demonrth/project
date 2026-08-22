"""Coordinator for the complete five-stage research-writing workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents import (
    DataLogicVerifierAgent,
    EditorialAgent,
    ExperimentPlanningAgent,
    LiteratureResearchAgent,
    MethodDesignAgent,
)
from agents.base import AgentResult, BaseAgent
from backends import TextBackend
from protocol import (
    AgentMessage,
    DocumentStore,
    MessageBus,
    MessageStatus,
    MessageType,
    Priority,
    WriteConflictError,
    estimate_text_tokens,
    utc_now,
    write_json_schemas,
)


DEFAULT_TOPIC = "基于多智能体强化学习的分布式计算资源调度方法研究"
# Backward-compatible name used by earlier report code.
TOPIC = DEFAULT_TOPIC
SECTION_NAMES = (
    "立项依据与研究现状",
    "研究内容与技术路线",
    "实验方案与预期成果",
)
OWNERSHIP = {
    "立项依据与研究现状": "literature_agent",
    "研究内容与技术路线": "method_agent",
    "实验方案与预期成果": "experiment_agent",
}
WORKSPACE_FILES = {
    "literature_agent": "literature.json",
    "method_agent": "method.json",
    "experiment_agent": "experiment.json",
    "verifier_agent": "verification.json",
    "editor_agent": "editor.json",
}


class ResearchWritingCoordinator:
    coordinator_id = "coordinator"

    def __init__(
        self,
        *,
        backend: TextBackend,
        task_dir: Path,
        console: bool = True,
        topic: str = DEFAULT_TOPIC,
        requirements_text: str = "",
        requirements_source: str = "",
    ) -> None:
        self.backend = backend
        self.console = console
        self.task_dir = task_dir
        self.topic = topic.strip() or DEFAULT_TOPIC
        self.requirements_text = requirements_text.strip()
        self.requirements_source = requirements_source.strip()
        self.logs_dir = task_dir / "logs"
        self.outputs_dir = task_dir / "outputs"
        self.figures_dir = task_dir / "figures"
        self.workspace_dir = task_dir / "workspace"
        self.inputs_dir = task_dir / "inputs"
        for directory in (
            self.logs_dir,
            self.outputs_dir,
            self.figures_dir,
            self.workspace_dir,
            self.inputs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if self.requirements_text:
            (self.inputs_dir / "imported_requirements.txt").write_text(
                self.requirements_text, encoding="utf-8"
            )
            self._write_json(
                self.inputs_dir / "requirements_meta.json",
                {
                    "source": self.requirements_source or "GUI manual input",
                    "topic": self.topic,
                    "characters": len(self.requirements_text),
                    "sha256": hashlib.sha256(
                        self.requirements_text.encode("utf-8")
                    ).hexdigest(),
                },
            )

        self.bus = MessageBus(self.logs_dir / "messages.jsonl", console=console)
        self.store = DocumentStore(SECTION_NAMES, ownership=OWNERSHIP)
        self.agents: dict[str, BaseAgent] = {
            LiteratureResearchAgent.agent_id: LiteratureResearchAgent(backend),
            MethodDesignAgent.agent_id: MethodDesignAgent(backend),
            ExperimentPlanningAgent.agent_id: ExperimentPlanningAgent(backend),
            DataLogicVerifierAgent.agent_id: DataLogicVerifierAgent(backend),
            EditorialAgent.agent_id: EditorialAgent(backend),
        }
        self.conversation_id = f"task-{int(time.time())}"
        self.resolutions: list[dict[str, Any]] = []
        self.negotiation: list[dict[str, Any]] = []
        self.detected_conflicts: list[dict[str, Any]] = []
        self.agent_runs: list[dict[str, Any]] = []
        self.stage_timings_ms: dict[str, float] = {}
        self.errors: list[dict[str, Any]] = []
        self.write_conflicts: list[dict[str, Any]] = []
        self.workspace_cache: dict[str, dict[str, Any]] = {}
        self.task_plan = self._build_task_plan()

    async def run(self) -> dict[str, Any]:
        overall_start = time.perf_counter()
        overall_started_at = utc_now()
        write_json_schemas(self.task_dir)
        self._write_json(self.logs_dir / "task_plan.json", self.task_plan)

        self._announce("\n[1] Coordinator拆分任务")
        await self._timed("Stage 1-2 任务分解与并行起草", self._parallel_drafting())
        self._announce("\n[3-4] 三个Agent提交结果；VerificationAgent交叉审查")
        verifier_result = await self._timed("Stage 3 交叉审查", self._cross_review())
        self._announce("\n[5-6] Coordinator组织协商；Agent增量修订")
        postcheck = await self._timed("Stage 4 冲突解决与复核", self._resolve_conflicts(verifier_result))
        if not postcheck.data.get("passed"):
            raise RuntimeError("Post-revision verification did not pass")
        self._announce("\n[8] EditorAgent最终统稿")
        final_result = await self._timed("Stage 5 最终统稿", self._final_edit())

        proposal = final_result.sections["最终申请书"]
        final_path = self.outputs_dir / "final_proposal.md"
        final_path.write_text(proposal, encoding="utf-8")
        # Original-PDF compatibility path.
        (self.outputs_dir / "proposal.md").write_text(proposal, encoding="utf-8")

        snapshot = await self.store.snapshot()
        self._write_json(self.logs_dir / "document_snapshot.json", snapshot)
        self._write_json(
            self.logs_dir / "conflicts_and_resolutions.json",
            {
                "detected_conflicts": self.detected_conflicts,
                "negotiation": self.negotiation,
                "resolutions": self.resolutions,
                "resolved_conflicts": self.resolutions,
                "post_revision_conflicts": postcheck.data.get("conflicts", []),
                "post_revision_passed": True,
            },
        )
        blackboard = {
            "task": self.task_plan,
            "sections": snapshot,
            "agent_results": self.workspace_cache,
            "conflicts": self.detected_conflicts,
            "resolved_conflicts": self.resolutions,
            "messages": [message.to_dict() for message in self.bus.messages],
        }
        self._write_json(self.logs_dir / "blackboard_snapshot.json", blackboard)

        # Compatibility log path used by the first report version.
        message_text = self.bus.log_path.read_text(encoding="utf-8")
        (self.logs_dir / "run_log.jsonl").write_text(message_text, encoding="utf-8")

        summary = self.bus.communication_summary()
        summary.update(
            {
                "topic": self.topic,
                "requirements_imported": bool(self.requirements_text),
                "requirements_source": self.requirements_source,
                "requirements_characters": len(self.requirements_text),
                "backend": self.backend.name,
                "model": getattr(self.backend, "model", self.backend.name),
                "topology": "central coordinator + versioned blackboard",
                "leaf_to_leaf_hops": 2,
                "stage_timings_ms": {key: round(value, 2) for key, value in self.stage_timings_ms.items()},
                "total_runtime_ms": round((time.perf_counter() - overall_start) * 1000, 2),
                "conflicts_detected": len(self.detected_conflicts),
                "conflicts_resolved": len(self.resolutions),
                "document_versions": {name: item["version"] for name, item in snapshot.items()},
                "final_proposal": str(final_path.relative_to(self.task_dir)),
                "parallel_drafting_proved": self._parallel_overlap_proved(),
            }
        )
        self._write_json(self.logs_dir / "run_summary.json", summary)
        run_log = {
            "run_id": self.conversation_id,
            "topic": self.topic,
            "start_time": overall_started_at,
            "end_time": utc_now(),
            "duration_ms": summary["total_runtime_ms"],
            "model": summary["model"],
            "agent_runs": self.agent_runs,
            "stage_timings_ms": summary["stage_timings_ms"],
            "errors": self.errors,
            "retries": sum(item["retries"] for item in self.agent_runs),
            "write_conflicts": self.write_conflicts,
            "conflicts": self.detected_conflicts,
            "resolved_conflicts": self.resolutions,
            "parallel_drafting_proved": summary["parallel_drafting_proved"],
        }
        self._write_json(self.logs_dir / "run_log.json", run_log)
        self._announce("\n[9-10] 已输出基金申请书；已保存完整日志")
        return summary

    def _build_task_plan(self) -> dict[str, Any]:
        chapters = [
            "立项依据", "国内外研究现状", "研究目标", "研究内容", "关键科学问题",
            "研究方法", "技术路线", "实验方案", "评价指标", "预期成果",
        ]
        return {
            "topic": self.topic,
            "requirements": {
                "imported": bool(self.requirements_text),
                "source": self.requirements_source,
                "characters": len(self.requirements_text),
            },
            "topology": "central coordinator + blackboard",
            "chapters": [{"index": index, "title": title} for index, title in enumerate(chapters, 1)],
            "assignments": {
                "literature_agent": chapters[:2],
                "method_agent": chapters[2:7],
                "experiment_agent": chapters[7:],
                "verifier_agent": ["七类冲突检测", "公式/引用/逻辑复核"],
                "editor_agent": ["统一术语", "格式", "最终Markdown"],
            },
        }

    async def _parallel_drafting(self) -> None:
        assignments = [
            (self.agents["literature_agent"], ["立项依据", "国内外研究现状"]),
            (self.agents["method_agent"], ["研究目标", "研究内容", "关键科学问题", "研究方法", "技术路线"]),
            (self.agents["experiment_agent"], ["实验方案", "评价指标", "预期成果"]),
        ]
        self._announce("[2] Literature / Method / Experiment并行工作")
        results = await asyncio.gather(
            *(
                self._dispatch(
                    agent,
                    MessageType.TASK_ASSIGN,
                    {"summary": f"并行起草：{'、'.join(chapters)}", "action": "draft", "topic": self.topic, "sections": chapters},
                    result_type=MessageType.RESULT_SUBMIT,
                )
                for agent, chapters in assignments
            )
        )
        for result in results:
            for section_name, content in result.sections.items():
                await self._commit_section(
                    section_name,
                    base_version=0,
                    content=content,
                    writer=OWNERSHIP[section_name],
                )

    async def _cross_review(self) -> AgentResult:
        result = await self._dispatch(
            self.agents["verifier_agent"],
            MessageType.TASK_ASSIGN,
            {
                "summary": "核查七类冲突、公式、引用和统计设计",
                "action": "draft",
                "phase": "initial",
                "snapshot": await self.store.snapshot(),
            },
            priority=Priority.HIGH,
            result_type=MessageType.RESULT_SUBMIT,
        )
        self.detected_conflicts = result.data["conflicts"]
        for conflict in self.detected_conflicts:
            notification = AgentMessage.create(
                message_type=MessageType.CONFLICT_NOTICE,
                sender="verifier_agent",
                receiver=self.coordinator_id,
                conversation_id=self.conversation_id,
                priority=Priority(conflict["severity"]),
                payload={"summary": f"{conflict['conflict_id']} {conflict['type']}", **conflict},
                requires_ack=True,
            )
            await self.bus.send(notification)
            await self._acknowledge(notification, sender=self.coordinator_id, receiver="verifier_agent")
        return result

    async def _resolve_conflicts(self, verifier_result: AgentResult) -> AgentResult:
        conflicts = verifier_result.data["conflicts"]
        for conflict in conflicts:
            query_results = await asyncio.gather(
                *(
                    self._dispatch(
                        self.agents[participant],
                        MessageType.INFO_REQUEST,
                        {
                            "summary": f"就{conflict['conflict_id']}提交协商意见",
                            "topic": self.topic,
                            "question": conflict["suggestion"],
                            "conflict_id": conflict["conflict_id"],
                            "evidence": conflict["evidence"],
                        },
                        priority=Priority.HIGH,
                        result_type=MessageType.RESULT_SUBMIT,
                    )
                    for participant in conflict["agents"]
                )
            )
            opinions = [item.data["position"] for item in query_results]
            decision = await self._arbitrate(conflict, opinions)
            self.negotiation.append({"conflict": conflict, "opinions": opinions, "decision": decision})

        decision_by_id = {item["conflict"]["conflict_id"]: item["decision"] for item in self.negotiation}
        section_by_agent = {owner: section for section, owner in OWNERSHIP.items()}
        revision_agent_ids = list(
            dict.fromkeys(
                participant
                for conflict in conflicts
                for participant in conflict["agents"]
                if participant in section_by_agent
            )
        )
        revision_payloads: list[tuple[BaseAgent, str, int, dict[str, Any]]] = []
        for agent_id in revision_agent_ids:
            section_name = section_by_agent[agent_id]
            current = await self.store.read(section_name)
            relevant = [conflict for conflict in conflicts if agent_id in conflict["agents"]]
            relevant_ids = [conflict["conflict_id"] for conflict in relevant]
            resolution = "；".join(decision_by_id[item] for item in relevant_ids)
            revision_payloads.append(
                (
                    self.agents[agent_id],
                    section_name,
                    current.version,
                    {
                        "summary": "按冲突决议增量修订负责章节",
                        "action": "revise",
                        "topic": self.topic,
                        "resolution": resolution,
                        "conflict_ids": relevant_ids,
                        "conflict_details": relevant,
                        "current_content": current.content,
                        "base_version": current.version,
                    },
                )
            )
        results = await asyncio.gather(
            *(
                self._dispatch(
                    agent,
                    MessageType.REVISION_REQUEST,
                    payload,
                    priority=Priority.HIGH,
                    result_type=MessageType.REVISION_SUBMIT,
                )
                for agent, _section, _version, payload in revision_payloads
            )
        )
        revision_versions = {
            section: version for _agent, section, version, _payload in revision_payloads
        }
        for result in results:
            for section_name, content in result.sections.items():
                await self._commit_section(
                    section_name,
                    base_version=revision_versions[section_name],
                    content=content,
                    writer=OWNERSHIP[section_name],
                )

        self._announce("[7] VerificationAgent再次检查并关闭冲突")
        postcheck = await self._dispatch(
            self.agents["verifier_agent"],
            MessageType.TASK_ASSIGN,
            {
                "summary": "复核修订稿并关闭已解决冲突",
                "action": "draft",
                "phase": "post_revision",
                "snapshot": await self.store.snapshot(),
                "original_conflict_ids": [item["conflict_id"] for item in conflicts],
            },
            priority=Priority.CRITICAL,
            result_type=MessageType.CONFLICT_RESOLUTION,
            result_status=MessageStatus.RESOLVED,
        )
        if postcheck.data.get("passed"):
            for conflict in conflicts:
                resolved = {
                    **conflict,
                    "status": "RESOLVED",
                    "decision": decision_by_id[conflict["conflict_id"]],
                    "verified_by": "verifier_agent",
                    "verified_at": utc_now(),
                }
                self.resolutions.append(resolved)
        return postcheck

    async def _final_edit(self) -> AgentResult:
        return await self._dispatch(
            self.agents["editor_agent"],
            MessageType.TASK_ASSIGN,
            {
                "summary": "合并已复核章节，统一术语与格式，生成最终Markdown",
                "action": "draft",
                "topic": self.topic,
                "snapshot": await self.store.snapshot(),
                "resolutions": self.resolutions,
            },
            priority=Priority.HIGH,
            result_type=MessageType.FINAL_RESULT,
            result_status=MessageStatus.COMPLETED,
        )

    async def _dispatch(
        self,
        agent: BaseAgent,
        message_type: MessageType,
        payload: dict[str, Any],
        *,
        priority: Priority = Priority.NORMAL,
        result_type: MessageType = MessageType.RESULT_SUBMIT,
        result_status: MessageStatus = MessageStatus.COMPLETED,
    ) -> AgentResult:
        # Every working message carries the same authoritative project context.
        # The imported brief is data for proposal writing, not an instruction to
        # alter the communication protocol or expose runtime credentials.
        payload = {**payload, **self._project_context()}
        outbound = AgentMessage.create(
            message_type=message_type,
            sender=self.coordinator_id,
            receiver=agent.agent_id,
            conversation_id=self.conversation_id,
            priority=priority,
            payload=payload,
            requires_ack=True,
        )
        await self.bus.send(outbound)
        await self._acknowledge(outbound, sender=agent.agent_id, receiver=self.coordinator_id)

        start_time = utc_now()
        started = time.perf_counter()
        result: AgentResult | None = None
        execution_error: str | None = None
        coordinator_retries = 0
        for attempt in range(2):
            try:
                result = await agent.handle(outbound)
                break
            except Exception as exc:
                execution_error = f"{type(exc).__name__}: {exc}"
                coordinator_retries = attempt + 1
                if attempt == 0:
                    await asyncio.sleep(0.05)
        if result is None:
            result = AgentResult(
                summary=f"{agent.display_name}失败，协调器保留错误并继续运行",
                data={"error": execution_error, "degraded": True},
            )
            self.errors.append({"agent": agent.agent_id, "error": execution_error, "time": utc_now()})

        end_time = utc_now()
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        backend_retries = int(getattr(agent, "last_retries", 0))
        fallback_error = getattr(agent, "last_error", None)
        if fallback_error:
            self.errors.append({"agent": agent.agent_id, "error": fallback_error, "time": end_time, "fallback_used": True})
        self.agent_runs.append(
            {
                "agent": agent.agent_id,
                "message_type": message_type.value,
                "start_time": start_time,
                "end_time": end_time,
                "duration_ms": duration_ms,
                "model": getattr(self.backend, "model", self.backend.name),
                "input_tokens": outbound.token_count,
                "output_tokens": estimate_text_tokens(asdict(result)),
                "error": execution_error or fallback_error,
                "errors": [item for item in (execution_error, fallback_error) if item],
                "retries": coordinator_retries + backend_retries,
            }
        )
        self._persist_workspace(agent.agent_id, result)

        submission = AgentMessage.create(
            message_type=result_type,
            sender=agent.agent_id,
            receiver=self.coordinator_id,
            conversation_id=self.conversation_id,
            parent_message_id=outbound.message_id,
            priority=priority,
            status=result_status,
            payload={
                "summary": result.summary,
                "sections": list(result.sections),
                "data": _compact_data(result.data),
            },
            requires_ack=True,
        )
        await self.bus.send(submission)
        await self._acknowledge(submission, sender=self.coordinator_id, receiver=agent.agent_id)
        return result

    async def _acknowledge(self, original: AgentMessage, *, sender: str, receiver: str) -> None:
        ack = AgentMessage.create(
            message_type=MessageType.ACK,
            sender=sender,
            receiver=receiver,
            conversation_id=original.conversation_id,
            parent_message_id=original.message_id,
            priority=Priority.NORMAL,
            status=MessageStatus.ACCEPTED,
            payload={"summary": f"已接收{original.message_id}", "accepted_type": original.message_type},
            requires_ack=False,
        )
        await self.bus.send(ack)

    async def _commit_section(self, section_name: str, *, base_version: int, content: str, writer: str) -> None:
        try:
            await self.store.compare_and_swap(
                section_name,
                expected_version=base_version,
                new_content=content,
                writer=writer,
            )
        except WriteConflictError as exc:
            latest = await self.store.read(section_name)
            event = {
                "section": section_name,
                "writer": writer,
                "base_version": base_version,
                "latest_version": latest.version,
                "error": str(exc),
                "action": "REJECT → FETCH_LATEST → RETRY",
            }
            self.write_conflicts.append(event)
            await self.store.compare_and_swap(
                section_name,
                expected_version=latest.version,
                new_content=content,
                writer=writer,
            )

    def _persist_workspace(self, agent_id: str, result: AgentResult) -> None:
        payload = {"agent_id": agent_id, **asdict(result), "saved_at": utc_now()}
        self.workspace_cache[agent_id] = payload
        filename = WORKSPACE_FILES[agent_id]
        self._write_json(self.workspace_dir / filename, payload)

    async def _timed(self, label: str, awaitable: Any) -> Any:
        started = time.perf_counter()
        result = await awaitable
        self.stage_timings_ms[label] = (time.perf_counter() - started) * 1000
        return result

    def _parallel_overlap_proved(self) -> bool:
        draft_ids = {"literature_agent", "method_agent", "experiment_agent"}
        draft_runs = [item for item in self.agent_runs if item["agent"] in draft_ids and item["message_type"] == MessageType.TASK_ASSIGN.value]
        if len(draft_runs) != 3:
            return False
        starts = [item["start_time"] for item in draft_runs]
        ends = [item["end_time"] for item in draft_runs]
        return max(starts) < min(ends)

    async def _arbitrate(self, conflict: dict[str, Any], opinions: list[str]) -> str:
        conflict_id = conflict["conflict_id"]
        if conflict_id in {"C001", "C006"}:
            fallback = "统一采用当前可用的8 × A100，总训练预算上限为960 GPU小时"
        elif conflict_id == "C002":
            fallback = "SLA违约率降低25%为主要验收目标，降低35%为扩展目标"
        else:
            fallback = conflict["suggestion"]
        return await self.backend.generate(
            system_prompt="你是多智能体科研写作系统的协调与仲裁智能体。",
            user_prompt=(
                f"项目主题：{self.topic}\n"
                f"{self._requirements_prompt()}"
                f"冲突：{conflict['description']}\n"
                f"证据：{conflict['evidence']}\n核查建议：{conflict['suggestion']}\n"
                f"参与Agent意见：{opinions}\n"
                "请给出150字以内的唯一、可执行决议，指明要修改的内容和验证标准。"
            ),
            fallback=fallback,
        )

    def _project_context(self) -> dict[str, Any]:
        context: dict[str, Any] = {"topic": self.topic}
        if self.requirements_text:
            context["project_requirements"] = self.requirements_text
            context["requirements_source"] = self.requirements_source
        return context

    def _requirements_prompt(self) -> str:
        if not self.requirements_text:
            return ""
        return (
            "【导入的项目要求】\n"
            f"{self.requirements_text}\n"
            "【要求结束】\n"
        )

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _announce(self, message: str) -> None:
        if self.console:
            print(message)


def _compact_data(data: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in data.items():
        if key == "conflicts" and isinstance(value, list):
            compact[key] = [
                {"conflict_id": item.get("conflict_id"), "type": item.get("type"), "severity": item.get("severity")}
                for item in value
            ]
        elif isinstance(value, str) and len(value) > 180:
            compact[key] = value[:177] + "..."
        else:
            compact[key] = value
    return compact
