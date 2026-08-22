"""Windows communication workbench for the multi-agent writing system."""

from __future__ import annotations

import asyncio
import ctypes
import io
import json
import os
import queue
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Plotting always happens off-screen; the desktop interface is provided by Tk.
os.environ.setdefault("MPLBACKEND", "Agg")

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from demo_proposal_writing import async_main
from requirement_loader import SUPPORTED_EXTENSIONS, load_requirement_file


APP_TITLE = "多智能体通信协议与科研协作写作系统"
COLORS = {
    "bg": "#F3F6FA",
    "panel": "#FFFFFF",
    "card": "#FFFFFF",
    "card_alt": "#F8FAFD",
    "border": "#E1E7EF",
    "grid": "#E9EFF7",
    "text": "#172033",
    "muted": "#748198",
    "cyan": "#2563EB",
    "cyan_dim": "#9AB4EE",
    "purple": "#7C3AED",
    "green": "#16A36A",
    "amber": "#E98B00",
    "red": "#DC3E4A",
}
PIPELINE_STAGES = (
    ("01", "任务拆解"),
    ("02", "并行起草"),
    ("03", "交叉核查"),
    ("04", "冲突闭环"),
    ("05", "最终统稿"),
)
AGENTS = {
    "coordinator": ("Coordinator", "路由 / 仲裁 / 黑板写入"),
    "literature_agent": ("Literature", "立项依据与研究现状"),
    "method_agent": ("Method", "研究方法与技术路线"),
    "experiment_agent": ("Experiment", "实验方案与评价指标"),
    "verifier_agent": ("Verification", "跨章节一致性核查"),
    "editor_agent": ("Editor", "最终统稿与格式统一"),
}
AGENT_STATUS_COLORS = {
    "IDLE": COLORS["muted"],
    "RUNNING": COLORS["cyan"],
    "WAIT_ACK": COLORS["purple"],
    "CONFLICT": COLORS["amber"],
    "COMPLETE": COLORS["green"],
    "ERROR": COLORS["red"],
}
FIGURES = {
    "sequence_diagram.png": ("通信序列图", "UML SEQUENCE", "完整消息调用链"),
    "communication_load.png": ("通信负载分布", "AGENT LOAD", "各 Agent 收发消息数"),
    "message_type_distribution.png": ("消息类型分布", "MESSAGE TYPES", "业务消息类型占比"),
}
MESSAGE_COLUMNS = (
    ("time", "TIME", 88, "center"),
    ("sender", "FROM", 112, "w"),
    ("receiver", "TO", 112, "w"),
    ("message_type", "TYPE", 154, "w"),
    ("priority", "PRIORITY", 84, "center"),
    ("status", "STATUS", 92, "center"),
    ("summary", "SUMMARY", 360, "w"),
)


def application_dir() -> Path:
    """Return the executable directory when frozen, otherwise the source directory."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def enable_windows_dpi_awareness() -> str:
    """Enable native per-monitor rendering before Tk creates its first window."""

    if sys.platform != "win32":
        return "platform-default"
    try:
        user32 = ctypes.windll.user32
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if setter(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1 fallback)
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) in (0, -2147024891):
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system-aware"
    except (AttributeError, OSError):
        pass
    return "unavailable"


def calibrate_tk_dpi(root: tk.Tk) -> tuple[float, float]:
    """Synchronize Tk point sizes with the actual DPI reported by Windows."""

    dpi = float(root.winfo_fpixels("1i"))
    scaling = dpi / 72.0
    root.tk.call("tk", "scaling", scaling)
    return dpi, scaling


class QueueWriter(io.TextIOBase):
    """Forward worker-thread output to Tk through a thread-safe queue."""

    def __init__(self, events: queue.Queue[tuple[str, Any]]) -> None:
        self.events = events

    def write(self, value: str) -> int:
        if value:
            self.events.put(("log", value))
        return len(value)

    def flush(self) -> None:
        return None


class ResearchWritingApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.dpi, self.tk_scaling = calibrate_tk_dpi(root)
        self.ui_scale = max(1.0, self.dpi / 96.0)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.running = False
        self.mission_state = "ready"
        self.last_output_dir = application_dir()
        self.stage_cards: list[dict[str, Any]] = []
        self.stage_states = ["pending"] * len(PIPELINE_STAGES)
        self.agent_states = {agent_id: "IDLE" for agent_id in AGENTS}
        self.agent_status_labels: dict[str, tk.Label] = {}
        self.metric_values: dict[str, tk.StringVar] = {}
        self.messages: list[dict[str, Any]] = []
        self.messages_by_id: dict[str, dict[str, Any]] = {}
        self.known_message_ids: set[str] = set()
        self.conflicts: dict[str, dict[str, Any]] = {}
        self.preview_canvases: dict[str, tk.Canvas] = {}
        self.preview_buttons: dict[str, tk.Button] = {}
        self.preview_images: dict[str, Any] = {}
        self.pulse_on = False
        self._pre_run_log_signature: tuple[int, int] | None = None
        self._awaiting_log_reset = False

        self.mode = tk.StringVar(value="mock")
        self.api_key = tk.StringVar()
        self.base_url = tk.StringVar(value="https://api.deepseek.com")
        self.model = tk.StringVar(value="deepseek-chat")
        self.output_dir = tk.StringVar(value=str(application_dir()))
        self.requirements_path = tk.StringVar(value="尚未导入，可直接粘贴或选择文件")
        self.requirements_status = tk.StringVar(value="支持 TXT / MD / JSON / CSV / DOCX / PDF")
        self.status = tk.StringVar(value="SYSTEM READY // Mock 离线演示可直接运行")
        self.pipeline_summary = tk.StringVar(value="0/5  PENDING")
        self.final_path = tk.StringVar(value="最终申请书：等待本次任务生成")
        self.visual_status = tk.StringVar(value="等待本次任务完成后，根据真实消息日志生成图表")

        self._build_ui()
        self._toggle_real_fields()
        self._reset_runtime_views(clear_log=False)
        self.root.after(100, self._drain_events)
        self.root.after(650, self._animate_pulse)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(self._px(1540), max(self._px(1160), screen_width - self._px(70)))
        height = min(self._px(920), max(self._px(680), screen_height - self._px(90)))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(self._px(1160), self._px(680))
        if sys.platform == "win32":
            # Let Windows apply the monitor's DPI-aware working area.  A manual
            # geometry can otherwise extend beyond the desktop at 125%/150% scaling.
            self.root.state("zoomed")
        self.root.configure(background=COLORS["bg"])
        self._configure_styles()

        outer = tk.Frame(self.root, background=COLORS["bg"])
        outer.pack(fill="both", expand=True)

        self.hero = tk.Canvas(
            outer, height=92, background=COLORS["bg"], highlightthickness=0
        )
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", self._draw_hero)

        metrics = tk.Frame(outer, background=COLORS["bg"])
        metrics.pack(fill="x", padx=18, pady=(7, 9))
        metric_specs = (
            ("agents", "6", "智能体", "在线协作角色", COLORS["cyan"]),
            ("messages", "0", "消息", "协议记录", COLORS["purple"]),
            ("ack_rate", "--", "ACK 闭环率", "确认应答完整度", COLORS["green"]),
            ("conflicts", "0 / 0", "冲突", "解决 / 发现", COLORS["amber"]),
            ("tokens", "0", "Token 开销", "通信估算", COLORS["purple"]),
        )
        for index, (key, value, title, subtitle, color) in enumerate(metric_specs):
            metrics.columnconfigure(index, weight=1, uniform="metric")
            self._build_metric_card(metrics, index, key, value, title, subtitle, color)

        body = tk.PanedWindow(
            outer,
            orient="horizontal",
            background=COLORS["bg"],
            borderwidth=0,
            sashwidth=self._px(8),
            sashrelief="flat",
            showhandle=False,
        )
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        left = tk.Frame(
            body,
            width=self._px(300),
            background=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        left.pack_propagate(False)
        body.add(left, minsize=self._px(284), width=self._px(300))
        self._build_settings_panel(left)

        right = tk.Frame(body, background=COLORS["bg"])
        body.add(right, minsize=self._px(820), stretch="always")
        self._build_workspace(right)

    def _px(self, value: int) -> int:
        """Scale explicit pixel dimensions to the current monitor DPI."""

        return max(1, round(value * self.ui_scale))

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Cyber.Horizontal.TProgressbar",
            troughcolor=COLORS["card_alt"],
            background=COLORS["cyan"],
            bordercolor=COLORS["card_alt"],
            lightcolor=COLORS["cyan"],
            darkcolor=COLORS["cyan_dim"],
            thickness=7,
        )
        style.configure(
            "Cyber.Vertical.TScrollbar",
            troughcolor=COLORS["card_alt"],
            background=COLORS["border"],
            bordercolor=COLORS["card_alt"],
            arrowcolor=COLORS["muted"],
        )
        style.configure(
            "Workbench.TNotebook",
            background=COLORS["bg"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Workbench.TNotebook.Tab",
            background=COLORS["card_alt"],
            foreground=COLORS["muted"],
            borderwidth=0,
            padding=(18, 9),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Workbench.TNotebook.Tab",
            background=[("selected", COLORS["card"]), ("active", COLORS["panel"])],
            foreground=[("selected", COLORS["cyan"]), ("active", COLORS["text"])],
        )
        style.configure(
            "Bus.Treeview",
            background=COLORS["card"],
            fieldbackground=COLORS["card"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            rowheight=27,
            font=("Segoe UI", 8),
        )
        style.map(
            "Bus.Treeview",
            background=[("selected", "#EAF1FF")],
            foreground=[("selected", COLORS["cyan"])],
        )
        style.configure(
            "Bus.Treeview.Heading",
            background=COLORS["card"],
            foreground=COLORS["muted"],
            relief="flat",
            bordercolor=COLORS["border"],
            font=("Segoe UI", 8, "bold"),
            padding=(5, 7),
        )
        style.map("Bus.Treeview.Heading", background=[("active", COLORS["border"])])

    def _build_metric_card(
        self,
        parent: tk.Frame,
        column: int,
        key: str,
        initial: str,
        title: str,
        subtitle: str,
        color: str,
    ) -> None:
        card = tk.Frame(
            parent,
            background=COLORS["card_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0))
        value_var = tk.StringVar(value=initial)
        self.metric_values[key] = value_var
        tk.Label(
            card,
            textvariable=value_var,
            background=COLORS["card_alt"],
            foreground=color,
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left", padx=(12, 9), pady=8)
        labels = tk.Frame(card, background=COLORS["card_alt"])
        labels.pack(side="left", fill="y", pady=7)
        tk.Label(
            labels,
            text=title,
            background=COLORS["card_alt"],
            foreground=COLORS["text"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            labels,
            text=subtitle,
            background=COLORS["card_alt"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")

    def _draw_hero(self, event: Any | None = None) -> None:
        del event
        width = max(self.hero.winfo_width(), 900)
        height = 92
        self.hero.delete("all")
        self.hero.create_rectangle(0, 0, width, height, fill=COLORS["panel"], outline="")
        for x in range(0, width, 72):
            self.hero.create_line(x, 0, x, height, fill=COLORS["grid"])
        for y in range(14, height, 28):
            self.hero.create_line(0, y, width, y, fill=COLORS["grid"])
        self.hero.create_rectangle(0, height - 2, width, height, fill=COLORS["cyan_dim"], outline="")
        self.hero.create_text(
            20, 16, anchor="nw", text="MAS // RESEARCH COLLABORATION SYSTEM",
            fill=COLORS["cyan"], font=("Segoe UI", 9, "bold"),
        )
        self.hero.create_text(
            20, 38, anchor="nw", text=APP_TITLE, fill=COLORS["text"],
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        self.hero.create_text(
            width - 370, 26, anchor="w",
            text="PROTOCOL 2.0  ·  HYBRID STAR + BLACKBOARD",
            fill=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        )
        state_text, state_color = {
            "ready": ("SYSTEM READY", COLORS["green"]),
            "active": ("SYSTEM ACTIVE", COLORS["cyan"]),
            "completed": ("MISSION COMPLETED", COLORS["green"]),
            "failed": ("EXECUTION FAILED", COLORS["red"]),
        }[self.mission_state]
        self.hero.create_oval(
            width - 370, 55, width - 360, 65, fill=state_color, outline="", tags="pulse"
        )
        self.hero.create_text(
            width - 350, 60, anchor="w", text=state_text, fill=state_color,
            font=("Segoe UI", 9, "bold"),
        )
        self.hero.create_text(
            width - 185, 60, anchor="w", text=f"● {self.mode.get().upper()} MODE",
            fill=COLORS["purple"] if self.mode.get() == "real" else COLORS["cyan"],
            font=("Segoe UI", 8, "bold"),
        )

    def _build_settings_panel(self, parent: tk.Frame) -> None:
        control_canvas = tk.Canvas(
            parent, background=COLORS["panel"], highlightthickness=0, borderwidth=0
        )
        control_scroll = ttk.Scrollbar(
            parent, orient="vertical", command=control_canvas.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        control_canvas.configure(yscrollcommand=control_scroll.set)
        control_canvas.pack(side="left", fill="both", expand=True)
        control_scroll.pack(side="right", fill="y")
        content = tk.Frame(control_canvas, background=COLORS["panel"])
        content_window = control_canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event: Any | None = None) -> None:
            control_canvas.configure(scrollregion=control_canvas.bbox("all"))

        def fit_content(event: Any) -> None:
            control_canvas.itemconfigure(content_window, width=event.width)

        content.bind("<Configure>", update_scroll_region)
        control_canvas.bind("<Configure>", fit_content)
        self.control_canvas = control_canvas

        tk.Label(
            content, text="CONTROL", background=COLORS["panel"], foreground=COLORS["cyan"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=16, pady=(15, 1))
        tk.Label(
            content, text="任务控制台", background=COLORS["panel"], foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w", padx=16, pady=(0, 10))

        mode_card = tk.Frame(
            content, background=COLORS["card_alt"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        mode_card.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(
            mode_card, text="EXECUTION CORE", background=COLORS["card_alt"],
            foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=11, pady=(8, 3))
        self.mock_radio = tk.Radiobutton(
            mode_card, text="●  MOCK / 离线确定性", variable=self.mode, value="mock",
            command=self._toggle_real_fields, background=COLORS["card_alt"],
            activebackground=COLORS["card_alt"], foreground=COLORS["text"],
            activeforeground=COLORS["cyan"], selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.mock_radio.pack(fill="x", padx=7, pady=1)
        self.real_radio = tk.Radiobutton(
            mode_card, text="●  REAL / OpenAI-compatible", variable=self.mode, value="real",
            command=self._toggle_real_fields, background=COLORS["card_alt"],
            activebackground=COLORS["card_alt"], foreground=COLORS["text"],
            activeforeground=COLORS["purple"], selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.real_radio.pack(fill="x", padx=7, pady=(1, 7))

        self.real_fields = tk.Frame(content, background=COLORS["panel"])
        self.real_fields.pack(fill="x", padx=16)
        self.real_fields.columnconfigure(0, weight=1)
        self.key_entry = self._create_field(
            self.real_fields, "API KEY / 仅驻留内存", self.api_key, 0, show="●"
        )
        self.url_entry = self._create_field(self.real_fields, "BASE URL", self.base_url, 1)
        self.model_entry = self._create_field(self.real_fields, "MODEL", self.model, 2)

        self.requirements_block = tk.Frame(
            content,
            background=COLORS["card_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        self.requirements_block.pack(fill="x", padx=16, pady=(9, 10))
        brief_head = tk.Frame(self.requirements_block, background=COLORS["card_alt"])
        brief_head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(
            brief_head,
            text="PROJECT BRIEF / 项目要求",
            background=COLORS["card_alt"],
            foreground=COLORS["purple"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            brief_head,
            text="REAL MODE",
            background="#F2ECFF",
            foreground=COLORS["purple"],
            font=("Segoe UI", 7, "bold"),
            padx=6,
            pady=2,
        ).pack(side="right")
        tk.Label(
            self.requirements_block,
            textvariable=self.requirements_path,
            background=COLORS["card_alt"],
            foreground=COLORS["text"],
            wraplength=self._px(245),
            justify="left",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=10)
        brief_actions = tk.Frame(self.requirements_block, background=COLORS["card_alt"])
        brief_actions.pack(fill="x", padx=10, pady=(5, 6))
        self.requirements_import_button = self._secondary_button(
            brief_actions, "导入要求文件", self._choose_requirements_file
        )
        self.requirements_import_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.requirements_clear_button = self._secondary_button(
            brief_actions, "清空", self._clear_requirements
        )
        self.requirements_clear_button.pack(side="left", padx=(3, 0))
        self.requirements_editor = tk.Text(
            self.requirements_block,
            height=6,
            wrap="word",
            relief="flat",
            background=COLORS["card"],
            foreground=COLORS["text"],
            insertbackground=COLORS["cyan"],
            selectbackground="#EAF1FF",
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["cyan_dim"],
            highlightthickness=1,
            font=("Microsoft YaHei UI", 8),
            padx=7,
            pady=6,
        )
        self.requirements_editor.pack(fill="x", padx=10)
        tk.Label(
            self.requirements_block,
            textvariable=self.requirements_status,
            background=COLORS["card_alt"],
            foreground=COLORS["muted"],
            wraplength=self._px(245),
            justify="left",
            font=("Microsoft YaHei UI", 7),
        ).pack(anchor="w", padx=10, pady=(5, 8))

        self.output_block = tk.Frame(content, background=COLORS["panel"])
        self.output_block.pack(fill="x", padx=16)
        tk.Label(
            self.output_block, text="OUTPUT ROOT", background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(2, 4))
        output_row = tk.Frame(self.output_block, background=COLORS["panel"])
        output_row.pack(fill="x")
        output_row.columnconfigure(0, weight=1)
        self.output_entry = tk.Entry(
            output_row, textvariable=self.output_dir, relief="flat",
            background=COLORS["card_alt"], foreground=COLORS["text"],
            insertbackground=COLORS["cyan"], highlightbackground=COLORS["border"],
            highlightcolor=COLORS["cyan_dim"], highlightthickness=1,
            font=("Segoe UI", 8),
        )
        self.output_entry.grid(row=0, column=0, sticky="ew", ipady=7)
        self.browse_button = tk.Button(
            output_row, text="浏览", command=self._choose_output_dir, relief="flat",
            background=COLORS["border"], activebackground=COLORS["cyan_dim"],
            foreground=COLORS["text"], activeforeground="white",
            font=("Microsoft YaHei UI", 9), cursor="hand2", padx=9,
        )
        self.browse_button.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        self.start_button = tk.Button(
            content, text="▶  启动五阶段协作任务", command=self._start, relief="flat",
            background=COLORS["cyan"], activebackground="#1D4ED8",
            foreground="white", activeforeground="white",
            disabledforeground=COLORS["muted"], font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2", pady=10,
        )
        self.start_button.pack(fill="x", padx=16, pady=(12, 7))

        secondary = tk.Frame(content, background=COLORS["panel"])
        secondary.pack(fill="x", padx=16)
        self.open_proposal_button = self._secondary_button(
            secondary, "打开申请书", self._open_proposal, state="disabled"
        )
        self.open_proposal_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.result_button = self._secondary_button(secondary, "结果目录", self._open_output_dir)
        self.result_button.pack(side="left", fill="x", expand=True, padx=(3, 0))

        status_card = tk.Frame(
            content, background=COLORS["card_alt"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        status_card.pack(fill="x", padx=16, pady=(10, 9))
        tk.Label(
            status_card, text="RUNTIME STATUS", background=COLORS["card_alt"],
            foreground=COLORS["purple"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 3))
        tk.Label(
            status_card, textvariable=self.status, wraplength=self._px(244), justify="left",
            background=COLORS["card_alt"], foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=10)
        tk.Label(
            status_card, textvariable=self.final_path, wraplength=self._px(244), justify="left",
            background=COLORS["card_alt"], foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=10, pady=(5, 8))

        profile = tk.Frame(content, background=COLORS["panel"])
        profile.pack(fill="x", padx=16, pady=(0, 14))
        tk.Label(
            profile, text="COMMUNICATION PROFILE", background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            profile,
            text="STAR BUS  ·  BLACKBOARD  ·  CAS VERSIONING\n"
                 "10 MESSAGE TYPES  ·  STRUCTURED JSONL",
            justify="left", wraplength=self._px(250), background=COLORS["panel"],
            foreground=COLORS["cyan_dim"],
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(3, 0))

    def _create_field(
        self,
        parent: tk.Frame,
        label: str,
        variable: tk.StringVar,
        index: int,
        *,
        show: str | None = None,
    ) -> tk.Entry:
        row = index * 2
        tk.Label(
            parent, text=label, background=COLORS["panel"], foreground=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).grid(row=row, column=0, sticky="w", pady=(0 if index == 0 else 7, 3))
        entry = tk.Entry(
            parent, textvariable=variable, show=show or "", relief="flat",
            background=COLORS["card_alt"], disabledbackground="#EEF2F7",
            disabledforeground="#9AA6B8", foreground=COLORS["text"],
            insertbackground=COLORS["cyan"], highlightbackground=COLORS["border"],
            highlightcolor=COLORS["cyan_dim"], highlightthickness=1,
            font=("Segoe UI", 8),
        )
        entry.grid(row=row + 1, column=0, sticky="ew", ipady=6)
        return entry

    def _secondary_button(
        self,
        parent: tk.Frame,
        text: str,
        command: Any,
        *,
        state: str = "normal",
    ) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, state=state, relief="flat",
            background=COLORS["card"], activebackground=COLORS["border"],
            foreground=COLORS["text"], activeforeground=COLORS["cyan"],
            disabledforeground="#A7B1C2", font=("Microsoft YaHei UI", 8),
            cursor="hand2", pady=7,
        )

    def _build_workspace(self, parent: tk.Frame) -> None:
        self._build_pipeline(parent)
        self.notebook = ttk.Notebook(parent, style="Workbench.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=(8, 0))

        overview = tk.Frame(self.notebook, background=COLORS["bg"])
        messages = tk.Frame(self.notebook, background=COLORS["bg"])
        conflicts = tk.Frame(self.notebook, background=COLORS["bg"])
        visualizations = tk.Frame(self.notebook, background=COLORS["bg"])
        self.notebook.add(overview, text="  总览 / OVERVIEW  ")
        self.notebook.add(messages, text="  消息总线 / MESSAGE BUS  ")
        self.notebook.add(conflicts, text="  冲突追踪 / CONFLICT TRACE  ")
        self.notebook.add(visualizations, text="  可视化 / VISUALIZATION  ")

        self._build_overview_tab(overview)
        self._build_message_tab(messages)
        self._build_conflict_tab(conflicts)
        self._build_visualization_tab(visualizations)

    def _build_pipeline(self, parent: tk.Frame) -> None:
        pipeline = tk.Frame(
            parent, background=COLORS["panel"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        pipeline.pack(fill="x", padx=(8, 0), pady=(0, 8))
        header = tk.Frame(pipeline, background=COLORS["panel"])
        header.pack(fill="x", padx=12, pady=(8, 5))
        tk.Label(
            header, text="FIVE-STAGE EXECUTION PIPELINE", background=COLORS["panel"],
            foreground=COLORS["purple"], font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            header, textvariable=self.pipeline_summary, background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        ).pack(side="right", padx=(10, 0))
        self.progress = ttk.Progressbar(
            header, mode="determinate", maximum=5, value=0, length=150,
            style="Cyber.Horizontal.TProgressbar",
        )
        self.progress.pack(side="right")

        stages = tk.Frame(pipeline, background=COLORS["panel"])
        stages.pack(fill="x", padx=10, pady=(0, 9))
        for index, (number, name) in enumerate(PIPELINE_STAGES):
            stages.columnconfigure(index, weight=1, uniform="stage")
            card = tk.Frame(stages, background=COLORS["card_alt"])
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 3, 0))
            top = tk.Frame(card, background=COLORS["card_alt"])
            top.pack(fill="x", padx=8, pady=(5, 0))
            number_label = tk.Label(
                top, text=number, background=COLORS["card_alt"],
                foreground=COLORS["border"], font=("Segoe UI", 9, "bold"),
            )
            number_label.pack(side="left")
            state_label = tk.Label(
                top, text="○ PENDING", background=COLORS["card_alt"],
                foreground=COLORS["muted"], font=("Segoe UI", 7, "bold"),
            )
            state_label.pack(side="right")
            name_label = tk.Label(
                card, text=name, background=COLORS["card_alt"], foreground=COLORS["muted"],
                font=("Microsoft YaHei UI", 8),
            )
            name_label.pack(anchor="w", padx=8, pady=(0, 5))
            self.stage_cards.append({
                "card": card, "top": top, "number": number_label,
                "state": state_label, "name": name_label,
            })

    def _build_overview_tab(self, parent: tk.Frame) -> None:
        vertical = tk.PanedWindow(
            parent, orient="vertical", background=COLORS["bg"], borderwidth=0,
            sashwidth=7, showhandle=False,
        )
        vertical.pack(fill="both", expand=True, pady=(7, 0))
        top = tk.PanedWindow(
            vertical, orient="horizontal", background=COLORS["bg"], borderwidth=0,
            sashwidth=7, showhandle=False,
        )
        vertical.add(top, minsize=235, stretch="always")

        topology_card = self._panel(top)
        top.add(topology_card, minsize=500, stretch="always")
        topology_header = tk.Frame(topology_card, background=COLORS["panel"])
        topology_header.pack(fill="x", padx=12, pady=(9, 4))
        tk.Label(
            topology_header, text="COMMUNICATION TOPOLOGY", background=COLORS["panel"],
            foreground=COLORS["cyan"], font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Label(
            topology_header, text="STAR BUS + VERSIONED BLACKBOARD",
            background=COLORS["panel"], foreground=COLORS["muted"],
            font=("Segoe UI", 7, "bold"),
        ).pack(side="right")
        self.topology_canvas = tk.Canvas(
            topology_card, background=COLORS["card"], highlightthickness=0
        )
        self.topology_canvas.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.topology_canvas.bind("<Configure>", self._draw_topology)

        agent_card = self._panel(top)
        top.add(agent_card, minsize=285, width=310)
        tk.Label(
            agent_card, text="AGENT RUNTIME STATE", background=COLORS["panel"],
            foreground=COLORS["purple"], font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(9, 5))
        for agent_id, (name, role) in AGENTS.items():
            row = tk.Frame(agent_card, background=COLORS["card_alt"])
            row.pack(fill="x", padx=9, pady=(0, 3))
            identity = tk.Frame(row, background=COLORS["card_alt"])
            identity.pack(side="left", fill="both", expand=True, padx=9, pady=5)
            tk.Label(
                identity, text=name, background=COLORS["card_alt"], foreground=COLORS["text"],
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            tk.Label(
                identity, text=role, background=COLORS["card_alt"], foreground=COLORS["muted"],
                font=("Microsoft YaHei UI", 7),
            ).pack(anchor="w")
            status_label = tk.Label(
                row, text="● IDLE", background=COLORS["card_alt"],
                foreground=COLORS["muted"], font=("Segoe UI", 7, "bold"),
                width=11, anchor="e",
            )
            status_label.pack(side="right", padx=(3, 9))
            self.agent_status_labels[agent_id] = status_label

        log_card = self._panel(vertical)
        vertical.add(log_card, minsize=145)
        log_header = tk.Frame(log_card, background=COLORS["panel"])
        log_header.pack(fill="x", padx=12, pady=(7, 5))
        self.status_dot = tk.Label(
            log_header, text="●", background=COLORS["panel"],
            foreground=COLORS["green"], font=("Segoe UI", 9),
        )
        self.status_dot.pack(side="left")
        tk.Label(
            log_header, text=" LIVE EVENT STREAM", background=COLORS["panel"],
            foreground=COLORS["text"], font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            log_header, textvariable=self.status, background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=(12, 0))
        tk.Button(
            log_header, text="CLEAR", command=self._clear_log, relief="flat",
            background=COLORS["panel"], activebackground=COLORS["card"],
            foreground=COLORS["muted"], activeforeground=COLORS["cyan"],
            font=("Segoe UI", 7, "bold"), cursor="hand2",
        ).pack(side="right")
        log_body = tk.Frame(log_card, background=COLORS["card_alt"])
        log_body.pack(fill="both", expand=True, padx=9, pady=(0, 8))
        self.log = tk.Text(
            log_body, wrap="word", state="disabled", font=("Consolas", 8),
            background=COLORS["card"], foreground=COLORS["text"],
            selectbackground=COLORS["cyan_dim"], insertbackground=COLORS["cyan"],
            relief="flat", padx=10, pady=7, spacing1=1, spacing3=1,
        )
        self.log.tag_configure("info", foreground=COLORS["text"])
        self.log.tag_configure("task", foreground=COLORS["cyan"])
        self.log.tag_configure("ack", foreground=COLORS["muted"])
        self.log.tag_configure("conflict", foreground=COLORS["amber"])
        self.log.tag_configure("success", foreground=COLORS["green"])
        self.log.tag_configure("error", foreground=COLORS["red"])
        scrollbar = ttk.Scrollbar(
            log_body, orient="vertical", command=self.log.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _panel(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent, background=COLORS["panel"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )

    def _build_message_tab(self, parent: tk.Frame) -> None:
        split = tk.PanedWindow(
            parent, orient="vertical", background=COLORS["bg"], borderwidth=0,
            sashwidth=7, showhandle=False,
        )
        split.pack(fill="both", expand=True, pady=(7, 0))
        table_card = self._panel(split)
        split.add(table_card, minsize=245, stretch="always")
        table_header = tk.Frame(table_card, background=COLORS["panel"])
        table_header.pack(fill="x", padx=12, pady=(8, 6))
        tk.Label(
            table_header, text="MESSAGE BUS / 真实结构化消息", background=COLORS["panel"],
            foreground=COLORS["cyan"], font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")
        self.bus_counter = tk.StringVar(value="0 RECORDS")
        tk.Label(
            table_header, textvariable=self.bus_counter, background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"),
        ).pack(side="right")

        tree_frame = tk.Frame(table_card, background=COLORS["panel"])
        tree_frame.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        column_ids = tuple(item[0] for item in MESSAGE_COLUMNS)
        self.message_tree = ttk.Treeview(
            tree_frame, columns=column_ids, show="headings",
            selectmode="browse", style="Bus.Treeview",
        )
        for column_id, title, width, anchor in MESSAGE_COLUMNS:
            self.message_tree.heading(column_id, text=title)
            self.message_tree.column(
                column_id, width=width, minwidth=60, anchor=anchor,
                stretch=column_id == "summary",
            )
        self.message_tree.tag_configure("task", foreground=COLORS["cyan"])
        self.message_tree.tag_configure("ack", foreground=COLORS["muted"])
        self.message_tree.tag_configure("conflict", foreground=COLORS["amber"])
        self.message_tree.tag_configure("success", foreground=COLORS["green"])
        self.message_tree.tag_configure("error", foreground=COLORS["red"])
        tree_y = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.message_tree.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        tree_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.message_tree.xview)
        self.message_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.message_tree.grid(row=0, column=0, sticky="nsew")
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.message_tree.bind("<<TreeviewSelect>>", self._show_message_detail)

        detail_card = self._panel(split)
        split.add(detail_card, minsize=165)
        tk.Label(
            detail_card, text="MESSAGE DETAIL / 协议字段检查", background=COLORS["panel"],
            foreground=COLORS["purple"], font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(8, 5))
        detail_body = tk.Frame(detail_card, background=COLORS["panel"])
        detail_body.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.message_detail = tk.Text(
            detail_body, wrap="word", state="disabled", background=COLORS["card"],
            foreground=COLORS["text"], selectbackground="#EAF1FF", relief="flat",
            font=("Consolas", 8), padx=10, pady=8,
        )
        self.message_detail.tag_configure(
            "title", foreground=COLORS["cyan"], font=("Consolas", 9, "bold")
        )
        self.message_detail.tag_configure("key", foreground=COLORS["purple"])
        self.message_detail.tag_configure("value", foreground=COLORS["text"])
        detail_scroll = ttk.Scrollbar(
            detail_body, orient="vertical", command=self.message_detail.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        self.message_detail.configure(yscrollcommand=detail_scroll.set)
        self.message_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")

    def _build_conflict_tab(self, parent: tk.Frame) -> None:
        split = tk.PanedWindow(
            parent, orient="horizontal", background=COLORS["bg"], borderwidth=0,
            sashwidth=7, showhandle=False,
        )
        split.pack(fill="both", expand=True, pady=(7, 0))
        list_card = self._panel(split)
        split.add(list_card, minsize=350, width=390)
        tk.Label(
            list_card, text="CONFLICT TRACE / 冲突清单", background=COLORS["panel"],
            foreground=COLORS["amber"], font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(9, 6))
        conflict_tree_frame = tk.Frame(list_card, background=COLORS["panel"])
        conflict_tree_frame.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.conflict_tree = ttk.Treeview(
            conflict_tree_frame, columns=("id", "type", "severity", "status"),
            show="headings", selectmode="browse", style="Bus.Treeview",
        )
        for key, title, width in (
            ("id", "ID", 62), ("type", "TYPE", 150),
            ("severity", "SEVERITY", 82), ("status", "STATUS", 88),
        ):
            self.conflict_tree.heading(key, text=title)
            self.conflict_tree.column(
                key, width=width, minwidth=55, anchor="w", stretch=key == "type"
            )
        self.conflict_tree.tag_configure("open", foreground=COLORS["amber"])
        self.conflict_tree.tag_configure("resolved", foreground=COLORS["green"])
        conflict_scroll = ttk.Scrollbar(
            conflict_tree_frame, orient="vertical", command=self.conflict_tree.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        self.conflict_tree.configure(yscrollcommand=conflict_scroll.set)
        self.conflict_tree.pack(side="left", fill="both", expand=True)
        conflict_scroll.pack(side="right", fill="y")
        self.conflict_tree.bind("<<TreeviewSelect>>", self._show_conflict_detail)

        trace_card = self._panel(split)
        split.add(trace_card, minsize=450, stretch="always")
        tk.Label(
            trace_card, text="RESOLUTION CHAIN / 检测—仲裁—修订—复核",
            background=COLORS["panel"], foreground=COLORS["purple"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(9, 6))
        trace_body = tk.Frame(trace_card, background=COLORS["panel"])
        trace_body.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.conflict_detail = tk.Text(
            trace_body, wrap="word", state="disabled", background=COLORS["card"],
            foreground=COLORS["text"], selectbackground="#EAF1FF", relief="flat",
            font=("Microsoft YaHei UI", 9), padx=13, pady=11, spacing3=2,
        )
        self.conflict_detail.tag_configure(
            "title", foreground=COLORS["amber"], font=("Segoe UI", 13, "bold")
        )
        self.conflict_detail.tag_configure(
            "label", foreground=COLORS["purple"], font=("Microsoft YaHei UI", 9, "bold")
        )
        self.conflict_detail.tag_configure(
            "success", foreground=COLORS["green"], font=("Microsoft YaHei UI", 9, "bold")
        )
        self.conflict_detail.tag_configure(
            "timeline", foreground=COLORS["cyan"], font=("Consolas", 8, "bold")
        )
        trace_scroll = ttk.Scrollbar(
            trace_body, orient="vertical", command=self.conflict_detail.yview,
            style="Cyber.Vertical.TScrollbar",
        )
        self.conflict_detail.configure(yscrollcommand=trace_scroll.set)
        self.conflict_detail.pack(side="left", fill="both", expand=True)
        trace_scroll.pack(side="right", fill="y")

    def _build_visualization_tab(self, parent: tk.Frame) -> None:
        header = tk.Frame(
            parent, background=COLORS["panel"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        header.pack(fill="x", pady=(7, 7))
        tk.Label(
            header, text="VISUALIZATION / 通信分析", background=COLORS["panel"],
            foreground=COLORS["cyan"], font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", padx=12, pady=9)
        tk.Label(
            header, textvariable=self.visual_status, background=COLORS["panel"],
            foreground=COLORS["muted"], font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=12)

        cards = tk.Frame(parent, background=COLORS["bg"])
        cards.pack(fill="both", expand=True)
        for index, (filename, (title, english, description)) in enumerate(FIGURES.items()):
            cards.columnconfigure(index, weight=1, uniform="figure")
            card = self._panel(cards)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))
            cards.rowconfigure(0, weight=1)
            tk.Label(
                card, text=english, background=COLORS["panel"],
                foreground=COLORS["purple"], font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w", padx=12, pady=(11, 1))
            tk.Label(
                card, text=title, background=COLORS["panel"], foreground=COLORS["text"],
                font=("Microsoft YaHei UI", 13, "bold"),
            ).pack(anchor="w", padx=12)
            tk.Label(
                card, text=description, background=COLORS["panel"],
                foreground=COLORS["muted"], font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", padx=12, pady=(1, 8))
            canvas = tk.Canvas(
                card, height=260, background=COLORS["card"],
                highlightbackground=COLORS["border"], highlightthickness=1,
                cursor="hand2",
            )
            canvas.pack(fill="both", expand=True, padx=11, pady=(0, 9))
            canvas.bind("<Button-1>", lambda _event, name=filename: self._open_figure(name))
            canvas.bind("<Configure>", lambda _event, name=filename: self._draw_figure_preview(name))
            self.preview_canvases[filename] = canvas
            button = self._secondary_button(
                card, "查看高清图片", lambda name=filename: self._open_figure(name),
                state="disabled",
            )
            button.pack(fill="x", padx=11, pady=(0, 11))
            self.preview_buttons[filename] = button

    def _toggle_real_fields(self) -> None:
        real_mode = self.mode.get() == "real"
        state = "normal" if real_mode and not self.running else "disabled"
        for widget in (self.key_entry, self.url_entry, self.model_entry):
            widget.configure(state=state)
        if real_mode:
            if not self.real_fields.winfo_manager():
                self.real_fields.pack(fill="x", padx=16, before=self.output_block)
            if not self.requirements_block.winfo_manager():
                self.requirements_block.pack(
                    fill="x", padx=16, pady=(9, 10), before=self.output_block
                )
            if not self.running:
                self.status.set("SYSTEM READY // 可导入实际项目要求并由真实模型据此写作")
        else:
            self.real_fields.pack_forget()
            self.requirements_block.pack_forget()
            if not self.running and self.mission_state == "ready":
                self.status.set("SYSTEM READY // Mock 离线演示可直接运行")
        self._draw_hero()
        self.root.after_idle(
            lambda: self.control_canvas.configure(scrollregion=self.control_canvas.bbox("all"))
        )

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir.get(), title="选择结果保存目录")
        if selected:
            self.output_dir.set(selected)

    def _choose_requirements_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="导入实际项目要求",
            filetypes=(
                ("支持的要求文档", "*.txt *.md *.markdown *.json *.yaml *.yml *.csv *.docx *.pdf"),
                ("文本与 Markdown", "*.txt *.md *.markdown"),
                ("Office 与 PDF", "*.docx *.pdf"),
                ("全部文件", "*.*"),
            ),
        )
        if not selected:
            return
        path = Path(selected)
        try:
            content = load_requirement_file(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, f"无法导入项目要求：\n{exc}")
            return
        self.requirements_editor.delete("1.0", "end")
        self.requirements_editor.insert("1.0", content)
        self.requirements_path.set(path.name)
        self.requirements_status.set(
            f"已导入 {len(content):,} 字符 · 启动后传入全部专业智能体"
        )
        self.status.set("PROJECT BRIEF READY // Real 模式将按导入要求生成申请书")

    def _clear_requirements(self) -> None:
        self.requirements_editor.delete("1.0", "end")
        self.requirements_path.set("尚未导入，可直接粘贴或选择文件")
        supported = " / ".join(sorted(item.lstrip(".").upper() for item in SUPPORTED_EXTENSIONS))
        self.requirements_status.set(f"支持 {supported}")

    def _append_log(self, value: str) -> None:
        upper = value.upper()
        if "ERROR" in upper or "FAILED" in upper:
            tag = "error"
        elif "CONFLICT" in upper or "冲突" in value:
            tag = "conflict"
        elif "FINAL_RESULT" in upper or "完成" in value or "RESOLVED" in upper:
            tag = "success"
        elif "TASK_ASSIGN" in upper or "REVISION_REQUEST" in upper:
            tag = "task"
        elif "ACK" in upper:
            tag = "ack"
        else:
            tag = "info"
        self.log.configure(state="normal")
        self.log.insert("end", value, tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        self._update_pipeline(value)

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_stage(self, index: int, state: str) -> None:
        if index < 0 or index >= len(self.stage_cards):
            return
        self.stage_states[index] = state
        labels = self.stage_cards[index]
        background, accent, state_text = {
            "pending": (COLORS["card_alt"], COLORS["muted"], "○ PENDING"),
            "active": ("#EAF1FF", COLORS["cyan"], "● RUNNING"),
            "conflict": ("#FFF5E5", COLORS["amber"], "! CONFLICT"),
            "complete": ("#EAF8F1", COLORS["green"], "✓ COMPLETE"),
            "error": ("#FFF0F1", COLORS["red"], "! ERROR"),
        }[state]
        labels["card"].configure(background=background)
        labels["top"].configure(background=background)
        labels["number"].configure(background=background, foreground=accent)
        labels["state"].configure(background=background, foreground=accent, text=state_text)
        labels["name"].configure(
            background=background,
            foreground=COLORS["text"] if state in {"active", "conflict"} else COLORS["muted"],
        )
        complete = sum(item == "complete" for item in self.stage_states)
        self.progress.configure(value=complete + (0.35 if "active" in self.stage_states else 0))
        if complete == len(self.stage_states):
            self.pipeline_summary.set("5/5  PIPELINE COMPLETE")
        elif self.running:
            self.pipeline_summary.set(f"{complete}/5  EXECUTING")
        else:
            self.pipeline_summary.set(f"{complete}/5  PENDING")

    def _advance_stage(self, index: int, state: str = "active") -> None:
        for previous in range(index):
            self._set_stage(previous, "complete")
        self._set_stage(index, state)

    def _reset_pipeline(self) -> None:
        for index in range(len(self.stage_cards)):
            self._set_stage(index, "pending")

    def _update_pipeline(self, value: str) -> None:
        transitions = (
            ("[1]", 0, "active"),
            ("[2]", 1, "active"),
            ("[3-4]", 2, "active"),
            ("[5-6]", 3, "conflict"),
            ("[7]", 3, "active"),
            ("[8]", 4, "active"),
        )
        for marker, active, state in transitions:
            if marker in value:
                self._advance_stage(active, state)

    def _animate_pulse(self) -> None:
        self.pulse_on = not self.pulse_on
        if self.hero.find_withtag("pulse"):
            if self.mission_state == "active":
                color = COLORS["cyan"] if self.pulse_on else COLORS["cyan_dim"]
            elif self.mission_state == "failed":
                color = COLORS["red"]
            else:
                color = COLORS["green"] if self.pulse_on else "#7BCDA7"
            self.hero.itemconfigure("pulse", fill=color)
        if hasattr(self, "status_dot"):
            if self.mission_state == "active":
                color = COLORS["cyan"] if self.pulse_on else COLORS["cyan_dim"]
            elif self.mission_state == "failed":
                color = COLORS["red"]
            else:
                color = COLORS["green"]
            self.status_dot.configure(foreground=color)
        self.root.after(650, self._animate_pulse)

    def _start(self) -> None:
        if self.running:
            return
        raw_output = self.output_dir.get().strip()
        if not raw_output:
            messagebox.showerror(APP_TITLE, "请选择结果保存目录。")
            return
        output = Path(raw_output).expanduser()
        try:
            output.mkdir(parents=True, exist_ok=True)
            output = output.resolve()
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法使用结果目录：\n{exc}")
            return

        messages_path = output / "logs" / "messages.jsonl"
        self._pre_run_log_signature = self._file_signature(messages_path)
        self._awaiting_log_reset = True
        self.running = True
        self.mission_state = "active"
        self.last_output_dir = output
        self._reset_runtime_views(clear_log=True)
        self._set_stage(0, "active")
        self._set_controls_running(True)
        self.status.set("SYSTEM ACTIVE // 正在执行真实协议消息与五阶段协作")
        self.final_path.set("最终申请书：生成中…")
        self._draw_hero()
        self._append_log(f"结果目录：{output}\n运行模式：{self.mode.get()}\n\n")

        requirements_text = self.requirements_editor.get("1.0", "end-1c").strip()
        requirements_source = self.requirements_path.get().strip()
        if self.mode.get() == "real" and requirements_text:
            self._append_log(
                f"项目要求：{requirements_source}（{len(requirements_text):,} 字符）\n\n"
            )

        settings = {
            "mode": self.mode.get(),
            "api_key": self.api_key.get().strip(),
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
            "output": output,
            "requirements_text": requirements_text if self.mode.get() == "real" else "",
            "requirements_source": requirements_source if self.mode.get() == "real" else "",
        }
        threading.Thread(target=self._run_worker, args=(settings,), daemon=True).start()

    def _set_controls_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.mock_radio.configure(state="disabled" if running else "normal")
        self.real_radio.configure(state="disabled" if running else "normal")
        self.output_entry.configure(state="disabled" if running else "normal")
        self.browse_button.configure(state="disabled" if running else "normal")
        self.requirements_import_button.configure(state="disabled" if running else "normal")
        self.requirements_clear_button.configure(state="disabled" if running else "normal")
        self.requirements_editor.configure(state="disabled" if running else "normal")
        if running:
            for widget in (self.key_entry, self.url_entry, self.model_entry):
                widget.configure(state="disabled")
        else:
            self._toggle_real_fields()

    def _reset_runtime_views(self, *, clear_log: bool) -> None:
        self.messages.clear()
        self.messages_by_id.clear()
        self.known_message_ids.clear()
        self.conflicts.clear()
        for item in self.message_tree.get_children():
            self.message_tree.delete(item)
        for item in self.conflict_tree.get_children():
            self.conflict_tree.delete(item)
        self.bus_counter.set("0 RECORDS")
        self.metric_values["messages"].set("0")
        self.metric_values["ack_rate"].set("--")
        self.metric_values["conflicts"].set("0 / 0")
        self.metric_values["tokens"].set("0")
        self.agent_states = {agent_id: "IDLE" for agent_id in AGENTS}
        if self.running:
            self.agent_states["coordinator"] = "RUNNING"
        self._refresh_agent_states()
        self._reset_pipeline()
        self._set_text_placeholder(
            self.message_detail,
            "等待真实协议消息。\n任务启动后，选择任意消息查看完整 AgentMessage 字段。",
        )
        self._set_text_placeholder(
            self.conflict_detail,
            "等待 VerificationAgent 发布 CONFLICT_NOTICE。\n"
            "冲突证据、参与 Agent 和闭环消息将来自本次运行记录。",
        )
        self.preview_images.clear()
        self.visual_status.set("等待本次任务完成后，根据真实消息日志生成图表")
        for filename, button in self.preview_buttons.items():
            button.configure(state="disabled")
            self._draw_waiting_preview(filename)
        if clear_log:
            self._clear_log()

    def _set_text_placeholder(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _run_worker(self, settings: dict[str, Any]) -> None:
        writer = QueueWriter(self.events)
        env_names = ("MAS_LLM_API_KEY", "MAS_LLM_BASE_URL", "MAS_LLM_MODEL")
        previous = {name: os.environ.get(name) for name in env_names}
        try:
            if settings["mode"] == "real":
                if settings["api_key"]:
                    os.environ["MAS_LLM_API_KEY"] = settings["api_key"]
                if settings["base_url"]:
                    os.environ["MAS_LLM_BASE_URL"] = settings["base_url"]
                if settings["model"]:
                    os.environ["MAS_LLM_MODEL"] = settings["model"]

            args = SimpleNamespace(
                real=settings["mode"] == "real",
                mock=settings["mode"] == "mock",
                backend=None,
                task_dir=settings["output"],
                requirements=None,
                requirements_text=settings["requirements_text"],
                requirements_source=settings["requirements_source"],
                topic=None,
            )
            with redirect_stdout(writer), redirect_stderr(writer):
                summary = asyncio.run(async_main(args))
            self.events.put(("done", summary))
        except Exception:
            self.events.put(("error", traceback.format_exc()))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < 250:
                event, value = self.events.get_nowait()
                processed += 1
                if event == "log":
                    self._append_log(str(value))
                elif event == "done":
                    self._finish_success(value)
                elif event == "error":
                    self._finish_error(str(value))
        except queue.Empty:
            pass
        if self.running:
            self._sync_runtime_messages()
        self.root.after(100, self._drain_events)

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _sync_runtime_messages(self, *, force: bool = False) -> None:
        path = self.last_output_dir / "logs" / "messages.jsonl"
        signature = self._file_signature(path)
        if signature is None:
            return
        if self._awaiting_log_reset and not force:
            if signature == self._pre_run_log_signature:
                return
            self._awaiting_log_reset = False
        if force:
            self._awaiting_log_reset = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return
        added = False
        for line in lines:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            message_id = str(message.get("message_id", ""))
            if not message_id or message_id in self.known_message_ids:
                continue
            self.known_message_ids.add(message_id)
            self.messages.append(message)
            self.messages_by_id[message_id] = message
            self._insert_message(message)
            self._apply_message_state(message)
            self._update_conflict_from_message(message)
            added = True
        if added:
            self.bus_counter.set(f"{len(self.messages)} RECORDS")
            self._refresh_metrics()
            self._refresh_agent_states()
            self._refresh_conflict_tree()

    def _insert_message(self, message: dict[str, Any]) -> None:
        timestamp = str(message.get("timestamp", ""))
        time_value = timestamp[11:19] if len(timestamp) >= 19 else timestamp
        sender = self._display_agent(str(message.get("sender", "")))
        receiver = self._display_agent(str(message.get("receiver", "")))
        message_type = str(message.get("message_type", ""))
        summary = str(message.get("summary", "")).replace("\n", " ")
        values = (
            time_value, sender, receiver, message_type,
            message.get("priority", ""), message.get("status", ""), summary,
        )
        if message_type == "CONFLICT_NOTICE":
            tag = "conflict"
        elif message_type in {"CONFLICT_RESOLUTION", "FINAL_RESULT"}:
            tag = "success"
        elif message_type in {"TASK_ASSIGN", "INFO_REQUEST", "REVISION_REQUEST"}:
            tag = "task"
        elif message_type == "ACK":
            tag = "ack"
        elif message_type == "ERROR":
            tag = "error"
        else:
            tag = ""
        self.message_tree.insert(
            "", "end", iid=message["message_id"], values=values, tags=(tag,)
        )
        self.message_tree.see(message["message_id"])

    @staticmethod
    def _display_agent(agent_id: str) -> str:
        return AGENTS.get(agent_id, (agent_id, ""))[0]

    def _apply_message_state(self, message: dict[str, Any]) -> None:
        sender = str(message.get("sender", ""))
        receiver = str(message.get("receiver", ""))
        message_type = str(message.get("message_type", ""))
        if self.running:
            self.agent_states["coordinator"] = "RUNNING"
        if (
            message_type in {"TASK_ASSIGN", "INFO_REQUEST", "REVISION_REQUEST"}
            and receiver in self.agent_states
        ):
            self.agent_states[receiver] = "WAIT_ACK"
        elif message_type == "ACK":
            parent = self.messages_by_id.get(str(message.get("parent_message_id", "")), {})
            parent_type = parent.get("message_type")
            if sender != "coordinator" and sender in self.agent_states:
                self.agent_states[sender] = "RUNNING"
            elif receiver in self.agent_states:
                if parent_type == "CONFLICT_NOTICE":
                    self.agent_states[receiver] = "CONFLICT"
                else:
                    self.agent_states[receiver] = "COMPLETE"
        elif message_type == "CONFLICT_NOTICE" and sender in self.agent_states:
            self.agent_states[sender] = "CONFLICT"
        elif message_type in {
            "RESULT_SUBMIT", "REVISION_SUBMIT", "CONFLICT_RESOLUTION", "FINAL_RESULT"
        }:
            if sender in self.agent_states:
                self.agent_states[sender] = "WAIT_ACK"
        elif message_type == "ERROR":
            target = sender if sender in self.agent_states else receiver
            if target in self.agent_states:
                self.agent_states[target] = "ERROR"

    def _refresh_agent_states(self) -> None:
        for agent_id, status in self.agent_states.items():
            label = self.agent_status_labels.get(agent_id)
            if label is not None:
                label.configure(text=f"● {status}", foreground=AGENT_STATUS_COLORS[status])
        self._draw_topology()

    def _draw_topology(self, event: Any | None = None) -> None:
        del event
        if not hasattr(self, "topology_canvas"):
            return
        canvas = self.topology_canvas
        width = max(canvas.winfo_width(), 540)
        height = max(canvas.winfo_height(), 210)
        canvas.delete("all")
        for x in range(0, width, 48):
            canvas.create_line(x, 0, x, height, fill=COLORS["grid"])
        for y in range(0, height, 36):
            canvas.create_line(0, y, width, y, fill=COLORS["grid"])
        positions = {
            "coordinator": (0.50, 0.48),
            "literature_agent": (0.13, 0.18),
            "method_agent": (0.39, 0.15),
            "experiment_agent": (0.70, 0.17),
            "verifier_agent": (0.86, 0.58),
            "editor_agent": (0.66, 0.83),
        }
        center_x, center_y = positions["coordinator"]
        for agent_id, (ratio_x, ratio_y) in positions.items():
            if agent_id == "coordinator":
                continue
            canvas.create_line(
                center_x * width, center_y * height, ratio_x * width, ratio_y * height,
                fill=COLORS["cyan_dim"], width=1, arrow="both", arrowshape=(7, 8, 3),
            )
        blackboard_x, blackboard_y = 0.20 * width, 0.78 * height
        canvas.create_line(
            center_x * width, center_y * height, blackboard_x, blackboard_y,
            fill=COLORS["purple"], dash=(4, 3), width=1,
        )
        canvas.create_rectangle(
            blackboard_x - 63, blackboard_y - 20, blackboard_x + 63, blackboard_y + 20,
            fill="#F2ECFF", outline=COLORS["purple"], width=1,
        )
        canvas.create_text(
            blackboard_x, blackboard_y - 4, text="VERSIONED BLACKBOARD",
            fill=COLORS["purple"], font=("Segoe UI", 7, "bold"),
        )
        canvas.create_text(
            blackboard_x, blackboard_y + 9, text="CAS · SECTION LOCKS",
            fill=COLORS["muted"], font=("Segoe UI", 6),
        )
        for agent_id, (ratio_x, ratio_y) in positions.items():
            x = ratio_x * width
            y = ratio_y * height
            name = AGENTS[agent_id][0]
            status = self.agent_states[agent_id]
            color = AGENT_STATUS_COLORS[status]
            node_width = 116 if agent_id != "coordinator" else 130
            node_height = 43
            canvas.create_rectangle(
                x - node_width / 2, y - node_height / 2,
                x + node_width / 2, y + node_height / 2,
                fill=COLORS["card_alt"], outline=color,
                width=2 if status != "IDLE" else 1,
            )
            canvas.create_oval(
                x - node_width / 2 + 8, y - 13,
                x - node_width / 2 + 16, y - 5, fill=color, outline="",
            )
            canvas.create_text(
                x - node_width / 2 + 21, y - 9, anchor="w", text=name,
                fill=COLORS["text"], font=("Segoe UI", 8, "bold"),
            )
            canvas.create_text(
                x, y + 9, text=status, fill=color, font=("Segoe UI", 6, "bold")
            )
        canvas.create_text(
            width - 8, height - 7, anchor="se",
            text="LEAF-TO-LEAF DIRECT MESSAGE: BLOCKED",
            fill=COLORS["muted"], font=("Segoe UI", 6, "bold"),
        )

    def _refresh_metrics(self) -> None:
        total = len(self.messages)
        acknowledgements = sum(item.get("message_type") == "ACK" for item in self.messages)
        requiring_ack = sum(bool(item.get("requires_ack")) for item in self.messages)
        ack_rate = round(100 * acknowledgements / requiring_ack) if requiring_ack else 0
        tokens = sum(int(item.get("token_count", 0) or 0) for item in self.messages)
        resolved = sum(item.get("status") == "RESOLVED" for item in self.conflicts.values())
        self.metric_values["messages"].set(str(total))
        self.metric_values["ack_rate"].set(f"{ack_rate}%" if requiring_ack else "--")
        self.metric_values["conflicts"].set(f"{resolved} / {len(self.conflicts)}")
        self.metric_values["tokens"].set(self._compact_number(tokens))

    @staticmethod
    def _compact_number(value: int) -> str:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)

    def _show_message_detail(self, _event: Any | None = None) -> None:
        selection = self.message_tree.selection()
        if not selection:
            return
        message = self.messages_by_id.get(selection[0])
        if message is None:
            return
        ack_message = next((
            item for item in self.messages
            if item.get("message_type") == "ACK"
            and item.get("parent_message_id") == message.get("message_id")
        ), None)
        if not message.get("requires_ack"):
            ack_state = "NOT REQUIRED"
        elif ack_message:
            ack_state = (
                f"ACKED / {ack_message.get('status', '')} / "
                f"{ack_message.get('message_id', '')}"
            )
        else:
            ack_state = "WAITING"
        fields = (
            ("Message ID", message.get("message_id")),
            ("Parent Message ID", message.get("parent_message_id")),
            ("Conversation ID", message.get("conversation_id")),
            ("Sender", message.get("sender")),
            ("Receiver", message.get("receiver")),
            ("Message Type", message.get("message_type")),
            ("Timestamp", message.get("timestamp")),
            ("Priority", message.get("priority")),
            ("Status", message.get("status")),
            ("Token Count", message.get("token_count")),
            ("Payload Digest", message.get("payload_digest")),
            ("Requires ACK", message.get("requires_ack")),
            ("ACK State", ack_state),
            ("Summary", message.get("summary")),
        )
        widget = self.message_detail
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", "FORMAL AGENT MESSAGE / SCHEMA 2.0\n", "title")
        for key, value in fields:
            widget.insert("end", f"{key:<20}", "key")
            widget.insert("end", f"{value}\n", "value")
        widget.insert("end", "\nPAYLOAD\n", "title")
        widget.insert(
            "end", json.dumps(message.get("payload", {}), ensure_ascii=False, indent=2), "value"
        )
        widget.configure(state="disabled")

    def _update_conflict_from_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("message_type")
        if message_type == "CONFLICT_NOTICE":
            payload = dict(message.get("payload") or {})
            conflict_id = str(payload.get("conflict_id", ""))
            if conflict_id:
                payload.setdefault("status", "OPEN")
                self.conflicts[conflict_id] = payload
        elif message_type == "CONFLICT_RESOLUTION" and message.get("status") == "RESOLVED":
            for conflict in self.conflicts.values():
                conflict["status"] = "RESOLVED"

    def _refresh_conflict_tree(self) -> None:
        current = self.conflict_tree.selection()
        selected = current[0] if current else None
        existing = set(self.conflict_tree.get_children())
        for conflict_id, conflict in self.conflicts.items():
            values = (
                conflict_id, conflict.get("type", ""), conflict.get("severity", ""),
                conflict.get("status", "OPEN"),
            )
            tag = "resolved" if conflict.get("status") == "RESOLVED" else "open"
            if conflict_id in existing:
                self.conflict_tree.item(conflict_id, values=values, tags=(tag,))
            else:
                self.conflict_tree.insert(
                    "", "end", iid=conflict_id, values=values, tags=(tag,)
                )
        if selected and selected in self.conflicts:
            self.conflict_tree.selection_set(selected)
            self._render_conflict_detail(selected)
        elif self.conflicts:
            first = next(iter(self.conflicts))
            self.conflict_tree.selection_set(first)
            self.conflict_tree.focus(first)
            self._render_conflict_detail(first)
        self._refresh_metrics()

    def _show_conflict_detail(self, _event: Any | None = None) -> None:
        selection = self.conflict_tree.selection()
        if selection:
            self._render_conflict_detail(selection[0])

    def _render_conflict_detail(self, conflict_id: str) -> None:
        conflict = self.conflicts.get(conflict_id)
        if conflict is None:
            return
        widget = self.conflict_detail
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        status = str(conflict.get("status", "OPEN"))
        widget.insert("end", f"CONFLICT {conflict_id}  //  {status}\n", "title")
        widget.insert("end", "TYPE\n", "label")
        widget.insert("end", f"{conflict.get('type', '')} · {conflict.get('severity', '')}\n\n")
        widget.insert("end", "DETECTED BY\n", "label")
        widget.insert("end", "VerificationAgent\n\n")
        widget.insert("end", "DESCRIPTION\n", "label")
        widget.insert("end", f"{conflict.get('description', '')}\n\n")
        widget.insert("end", "PARTICIPANTS\n", "label")
        participants = conflict.get("agents") or conflict.get("participants") or []
        widget.insert(
            "end", " / ".join(self._display_agent(str(item)) for item in participants) + "\n\n"
        )
        widget.insert("end", "EVIDENCE\n", "label")
        evidence = conflict.get("evidence") or []
        if isinstance(evidence, list):
            for item in evidence:
                widget.insert("end", f"• {item}\n")
        else:
            widget.insert("end", f"{evidence}\n")
        widget.insert("end", "\nRECOMMENDATION\n", "label")
        widget.insert(
            "end", f"{conflict.get('suggestion') or conflict.get('recommendation') or ''}\n"
        )
        if conflict.get("decision"):
            widget.insert("end", "\nARBITRATION DECISION\n", "label")
            widget.insert("end", f"{conflict['decision']}\n", "success")
        widget.insert("end", "\nMESSAGE TIMELINE\n", "label")
        related = [
            item for item in self.messages
            if self._message_relates_to_conflict(item, conflict_id)
        ]
        if not related:
            widget.insert("end", "等待关联消息…\n")
        for index, message in enumerate(related, 1):
            timestamp = str(message.get("timestamp", ""))
            stamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp
            line = (
                f"{index:02d}  {stamp}  {message.get('message_type', ''):<21} "
                f"{self._display_agent(str(message.get('sender', '')))} → "
                f"{self._display_agent(str(message.get('receiver', '')))}\n"
            )
            widget.insert("end", line, "timeline")
            widget.insert("end", f"    {message.get('summary', '')}\n")
        if status == "RESOLVED":
            widget.insert("end", "\n✓ VERIFIED AND RESOLVED\n", "success")
        widget.configure(state="disabled")

    def _message_relates_to_conflict(
        self, message: dict[str, Any], conflict_id: str
    ) -> bool:
        if message.get("message_type") == "CONFLICT_RESOLUTION":
            return True
        current: dict[str, Any] | None = message
        visited: set[str] = set()
        while current:
            payload = current.get("payload") or {}
            if payload.get("conflict_id") == conflict_id:
                return True
            if conflict_id in (payload.get("conflict_ids") or []):
                return True
            current_id = str(current.get("message_id", ""))
            if current_id in visited:
                break
            visited.add(current_id)
            parent_id = str(current.get("parent_message_id") or "")
            current = self.messages_by_id.get(parent_id)
        return False

    def _load_completed_conflicts(self) -> None:
        path = self.last_output_dir / "logs" / "conflicts_and_resolutions.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        detected = {
            str(item.get("conflict_id")): dict(item)
            for item in payload.get("detected_conflicts", [])
            if item.get("conflict_id")
        }
        for resolved in payload.get("resolved_conflicts", []):
            conflict_id = str(resolved.get("conflict_id", ""))
            if conflict_id:
                detected[conflict_id] = dict(resolved)
        self.conflicts = detected
        self._refresh_conflict_tree()

    def _draw_waiting_preview(self, filename: str) -> None:
        canvas = self.preview_canvases.get(filename)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 180)
        canvas.create_text(
            width / 2, height / 2 - 10, text="◇", fill=COLORS["border"],
            font=("Segoe UI", 23),
        )
        canvas.create_text(
            width / 2, height / 2 + 25, text="等待本次任务完成后生成",
            fill=COLORS["muted"], font=("Microsoft YaHei UI", 9),
        )

    def _draw_figure_preview(self, filename: str) -> None:
        canvas = self.preview_canvases.get(filename)
        if canvas is None:
            return
        path = self.last_output_dir / "figures" / filename
        if self.mission_state != "completed" or not path.exists():
            self._draw_waiting_preview(filename)
            return
        try:
            target_width = max(200, canvas.winfo_width() - 16)
            target_height = max(140, canvas.winfo_height() - 16)
            with Image.open(path) as source:
                rendered = source.convert("RGBA")
                rendered.thumbnail(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
                image = ImageTk.PhotoImage(rendered, master=self.root)
        except (OSError, tk.TclError):
            self._draw_waiting_preview(filename)
            canvas.create_text(
                max(canvas.winfo_width(), 260) / 2,
                max(canvas.winfo_height(), 180) / 2 + 48,
                text="预览加载失败，可点击打开高清图片",
                fill=COLORS["amber"], font=("Microsoft YaHei UI", 8),
            )
            return
        self.preview_images[filename] = image
        canvas.delete("all")
        canvas.create_image(
            canvas.winfo_width() / 2, canvas.winfo_height() / 2, image=image
        )

    def _refresh_visualizations(self) -> None:
        generated = 0
        for filename, button in self.preview_buttons.items():
            path = self.last_output_dir / "figures" / filename
            if path.exists():
                generated += 1
                button.configure(state="normal")
            else:
                button.configure(state="disabled")
            self._draw_figure_preview(filename)
        if generated == len(FIGURES):
            self.visual_status.set("3/3 图表已由本次 messages.jsonl 自动生成")
        else:
            self.visual_status.set(f"{generated}/3 图表生成完成，请检查运行日志")

    def _open_figure(self, filename: str) -> None:
        path = self.last_output_dir / "figures" / filename
        if self.mission_state == "completed" and path.exists():
            os.startfile(path)  # type: ignore[attr-defined]
        elif self.mission_state == "completed":
            messagebox.showwarning(APP_TITLE, f"未找到图表：\n{path}")

    def _finish_success(self, summary: dict[str, Any]) -> None:
        self._sync_runtime_messages(force=True)
        self.running = False
        self.mission_state = "completed"
        for index in range(len(self.stage_cards)):
            self._set_stage(index, "complete")
        for agent_id in self.agent_states:
            if self.agent_states[agent_id] != "ERROR":
                self.agent_states[agent_id] = "COMPLETE"
        self._refresh_agent_states()
        self._load_completed_conflicts()
        self._set_controls_running(False)
        self.open_proposal_button.configure(state="normal")
        message = (
            f"MISSION COMPLETED // {summary['message_count']} 条消息，"
            f"冲突 {summary['conflicts_resolved']}/{summary['conflicts_detected']} 已解决"
        )
        proposal = self.last_output_dir / "outputs" / "final_proposal.md"
        self.status.set(message)
        self.final_path.set(f"最终申请书：{proposal}")
        self.status_dot.configure(foreground=COLORS["green"])
        self._refresh_metrics()
        self._draw_hero()
        self._append_log(f"\n=== {message} ===\n")
        self.root.after(120, self._refresh_visualizations)
        self.root.update_idletasks()
        messagebox.showinfo(
            APP_TITLE,
            "五阶段多智能体协作已完成。\n\n"
            f"消息：{summary['message_count']}\n"
            f"冲突：{summary['conflicts_resolved']}/{summary['conflicts_detected']} 已解决\n"
            "申请书：已生成\n图表：3 张已生成\n\n"
            "可在工作台中查看消息、冲突和可视化。",
        )

    def _finish_error(self, details: str) -> None:
        self._sync_runtime_messages(force=True)
        self.running = False
        self.mission_state = "failed"
        active = next(
            (
                index for index, state in enumerate(self.stage_states)
                if state in {"active", "conflict"}
            ),
            0,
        )
        self._set_stage(active, "error")
        self.agent_states["coordinator"] = "ERROR"
        self._refresh_agent_states()
        self._set_controls_running(False)
        self.status.set("EXECUTION FAILED // 请查看实时日志中的错误信息")
        self.final_path.set("最终申请书：本次任务未完成")
        self.status_dot.configure(foreground=COLORS["red"])
        self._draw_hero()
        self._append_log(f"\n[ERROR]\n{details}\n")
        messagebox.showerror(APP_TITLE, "运行失败，请查看工作台中的错误日志。")

    def _open_output_dir(self) -> None:
        path = Path(self.output_dir.get().strip() or application_dir())
        if path.exists():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showwarning(APP_TITLE, "结果目录尚不存在。")

    def _open_proposal(self) -> None:
        proposal = self.last_output_dir / "outputs" / "final_proposal.md"
        if proposal.exists():
            os.startfile(proposal)  # type: ignore[attr-defined]
        else:
            messagebox.showwarning(APP_TITLE, "尚未生成最终申请书。")

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno(APP_TITLE, "任务仍在运行，确定关闭吗？"):
            return
        self.root.destroy()


def run_self_test(target: Path) -> None:
    """Exercise the frozen application without opening a window (build verification)."""

    target.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(real=False, mock=True, backend=None, task_dir=target)
    captured = io.StringIO()
    with redirect_stdout(captured), redirect_stderr(captured):
        summary = asyncio.run(async_main(args))
    if summary["message_count"] != 62 or summary["conflicts_resolved"] != 3:
        raise RuntimeError(f"Unexpected self-test summary: {summary}")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        run_self_test(Path(sys.argv[2]).resolve())
        return
    enable_windows_dpi_awareness()
    root = tk.Tk()
    ResearchWritingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
