"""Method-design agent."""

from __future__ import annotations

from typing import Any

from agents.base import (
    AgentResult,
    BaseAgent,
    project_requirements_excerpt,
    project_requirements_prompt,
)


class MethodDesignAgent(BaseAgent):
    agent_id = "method_agent"
    display_name = "方法设计智能体"
    system_prompt = (
        "你是方法设计智能体。用形式化符号定义问题，给出可实现的算法模块、训练目标和"
        "技术路线；所有硬件、指标和预算假设必须明确标注。"
    )

    INITIAL = """### 3. 研究目标

构建面向云边异构集群的多智能体强化学习调度框架，在保证SLA约束的同时降低任务完成时间、能耗与通信开销，并验证策略在不同规模集群和故障场景中的迁移能力。

### 4. 关键科学问题

1. 局部观测条件下如何形成兼顾通信代价的协同调度决策；
2. 时延、能耗与SLA约束之间如何进行动态权衡；
3. 面向集群规模变化和节点故障，策略如何保持安全性与泛化能力。

### 5. 研究内容

项目将异构计算集群建模为部分可观测马尔可夫博弈。节点智能体依据本地队列、GPU利用率、网络带宽和任务截止期产生候选调度动作；协调智能体聚合压缩后的局部摘要，并广播全局约束信号。研究内容包括：

1. 面向任务依赖图的局部状态编码与跨节点实体对齐；
2. 带宽受限条件下的事件触发式智能体通信；
3. 同时约束时延、能耗和SLA违约率的多目标策略优化；
4. 面向节点故障与负载漂移的安全降级和策略迁移。

### 6. 研究方法

采用图神经网络、多智能体actor-critic、约束拉格朗日优化和事件触发通信相结合的方法，训练阶段使用全局状态，执行阶段仅使用局部观测和压缩摘要。

### 7. 技术路线

首先，以图神经网络编码任务DAG和资源拓扑；其次，采用参数共享actor生成离散节点选择与连续资源份额，集中式critic在训练阶段使用全局状态；再次，通过拉格朗日乘子动态调整时延、能耗与违约约束。奖励定义为：

`r_t = -(0.45*T_norm + 0.25*E_norm + 0.30*V_sla + 0.05*C_comm)`

其中`T_norm`为归一化完成时间，`E_norm`为能耗，`V_sla`为违约指示，`C_comm`为通信字节数。推理阶段仅交换增量状态摘要，以减少通信开销。初步训练计划基于 **4 × V100** 完成640 GPU小时训练；相对最强启发式基线，主要目标为SLA违约率降低 **25%**。
"""

    REVISED = """### 3. 研究目标

构建面向云边异构集群的多智能体强化学习调度框架，在保证SLA约束的同时降低任务完成时间、能耗与通信开销，并验证策略在不同规模集群和故障场景中的迁移能力。

### 4. 关键科学问题

1. 局部观测条件下如何形成兼顾通信代价的协同调度决策；
2. 时延、能耗与SLA约束之间如何进行动态权衡；
3. 面向集群规模变化和节点故障，策略如何保持安全性与泛化能力。

### 5. 研究内容

项目将异构计算集群建模为部分可观测马尔可夫博弈。节点智能体依据本地队列、GPU利用率、网络带宽和任务截止期产生候选调度动作；协调智能体聚合压缩后的局部摘要，并广播全局约束信号。研究内容包括：

1. 面向任务依赖图的局部状态编码与跨节点实体对齐；
2. 带宽受限条件下的事件触发式智能体通信；
3. 同时约束时延、能耗和SLA违约率的多目标策略优化；
4. 面向节点故障与负载漂移的安全降级和策略迁移。

### 6. 研究方法

采用图神经网络、多智能体actor-critic、约束拉格朗日优化和事件触发通信相结合的方法，训练阶段使用全局状态，执行阶段仅使用局部观测和压缩摘要。

### 7. 技术路线

首先，以图神经网络编码任务DAG和资源拓扑；其次，采用参数共享actor生成离散节点选择与连续资源份额，集中式critic在训练阶段使用全局状态；再次，通过拉格朗日乘子动态调整时延、能耗与违约约束。奖励定义为：

`r_t = -(0.45*T_norm + 0.25*E_norm + 0.30*V_sla + 0.05*C_comm)`

其中`T_norm`为归一化完成时间，`E_norm`为能耗，`V_sla`为违约指示，`C_comm`为通信字节数。推理阶段仅交换增量状态摘要，以减少通信开销。统一后的训练资源为 **8 × A100**，总量不超过960 GPU小时；主要验收目标为SLA违约率降低 **25%**，将降低35%列为扩展目标而非硬性结论。
"""

    async def draft(self, payload: dict[str, Any]) -> AgentResult:
        fallback = self.INITIAL
        if project_requirements_excerpt(payload):
            fallback = f"""### 3. 研究目标

围绕“{payload.get('topic', '')}”建立与导入要求一致的总体目标和可验收分目标。

### 4. 关键科学问题

根据实际任务要求识别研究对象、核心约束、关键变量及尚待解决的科学或工程问题。

### 5. 研究内容

研究内容以用户导入的范围、交付物和边界条件为准，采用分阶段、可验证的模块化设计。

### 6. 研究方法

真实模型恢复后，将依据要求选择适用的理论模型、算法、数据处理和验证方法。

### 7. 技术路线

形成“需求解析—方案设计—系统实现—实验验证—成果交付”的闭环路线。

#### 导入要求摘要

{project_requirements_excerpt(payload)}
"""
        content = await self._generate(
            f"主题：{payload.get('topic')}。编写研究内容与技术路线。"
            f"{project_requirements_prompt(payload)}",
            fallback,
        )
        return AgentResult(
            summary="完成研究内容、算法定义和技术路线初稿",
            sections={"研究内容与技术路线": content},
            data={"hardware": "4 × V100", "gpu_hours": 640, "sla_target": 0.25},
        )

    async def answer_query(self, payload: dict[str, Any]) -> AgentResult:
        conflict_id = payload.get("conflict_id")
        if conflict_id in {"C001", "C006"}:
            fallback = "同意统一为8 × A100；将总训练量限制为960 GPU小时"
        else:
            fallback = "按核查建议修订方法章节，保留已有技术路线"
        position = await self._generate(
            f"你是方法设计智能体，请针对{conflict_id}给出120字以内的可执行协商意见。\n"
            f"证据：{payload.get('evidence', [])}\n建议：{payload.get('question', '')}\n"
            f"当前项目主题：{payload.get('topic', '')}"
            f"{project_requirements_prompt(payload)}",
            fallback,
        )
        return AgentResult(
            summary=f"方法设计智能体提交{conflict_id}协商意见",
            data={"position": position, "conflict_id": conflict_id},
        )

    async def revise(self, payload: dict[str, Any]) -> AgentResult:
        current = payload.get("current_content", "")
        content = await self._generate(
            f"""项目主题：{payload.get('topic', '')}
请在保留方法章节原有研究对象、算法和结构的前提下，仅按冲突决议修订。
冲突决议：{payload.get('resolution', '')}
冲突详情：{payload.get('conflict_details', [])}

【当前方法章节】
{current}

输出修订后的完整方法章节，不得改写成其他项目。"""
            + project_requirements_prompt(payload),
            payload.get("current_content", "")
            if project_requirements_excerpt(payload)
            else self.REVISED,
        )
        return AgentResult(
            summary="按仲裁结果统一硬件与性能目标",
            sections={"研究内容与技术路线": content},
            data={"hardware": "8 × A100", "gpu_hours": 960, "sla_target": 0.25},
        )
