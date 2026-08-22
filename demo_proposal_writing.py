"""Run the complete five-stage collaborative proposal-writing demonstration."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backends import create_backend
from communication_metrics import write_metrics
from config import load_dotenv
from coordinator import DEFAULT_TOPIC, ResearchWritingCoordinator
from requirement_loader import infer_topic, load_requirement_file
from visualization import generate_all


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock",
        action="store_true",
        help="run the complete deterministic workflow without any API key",
    )
    mode.add_argument(
        "--real",
        action="store_true",
        help="use an OpenAI-compatible endpoint configured through .env or environment variables",
    )
    parser.add_argument(
        "--backend",
        choices=("mock", "auto", "openai"),
        default=None,
        help="backward-compatible mode selector; prefer --mock or --real",
    )
    parser.add_argument(
        "--task-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Task output root; defaults to the directory containing this script",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=None,
        help="project brief (.txt/.md/.json/.csv/.docx/.pdf) used by Real-mode agents",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="explicit project title; otherwise inferred from the imported brief",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> dict:
    task_dir = args.task_dir.resolve()
    load_dotenv(task_dir / ".env")
    backend_mode = "openai" if args.real else "mock" if args.mock else (args.backend or "mock")
    backend = create_backend(backend_mode)
    requirements_text = str(getattr(args, "requirements_text", "") or "").strip()
    requirements_source = str(getattr(args, "requirements_source", "") or "").strip()
    requirements_path = getattr(args, "requirements", None)
    if requirements_path and not requirements_text:
        source = Path(requirements_path).expanduser().resolve()
        requirements_text = load_requirement_file(source)
        requirements_source = str(source)
    explicit_topic = str(getattr(args, "topic", "") or "").strip()
    topic = explicit_topic or infer_topic(requirements_text, default=DEFAULT_TOPIC)
    coordinator = ResearchWritingCoordinator(
        backend=backend,
        task_dir=task_dir,
        topic=topic,
        requirements_text=requirements_text,
        requirements_source=requirements_source,
    )
    summary = await coordinator.run()
    metrics = write_metrics(
        task_dir / "logs" / "messages.jsonl",
        task_dir / "logs" / "communication_metrics.json",
    )
    print("[11] 已计算通信token开销")
    figures = generate_all(task_dir / "logs" / "messages.jsonl", task_dir / "figures")
    print("[12] 已根据真实日志生成三张可视化图")
    summary["figures"] = [str(path.relative_to(task_dir)) for path in figures]
    summary["communication_metrics"] = metrics
    (task_dir / "logs" / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    args = parse_args()
    summary = asyncio.run(async_main(args))
    print("\n=== Demo 完成 ===")
    print(
        f"消息 {summary['message_count']} 条 | token {summary['estimated_total_tokens']} | "
        f"冲突 {summary['conflicts_resolved']}/{summary['conflicts_detected']} 已解决"
    )
    print(f"最终申请书：{summary['final_proposal']}")


if __name__ == "__main__":
    main()
