"""Experiment-planning agent."""

from __future__ import annotations

from typing import Any

from agents.base import (
    AgentResult,
    BaseAgent,
    project_requirements_excerpt,
    project_requirements_prompt,
)


class ExperimentPlanningAgent(BaseAgent):
    agent_id = "experiment_agent"
    display_name = "实验规划智能体"
    system_prompt = (
        "你是实验规划智能体。设计数据、基线、消融、统计检验、算力预算和验收标准，"
        "确保每个指标可计算、每个结论可复现。"
    )

    INITIAL = """### 8. 实验方案

实验采用公开工作负载与可控仿真相结合：以Alibaba Cluster Trace和Google Cluster Workload Traces构造到达序列，在SimPy环境中复现异构GPU、网络拥塞和节点故障。对比方法包括FIFO、最短作业优先、DRF、DeepRM、Decima、独立PPO与MAPPO。

### 9. 评价指标

评估指标包括平均作业完成时间、P95尾时延、SLA违约率、GPU利用率、单位任务能耗、通信字节数与故障恢复时间。每组实验使用5个随机种子，报告均值、95%置信区间和配对bootstrap检验。消融实验分别移除图编码、事件触发通信、约束拉格朗日项和安全降级模块。

计划使用 **8 × A100** 运行120小时，即960 GPU小时。主要验收口径暂定为：相对最强基线SLA违约率降低 **35%**，P95时延降低15%，通信量不超过全量广播的30%。

### 10. 预期成果

形成一套可复现的分布式资源调度原型、一个带故障与负载漂移的评测基准、2篇学术论文和1项软件著作权；开源训练配置、匿名化日志和指标计算脚本。
"""

    REVISED = """### 8. 实验方案

实验采用公开工作负载与可控仿真相结合：以Alibaba Cluster Trace和Google Cluster Workload Traces构造到达序列，在SimPy环境中复现异构GPU、网络拥塞和节点故障。对比方法包括FIFO、最短作业优先、DRF、DeepRM、Decima、独立PPO与MAPPO。

### 9. 评价指标

评估指标包括平均作业完成时间、P95尾时延、SLA违约率、GPU利用率、单位任务能耗、通信字节数与故障恢复时间。每组实验使用5个随机种子，报告均值、95%置信区间和配对bootstrap检验。消融实验分别移除图编码、事件触发通信、约束拉格朗日项和安全降级模块。

统一使用 **8 × A100**，以960 GPU小时为预算上限。主要验收口径调整为：相对最强基线SLA违约率降低 **25%**，P95时延降低15%，通信量不超过全量广播的30%；SLA违约率降低35%作为扩展目标单独报告，不作为必达结论。

### 10. 预期成果

形成一套可复现的分布式资源调度原型、一个带故障与负载漂移的评测基准、2篇学术论文和1项软件著作权；开源训练配置、匿名化日志和指标计算脚本。
"""

    async def draft(self, payload: dict[str, Any]) -> AgentResult:
        fallback = self.INITIAL
        if project_requirements_excerpt(payload):
            fallback = f"""### 8. 实验方案

针对“{payload.get('topic', '')}”设计与实际场景一致的数据、对照方案、实施步骤和复现实验。

### 9. 评价指标

评价指标将从导入要求中的定量目标、质量约束、资源预算与验收方式中提取，并明确计算口径、样本规模和统计方法。

### 10. 预期成果

成果形式、完成时间和交付标准严格以导入要求为准；模型恢复后补充完整实验参数与风险预案。

#### 导入要求摘要

{project_requirements_excerpt(payload)}
"""
        content = await self._generate(
            f"主题：{payload.get('topic')}。编写实验方案与预期成果。"
            f"{project_requirements_prompt(payload)}",
            fallback,
        )
        return AgentResult(
            summary="完成实验、评估指标、预算与预期成果初稿",
            sections={"实验方案与预期成果": content},
            data={"hardware": "8 × A100", "gpu_hours": 960, "sla_target": 0.35},
        )

    async def answer_query(self, payload: dict[str, Any]) -> AgentResult:
        conflict_id = payload.get("conflict_id")
        if conflict_id == "C001":
            fallback = "A100是当前可用集群，建议方法章节改为8 × A100"
        elif conflict_id == "C006":
            fallback = "实验预算已按可用机时核定，建议统一为960 GPU小时"
        else:
            fallback = "按核查建议修订实验章节，保留已有数据和评估设计"
        position = await self._generate(
            f"你是实验规划智能体，请针对{conflict_id}给出120字以内的可执行协商意见。\n"
            f"证据：{payload.get('evidence', [])}\n建议：{payload.get('question', '')}\n"
            f"当前项目主题：{payload.get('topic', '')}"
            f"{project_requirements_prompt(payload)}",
            fallback,
        )
        return AgentResult(
            summary=f"实验规划智能体提交{conflict_id}协商意见",
            data={"position": position, "conflict_id": conflict_id},
        )

    async def revise(self, payload: dict[str, Any]) -> AgentResult:
        current = payload.get("current_content", "")
        content = await self._generate(
            f"""项目主题：{payload.get('topic', '')}
请在保留实验章节原有研究对象、数据集、基线和指标的前提下，仅按冲突决议修订。
冲突决议：{payload.get('resolution', '')}
冲突详情：{payload.get('conflict_details', [])}

【当前实验章节】
{current}

输出修订后的完整实验章节，不得改写成其他项目。"""
            + project_requirements_prompt(payload),
            payload.get("current_content", "")
            if project_requirements_excerpt(payload)
            else self.REVISED,
        )
        return AgentResult(
            summary="按仲裁结果统一算力预算与验收口径",
            sections={"实验方案与预期成果": content},
            data={"hardware": "8 × A100", "gpu_hours": 960, "sla_target": 0.25},
        )
