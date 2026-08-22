"""Generate the three required communication visualizations from JSONL logs."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ACTORS = [
    "coordinator",
    "literature_agent",
    "method_agent",
    "experiment_agent",
    "verifier_agent",
    "editor_agent",
]

DISPLAY = {
    "coordinator": "Coordinator",
    "literature_agent": "Literature",
    "method_agent": "Method",
    "experiment_agent": "Experiment",
    "verifier_agent": "Verifier",
    "editor_agent": "Editor",
}

# Chinese labels used by the report charts. Keep DISPLAY in English so the
# sequence diagram stays compact and readable.
DISPLAY_CN = {
    "coordinator": "协调器",
    "literature_agent": "文献智能体",
    "method_agent": "方法智能体",
    "experiment_agent": "实验智能体",
    "verifier_agent": "核查智能体",
    "editor_agent": "编辑智能体",
}

TYPE_LABELS = {
    "TASK_ASSIGN": "任务分配",
    "INFO_REQUEST": "信息请求",
    "RESULT_SUBMIT": "结果提交",
    "CONFLICT_NOTICE": "冲突通知",
    "ACK": "确认应答",
    "REVISION_REQUEST": "修订请求",
    "REVISION_SUBMIT": "修订提交",
    "CONFLICT_RESOLUTION": "冲突解决",
    "FINAL_RESULT": "最终结果",
    "ERROR": "错误消息",
}

COLORS = {
    "TASK_ASSIGN": "#2563EB",
    "INFO_REQUEST": "#7C3AED",
    "RESULT_SUBMIT": "#059669",
    "CONFLICT_NOTICE": "#DC2626",
    "ACK": "#94A3B8",
    "REVISION_REQUEST": "#D97706",
    "REVISION_SUBMIT": "#0891B2",
    "CONFLICT_RESOLUTION": "#16A34A",
    "FINAL_RESULT": "#0F766E",
    "ERROR": "#991B1B",
}


def _configure_font() -> None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            prop = font_manager.FontProperties(fname=str(candidate))
            plt.rcParams["font.family"] = prop.get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False


def load_messages(log_path: Path) -> list[dict[str, Any]]:
    messages = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            messages.append(json.loads(line))
    return messages


def generate_all(log_path: Path, output_dir: Path) -> list[Path]:
    _configure_font()
    output_dir.mkdir(parents=True, exist_ok=True)
    messages = load_messages(log_path)
    paths = [
        output_dir / "sequence_diagram.png",
        output_dir / "communication_load.png",
        output_dir / "message_type_distribution.png",
    ]
    plot_sequence(messages, paths[0])
    plot_load(messages, paths[1])
    plot_message_types(messages, paths[2])
    return paths


def plot_sequence(messages: list[dict[str, Any]], output_path: Path) -> None:
    """Draw a PlantUML-inspired UML communication sequence diagram.

    The complete trace is split into four readable panels so a run with dozens
    of messages remains suitable for reports and presentation slides.
    """
    panels = 4
    per_panel = max(1, math.ceil(len(messages) / panels))
    fig, axes = plt.subplots(2, 2, figsize=(16, 10.2))
    fig.patch.set_facecolor("#FFFDF7")

    x_positions = {actor: index for index, actor in enumerate(ACTORS)}

    phase_names = [
        "并行起草",
        "交叉核查",
        "修订处理",
        "复核与最终统稿",
    ]

    short_type = {
        "TASK_ASSIGN": "任务分配",
        "INFO_REQUEST": "信息请求",
        "RESULT_SUBMIT": "结果提交",
        "CONFLICT_NOTICE": "冲突通知",
        "ACK": "ACK",
        "REVISION_REQUEST": "修订请求",
        "REVISION_SUBMIT": "修订提交",
        "CONFLICT_RESOLUTION": "冲突解决",
        "FINAL_RESULT": "最终结果",
        "ERROR": "错误",
    }

    for panel_index, ax in enumerate(axes.flat):
        ax.set_facecolor("#FFFDF7")

        start = panel_index * per_panel
        subset = messages[start : start + per_panel]
        if not subset:
            ax.axis("off")
            continue

        rows = len(subset)

        # PlantUML-like participant boxes and dashed lifelines.
        for actor, x in x_positions.items():
            participant = FancyBboxPatch(
                (x - 0.36, rows + 0.58),
                0.72,
                0.46,
                boxstyle="round,pad=0.02,rounding_size=0.04",
                linewidth=1.0,
                edgecolor="#8B6F47",
                facecolor="#FFF3C4",
                zorder=4,
            )
            ax.add_patch(participant)

            ax.text(
                x,
                rows + 0.81,
                DISPLAY_CN[actor],
                ha="center",
                va="center",
                fontsize=6.8,
                fontweight="bold",
                color="#3F3426",
                zorder=5,
            )

            ax.plot(
                [x, x],
                [0.20, rows + 0.57],
                linestyle=(0, (3, 3)),
                color="#9A9A9A",
                linewidth=0.82,
                zorder=1,
            )

        for local_index, message in enumerate(subset):
            global_index = start + local_index + 1
            y = rows - local_index - 0.18

            sender_x = x_positions[message["sender"]]
            receiver_x = x_positions[message["receiver"]]
            message_type = message["message_type"]

            color = COLORS.get(message_type, "#374151")

            # ACK / submit-type messages use dashed return arrows.
            is_return = message_type in {
                "ACK",
                "RESULT_SUBMIT",
                "REVISION_SUBMIT",
                "FINAL_RESULT",
            }
            line_style = "--" if is_return else "-"

            arrow = FancyArrowPatch(
                (sender_x, y),
                (receiver_x, y),
                arrowstyle="-|>",
                mutation_scale=8.5,
                linewidth=1.2,
                linestyle=line_style,
                color=color,
                shrinkA=3,
                shrinkB=3,
                zorder=3,
            )
            ax.add_patch(arrow)

            midpoint = (sender_x + receiver_x) / 2
            label = f"{global_index:02d}  {short_type.get(message_type, message_type)}"
            ax.text(
                midpoint,
                y + 0.13,
                label,
                ha="center",
                va="bottom",
                fontsize=5.7,
                color=color,
                fontweight="bold" if message_type != "ACK" else "normal",
                bbox={
                    "boxstyle": "round,pad=0.08",
                    "facecolor": "#FFFDF7",
                    "edgecolor": "none",
                    "alpha": 0.94,
                },
                zorder=5,
            )

        ax.set_xlim(-0.48, len(ACTORS) - 0.52)
        ax.set_ylim(0, rows + 1.25)

        ax.set_title(
            f"{phase_names[panel_index]}  |  #{start + 1:02d}–{start + rows:02d}",
            loc="left",
            fontsize=10.5,
            fontweight="bold",
            color="#5B4636",
            pad=10,
        )

        ax.axis("off")

    fig.suptitle(
        f"多智能体通信 UML 时序图 — 共 {len(messages)} 条消息",
        fontsize=17,
        fontweight="bold",
        color="#3F3426",
        y=0.995,
    )

    fig.tight_layout(rect=(0, 0, 1, 0.968), h_pad=1.6, w_pad=1.2)
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="#FFFDF7")
    plt.close(fig)


def plot_load(messages: list[dict[str, Any]], output_path: Path) -> None:
    """Draw an ECharts-inspired grouped bar chart with real log statistics."""
    sent = Counter(message["sender"] for message in messages)
    received = Counter(message["receiver"] for message in messages)

    labels = [DISPLAY_CN[actor] for actor in ACTORS]
    sent_values = [sent[actor] for actor in ACTORS]
    received_values = [received[actor] for actor in ACTORS]

    x = list(range(len(ACTORS)))
    width = 0.32
    max_value = max(sent_values + received_values + [1])
    background_height = max_value * 1.12

    fig, ax = plt.subplots(figsize=(12.8, 6.8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # ECharts showBackground-like subtle background columns.
    bg_color = "#EEF2F7"
    ax.bar(
        [item - width / 2 for item in x],
        [background_height] * len(x),
        width,
        color=bg_color,
        edgecolor="none",
        zorder=0,
    )
    ax.bar(
        [item + width / 2 for item in x],
        [background_height] * len(x),
        width,
        color=bg_color,
        edgecolor="none",
        zorder=0,
    )

    sent_bars = ax.bar(
        [item - width / 2 for item in x],
        sent_values,
        width,
        label="发送消息",
        color="#5470C6",
        edgecolor="none",
        zorder=3,
    )
    received_bars = ax.bar(
        [item + width / 2 for item in x],
        received_values,
        width,
        label="接收消息",
        color="#91CC75",
        edgecolor="none",
        zorder=3,
    )

    ax.bar_label(sent_bars, padding=4, fontsize=10, color="#334155")
    ax.bar_label(received_bars, padding=4, fontsize=10, color="#334155")

    ax.set_xticks(x, labels)
    ax.tick_params(axis="x", labelsize=10, pad=8)
    ax.tick_params(axis="y", labelsize=9, colors="#64748B")
    ax.set_ylabel("消息数量", fontsize=11, color="#475569", labelpad=10)
    ax.set_title("各智能体通信负载", fontsize=18, fontweight="bold", pad=20, color="#0F172A")
    ax.set_ylim(0, background_height)

    ax.grid(axis="y", color="#E2E8F0", linewidth=0.8, alpha=0.9, zorder=1)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#CBD5E1")
    ax.tick_params(axis="y", length=0)

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=2,
        frameon=False,
        fontsize=10,
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_message_types(messages: list[dict[str, Any]], output_path: Path) -> None:
    """Draw an ECharts-inspired doughnut chart with Chinese legend labels."""
    counts = Counter(message["message_type"] for message in messages)
    ordered = [item for item in TYPE_LABELS if counts[item]]
    values = [counts[item] for item in ordered]
    labels = [TYPE_LABELS[item] for item in ordered]
    colors = [COLORS[item] for item in ordered]
    total = sum(values)

    fig, ax = plt.subplots(figsize=(10.8, 7.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # ECharts-like doughnut: radius ~= 40%–70%, small gaps, clean center.
    wedges, _ = ax.pie(
        values,
        labels=None,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={
            "width": 0.30,
            "linewidth": 4.0,
            "edgecolor": "white",
        },
    )

    # Static PNG has no hover state, so the center provides a concise summary.
    ax.text(
        0,
        0.08,
        "消息总数",
        ha="center",
        va="center",
        fontsize=13,
        color="#64748B",
    )
    ax.text(
        0,
        -0.10,
        str(total),
        ha="center",
        va="center",
        fontsize=27,
        fontweight="bold",
        color="#0F172A",
    )

    legend_labels = []
    for label, value in zip(labels, values):
        pct = value / total * 100 if total else 0
        legend_labels.append(f"{label}  {value} 条  ({pct:.1f}%)")

    ax.legend(
        wedges,
        legend_labels,
        title="消息类型",
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        frameon=False,
        fontsize=10,
        title_fontsize=11,
        labelspacing=1.0,
        handlelength=1.2,
    )

    ax.set_title("消息类型分布", fontsize=18, fontweight="bold", pad=20, color="#0F172A")
    ax.axis("equal")
    fig.tight_layout()
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)



if __name__ == "__main__":
    task_root = Path(__file__).resolve().parent
    generate_all(task_root / "logs" / "messages.jsonl", task_root / "figures")
