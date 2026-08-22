"""Canonical visualization entry point required by the course specification."""

from visualize import generate_all, load_messages, plot_load, plot_message_types, plot_sequence

__all__ = ["generate_all", "load_messages", "plot_sequence", "plot_load", "plot_message_types"]


if __name__ == "__main__":
    from pathlib import Path

    task_root = Path(__file__).resolve().parent
    generate_all(task_root / "logs" / "messages.jsonl", task_root / "figures")
