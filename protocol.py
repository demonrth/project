"""Formal protocol, star message bus, and versioned blackboard.

All agent-to-agent information is wrapped in :class:`AgentMessage`. The
protocol exposes both the expanded specification names (``conversation_id`` /
``parent_message_id``) and the original-report aliases (``correlation_id`` /
``related_message_id``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


class MessageType(str, Enum):
    TASK_ASSIGN = "TASK_ASSIGN"
    INFO_REQUEST = "INFO_REQUEST"
    RESULT_SUBMIT = "RESULT_SUBMIT"
    CONFLICT_NOTICE = "CONFLICT_NOTICE"
    ACK = "ACK"
    CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"
    REVISION_REQUEST = "REVISION_REQUEST"
    REVISION_SUBMIT = "REVISION_SUBMIT"
    FINAL_RESULT = "FINAL_RESULT"
    ERROR = "ERROR"

    # Backward-compatible symbolic names.
    TASK_ASSIGNMENT = "TASK_ASSIGN"
    INFORMATION_QUERY = "INFO_REQUEST"
    RESULT_SUBMISSION = "RESULT_SUBMIT"
    CONFLICT_NOTIFICATION = "CONFLICT_NOTICE"
    ACKNOWLEDGEMENT = "ACK"


class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MessageStatus(str, Enum):
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RESOLVED = "RESOLVED"


AGENT_MESSAGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.org/schemas/research-agent-message.schema.json",
    "title": "Research Collaboration Agent Message",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "message_id", "conversation_id", "parent_message_id",
        "correlation_id", "related_message_id", "message_type", "sender",
        "receiver", "timestamp", "priority", "payload", "summary", "status",
        "token_count", "payload_digest", "requires_ack",
    ],
    "properties": {
        "schema_version": {"const": "2.0"},
        "message_id": {"type": "string", "pattern": "^msg-[0-9a-f]{12}$"},
        "conversation_id": {"type": "string", "minLength": 1},
        "parent_message_id": {"type": ["string", "null"], "pattern": "^msg-[0-9a-f]{12}$"},
        "correlation_id": {"type": "string", "minLength": 1},
        "related_message_id": {"type": ["string", "null"], "pattern": "^msg-[0-9a-f]{12}$"},
        "message_type": {"enum": [member.value for member in MessageType]},
        "sender": {"type": "string", "minLength": 1},
        "receiver": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "format": "date-time"},
        "priority": {"enum": [member.value for member in Priority]},
        "payload": {"type": "object", "additionalProperties": True},
        "summary": {"type": "string", "minLength": 1},
        "status": {"enum": [member.value for member in MessageStatus]},
        "token_count": {"type": "integer", "minimum": 1},
        "payload_digest": {"type": "string", "pattern": "^[0-9a-f]{16}$"},
        "requires_ack": {"type": "boolean"},
    },
}


AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.org/schemas/research-agent-output.schema.json",
    "title": "Research Agent Structured Output",
    "type": "object",
    "additionalProperties": False,
    "required": ["agent_id", "summary", "sections", "data"],
    "properties": {
        "agent_id": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "sections": {"type": "object", "additionalProperties": {"type": "string"}},
        "data": {"type": "object", "additionalProperties": True},
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()[:16]


def estimate_text_tokens(value: Any) -> int:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 4))


@dataclass(slots=True)
class AgentMessage:
    schema_version: str
    message_id: str
    conversation_id: str
    parent_message_id: str | None
    correlation_id: str
    related_message_id: str | None
    message_type: str
    sender: str
    receiver: str
    timestamp: str
    priority: str
    payload: dict[str, Any]
    summary: str
    status: str
    token_count: int
    payload_digest: str
    requires_ack: bool

    @classmethod
    def create(
        cls,
        *,
        message_type: MessageType,
        sender: str,
        receiver: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        correlation_id: str | None = None,
        priority: Priority = Priority.NORMAL,
        parent_message_id: str | None = None,
        related_message_id: str | None = None,
        status: MessageStatus = MessageStatus.SENT,
        summary: str | None = None,
        requires_ack: bool = True,
    ) -> "AgentMessage":
        conversation = conversation_id or correlation_id
        if not conversation:
            raise ValueError("conversation_id is required")
        parent = parent_message_id if parent_message_id is not None else related_message_id
        readable_summary = summary or str(payload.get("summary", "")).strip()
        if not readable_summary:
            raise ValueError("summary is required either explicitly or in payload.summary")
        partial: dict[str, Any] = {
            "schema_version": "2.0",
            "message_id": f"msg-{uuid.uuid4().hex[:12]}",
            "conversation_id": conversation,
            "parent_message_id": parent,
            "correlation_id": conversation,
            "related_message_id": parent,
            "message_type": message_type.value,
            "sender": sender,
            "receiver": receiver,
            "timestamp": utc_now(),
            "priority": priority.value,
            "payload": payload,
            "summary": readable_summary,
            "status": status.value,
            "payload_digest": _payload_digest(payload),
            "requires_ack": requires_ack,
        }
        partial["token_count"] = estimate_text_tokens(partial)
        message = cls(**partial)
        message.validate()
        return message

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentMessage":
        message = cls(**value)
        message.validate()
        return message

    def validate(self) -> None:
        if self.schema_version != "2.0":
            raise ValueError("Unsupported schema_version")
        if re.fullmatch(r"msg-[0-9a-f]{12}", self.message_id) is None:
            raise ValueError("message_id must match msg-[0-9a-f]{12}")
        if self.conversation_id != self.correlation_id:
            raise ValueError("conversation_id and correlation_id aliases must match")
        if self.parent_message_id != self.related_message_id:
            raise ValueError("parent_message_id and related_message_id aliases must match")
        if self.message_type not in {item.value for item in MessageType}:
            raise ValueError(f"Unknown message_type: {self.message_type}")
        if self.priority not in {item.value for item in Priority}:
            raise ValueError(f"Unknown priority: {self.priority}")
        if self.status not in {item.value for item in MessageStatus}:
            raise ValueError(f"Unknown message status: {self.status}")
        if not self.sender or not self.receiver or not self.conversation_id:
            raise ValueError("sender, receiver and conversation_id are required")
        if not isinstance(self.payload, dict) or not self.summary:
            raise ValueError("payload must be an object and summary must be non-empty")
        if self.payload_digest != _payload_digest(self.payload):
            raise ValueError("payload_digest does not match payload")
        if not isinstance(self.token_count, int) or self.token_count < 1:
            raise ValueError("token_count must be a positive integer")
        datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TopologyViolationError(RuntimeError):
    """Raised when two leaf agents attempt direct communication."""


class MessageBus:
    """Asynchronous star-topology bus with idempotency and JSONL logging."""

    def __init__(self, log_path: Path, coordinator_id: str = "coordinator", *, console: bool = True) -> None:
        self.log_path = log_path
        self.coordinator_id = coordinator_id
        self.console = console
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()
        self.messages: list[AgentMessage] = []
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def _check_topology(self, message: AgentMessage) -> None:
        if message.sender != self.coordinator_id and message.receiver != self.coordinator_id:
            raise TopologyViolationError("Star topology requires leaf-agent messages to pass through coordinator")

    async def send(self, message: AgentMessage) -> bool:
        message.validate()
        self._check_topology(message)
        async with self._lock:
            if message.message_id in self._seen:
                return False
            self._seen.add(message.message_id)
            self.messages.append(message)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            if self.console:
                stamp = message.timestamp[11:23]
                print(f"[{stamp}] {message.sender} → {message.receiver}\n{message.message_type} | {message.summary}")
        await asyncio.sleep(0)
        return True

    def communication_summary(self) -> dict[str, Any]:
        sent: Counter[str] = Counter()
        received: Counter[str] = Counter()
        by_type: Counter[str] = Counter()
        tokens_by_agent: defaultdict[str, int] = defaultdict(int)
        tokens_by_type: defaultdict[str, int] = defaultdict(int)
        for message in self.messages:
            sent[message.sender] += 1
            received[message.receiver] += 1
            by_type[message.message_type] += 1
            tokens_by_agent[message.sender] += message.token_count
            tokens_by_type[message.message_type] += message.token_count
        total_tokens = sum(item.token_count for item in self.messages)
        count = len(self.messages)
        return {
            "message_count": count,
            "average_message_tokens": round(total_tokens / count, 2) if count else 0,
            "estimated_total_tokens": total_tokens,
            "formula": "C_total = sum(T_i) = N_messages * mean(T_i)",
            "sent_by_agent": dict(sorted(sent.items())),
            "received_by_agent": dict(sorted(received.items())),
            "tokens_sent_by_agent": dict(sorted(tokens_by_agent.items())),
            "messages_by_type": dict(sorted(by_type.items())),
            "tokens_by_type": dict(sorted(tokens_by_type.items())),
        }


def estimate_tokens(message: AgentMessage | dict[str, Any]) -> int:
    if isinstance(message, AgentMessage):
        return message.token_count
    return int(message.get("token_count") or estimate_text_tokens(message))


@dataclass(slots=True)
class DocumentSection:
    name: str
    content: str = ""
    version: int = 0
    last_writer: str = ""
    updated_at: str = field(default_factory=utc_now)


class WriteConflictError(RuntimeError):
    pass


class DocumentStore:
    """Blackboard sections with ownership, per-section locks, and CAS writes."""

    def __init__(self, section_names: Iterable[str], ownership: dict[str, str] | None = None) -> None:
        self._sections = {name: DocumentSection(name=name) for name in section_names}
        self._locks = {name: asyncio.Lock() for name in section_names}
        self._ownership = ownership or {}

    async def read(self, section_name: str) -> DocumentSection:
        return DocumentSection(**asdict(self._sections[section_name]))

    async def compare_and_swap(self, section_name: str, *, expected_version: int, new_content: str, writer: str) -> DocumentSection:
        expected_owner = self._ownership.get(section_name)
        if expected_owner and expected_owner != writer:
            raise PermissionError(f"{writer} cannot write {section_name}; owner is {expected_owner}")
        async with self._locks[section_name]:
            current = self._sections[section_name]
            if current.version != expected_version:
                raise WriteConflictError(
                    f"WRITE_CONFLICT {section_name}: expected v{expected_version}, current v{current.version}"
                )
            updated = DocumentSection(
                name=section_name,
                content=new_content,
                version=current.version + 1,
                last_writer=writer,
                updated_at=utc_now(),
            )
            self._sections[section_name] = updated
            return DocumentSection(**asdict(updated))

    async def compare_and_swap_with_retry(
        self,
        section_name: str,
        *,
        base_version: int,
        writer: str,
        rewrite: Callable[[DocumentSection], str],
        max_retries: int = 2,
    ) -> tuple[DocumentSection, int]:
        """Reject stale work, fetch the latest version, rewrite, and retry."""

        version = base_version
        retries = 0
        while True:
            latest = await self.read(section_name)
            try:
                return (
                    await self.compare_and_swap(
                        section_name,
                        expected_version=version,
                        new_content=rewrite(latest),
                        writer=writer,
                    ),
                    retries,
                )
            except WriteConflictError:
                if retries >= max_retries:
                    raise
                retries += 1
                version = latest.version

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: asdict(await self.read(name)) for name in self._sections}


def write_json_schemas(task_dir: Path) -> None:
    schema_dir = task_dir / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    message_text = json.dumps(AGENT_MESSAGE_SCHEMA, ensure_ascii=False, indent=2)
    (schema_dir / "message_schema.json").write_text(message_text, encoding="utf-8")
    (task_dir / "protocol.schema.json").write_text(message_text, encoding="utf-8")
    (schema_dir / "agent_output_schema.json").write_text(
        json.dumps(AGENT_OUTPUT_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_json_schema(path: Path) -> None:
    """Backward-compatible single-schema exporter."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(AGENT_MESSAGE_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
