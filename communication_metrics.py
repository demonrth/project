"""Communication cost metrics computed from the real message JSONL log."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_messages(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def compute_metrics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    sent = Counter(message["sender"] for message in messages)
    received = Counter(message["receiver"] for message in messages)
    count_by_type = Counter(message["message_type"] for message in messages)
    tokens_by_type: defaultdict[str, int] = defaultdict(int)
    tokens_by_agent: defaultdict[str, int] = defaultdict(int)
    for message in messages:
        tokens = int(message["token_count"])
        tokens_by_type[message["message_type"]] += tokens
        tokens_by_agent[message["sender"]] += tokens
    total = sum(int(message["token_count"]) for message in messages)
    count = len(messages)
    return {
        "formula": "C = Σ token_count = N × L_avg",
        "message_count": count,
        "total_tokens": total,
        "average_message_tokens": round(total / count, 2) if count else 0,
        "sent_by_agent": dict(sorted(sent.items())),
        "received_by_agent": dict(sorted(received.items())),
        "tokens_by_agent": dict(sorted(tokens_by_agent.items())),
        "message_count_by_type": dict(sorted(count_by_type.items())),
        "tokens_by_type": dict(sorted(tokens_by_type.items())),
        "optimizations": [
            "摘要通信：默认只发送summary与结构化字段",
            "增量传输：修订消息只携带冲突ID、版本号和修改摘要",
            "幂等去重：重复message_id不重复计费或写入",
        ],
    }


def write_metrics(messages_path: Path, output_path: Path) -> dict[str, Any]:
    metrics = compute_metrics(load_messages(messages_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = write_metrics(root / "logs" / "messages.jsonl", root / "logs" / "communication_metrics.json")
    print(json.dumps(result, ensure_ascii=False, indent=2))
