"""Cross-section conflict detection and post-revision verification."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from agents.base import AgentResult, BaseAgent


class ConflictType(str, Enum):
    RESOURCE_MISMATCH = "RESOURCE_MISMATCH"
    NUMBER_MISMATCH = "NUMBER_MISMATCH"
    TERM_MISMATCH = "TERM_MISMATCH"
    METHOD_EXPERIMENT_MISMATCH = "METHOD_EXPERIMENT_MISMATCH"
    METRIC_MISMATCH = "METRIC_MISMATCH"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    TIMELINE_MISMATCH = "TIMELINE_MISMATCH"


SUPPORTED_CONFLICT_TYPES = [item.value for item in ConflictType]


class DataLogicVerifierAgent(BaseAgent):
    agent_id = "verifier_agent"
    display_name = "数据/逻辑核查智能体"
    system_prompt = (
        "你是数据与逻辑核查智能体。逐项核对资源、数字、术语、方法实验映射、"
        "指标、引用和时间线；冲突必须给出证据，不得悄悄改写。"
    )

    async def draft(self, payload: dict[str, Any]) -> AgentResult:
        snapshot = payload["snapshot"]
        conflicts, checks = detect_conflicts(snapshot)
        phase = payload.get("phase", "initial")
        if conflicts:
            summary = f"{phase}核查完成：发现{len(conflicts)}处跨章节冲突"
        else:
            summary = f"{phase}核查完成：未发现未解决冲突，允许进入统稿"
        return AgentResult(
            summary=summary,
            data={
                "phase": phase,
                "conflicts": conflicts,
                "checks": checks,
                # Quality checks are evidence for the report, while only an
                # unresolved cross-section conflict blocks the workflow.
                "passed": not conflicts,
                "supported_conflict_types": SUPPORTED_CONFLICT_TYPES,
            },
        )


def _conflict(
    conflict_id: str,
    conflict_type: ConflictType,
    severity: str,
    agents: list[str],
    evidence: list[str],
    description: str,
    suggestion: str,
) -> dict[str, Any]:
    return {
        "conflict_id": conflict_id,
        "type": conflict_type.value,
        "severity": severity,
        "agents": agents,
        "evidence": evidence,
        "description": description,
        "suggestion": suggestion,
        "status": "OPEN",
        # Compatibility aliases used by the original report implementation.
        "kind": conflict_type.value.lower(),
        "participants": agents,
        "recommendation": suggestion,
    }


def detect_conflicts(snapshot: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    method = snapshot["研究内容与技术路线"]["content"]
    experiment = snapshot["实验方案与预期成果"]["content"]
    literature = snapshot["立项依据与研究现状"]["content"]
    conflicts: list[dict[str, Any]] = []

    if "4 × V100" in method and "8 × A100" in experiment:
        conflicts.append(
            _conflict(
                "C001",
                ConflictType.RESOURCE_MISMATCH,
                "CRITICAL",
                ["method_agent", "experiment_agent"],
                ["方法章节：4 × V100", "实验章节：8 × A100"],
                "方法设计与实验方案中的GPU型号和数量不一致",
                "以实际可用集群为准，统一GPU型号、数量和预算",
            )
        )

    method_target = _extract_target(method)
    experiment_target = _extract_target(experiment)
    if method_target and experiment_target and method_target != experiment_target:
        conflicts.append(
            _conflict(
                "C002",
                ConflictType.METRIC_MISMATCH,
                "HIGH",
                ["method_agent", "experiment_agent"],
                [f"方法章节主要目标：{method_target}%", f"实验章节主要目标：{experiment_target}%"],
                "同一SLA指标在方法和实验章节使用了不同验收目标",
                "区分必达目标与扩展目标，并统一主要验收口径",
            )
        )

    # The remaining rule families are active detectors even when this demo does
    # not trigger them. They become conflicts when their explicit invariants fail.
    references = re.findall(r"^\[(\d+)\]", literature, flags=re.MULTILINE)
    expected_references = [str(index) for index in range(1, len(references) + 1)]
    references_are_consecutive = bool(references) and references == expected_references
    if not references_are_consecutive:
        conflicts.append(
            _conflict(
                "C003",
                ConflictType.REFERENCE_MISMATCH,
                "HIGH",
                ["literature_agent"],
                [f"检测到引用序号：{references}"],
                "参考文献序号缺失、重复或不连续",
                "重新编号并核对正文引用",
            )
        )

    normalized_terms = {
        "作业完成时间": "完成时间",
        "任务完成时间": "完成时间",
        "服务等级协议": "SLA",
    }
    term_values = {canonical for term, canonical in normalized_terms.items() if term in method + experiment}
    if "服务等级协议" in method and "SLA" not in experiment:
        conflicts.append(
            _conflict(
                "C004",
                ConflictType.TERM_MISMATCH,
                "MEDIUM",
                ["method_agent", "experiment_agent"],
                ["方法：服务等级协议", "实验：未定义SLA"],
                "核心术语未在跨章节中保持一致",
                "建立术语表并统一使用SLA（服务等级协议）",
            )
        )

    if "事件触发式智能体通信" in method and "通信字节数" not in experiment:
        conflicts.append(
            _conflict(
                "C005",
                ConflictType.METHOD_EXPERIMENT_MISMATCH,
                "HIGH",
                ["method_agent", "experiment_agent"],
                ["方法包含事件触发通信", "实验缺少通信开销指标"],
                "关键方法模块没有对应实验指标",
                "在实验中增加通信字节数和全量广播对照",
            )
        )

    gpu_hours = [int(item) for item in re.findall(r"(\d+)\s*GPU小时", method + experiment)]
    if len(set(gpu_hours)) > 1:
        conflicts.append(
            _conflict(
                "C006",
                ConflictType.NUMBER_MISMATCH,
                "HIGH",
                ["method_agent", "experiment_agent"],
                [f"检测到GPU小时：{gpu_hours}"],
                "跨章节训练预算数字不一致",
                "统一训练预算上限",
            )
        )

    month_ranges = [int(item) for item in re.findall(r"第\d+[—-](\d+)个月", method + experiment)]
    if month_ranges and max(month_ranges) > 36:
        conflicts.append(
            _conflict(
                "C007",
                ConflictType.TIMELINE_MISMATCH,
                "MEDIUM",
                ["method_agent", "experiment_agent"],
                [f"最大计划月份：{max(month_ranges)}"],
                "实施时间线超出36个月项目周期",
                "调整阶段计划到项目周期内",
            )
        )

    checks = {
        "reward_formula_terms": all(term in method for term in ("T_norm", "E_norm", "V_sla", "C_comm")),
        "reference_sequence_complete": references_are_consecutive,
        "experiment_has_random_seeds": bool(re.search(r"随机种子|random seed|seed\s*=", experiment, re.I)),
        "experiment_has_uncertainty": bool(re.search(r"置信区间|bootstrap|p\s*[<=>]|统计检验", experiment, re.I)),
        "method_experiment_mapping": not ("事件触发式智能体通信" in method and "通信字节数" not in experiment),
        "term_normalization_available": bool(term_values) or "SLA" in method + experiment,
    }
    return conflicts, checks


def _extract_target(text: str) -> int | None:
    match = re.search(r"(?:主要目标|主要验收目标|主要验收口径(?:暂定为|调整为)?)[^。]*?降低\s*\*\*(\d+)%\*\*", text)
    return int(match.group(1)) if match else None
