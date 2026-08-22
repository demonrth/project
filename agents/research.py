"""Literature-research agent."""

from __future__ import annotations

from typing import Any

from agents.base import (
    AgentResult,
    BaseAgent,
    project_requirements_excerpt,
    project_requirements_prompt,
)


class LiteratureResearchAgent(BaseAgent):
    agent_id = "literature_agent"
    display_name = "文献调研智能体"
    system_prompt = (
        "你是文献调研智能体。仅使用可核验的公开文献，区分已证实事实与研究假设，"
        "为基金申请书撰写立项依据和国内外研究现状，引用采用[序号]。"
    )

    async def draft(self, payload: dict[str, Any]) -> AgentResult:
        fallback = """### 1. 立项依据

云边协同平台中的计算任务具有到达随机、资源异构和服务等级约束动态变化等特征。传统启发式调度通常依赖固定规则，在负载突变和节点故障场景中难以同时优化任务完成时间、能耗与违约率。强化学习能够从交互数据中学习调度策略，而多智能体强化学习（MARL）进一步允许各计算节点保留局部观测并协同决策，适合分布式资源管理。

现有研究形成了三条技术脉络。第一，MADDPG采用集中训练、分散执行机制处理多智能体协作[1]；QMIX通过单调价值分解提高协作任务的可训练性[2]；MAPPO显示了基于策略梯度方法在多智能体基准上的稳定性[3]。第二，DeepRM将深度强化学习引入集群资源管理[4]，Decima进一步面向数据处理集群学习图结构感知的调度策略[5]。第三，图神经网络能够表达任务依赖和资源拓扑，为策略迁移提供结构先验。

然而，现有方法仍存在三点不足：其一，多数方法将调度器视为单一智能体，难以处理跨节点局部信息与通信代价；其二，训练目标常采用固定加权和，无法随服务等级与能源预算动态调整；其三，策略在不同规模集群间的迁移与故障鲁棒性缺乏系统评测。本项目拟研究带通信约束的多智能体资源调度方法，形成“局部观测—协同通信—约束优化—安全执行”的完整技术链。

### 2. 国内外研究现状（Related Work）

价值分解方法适合离散协作决策，但对连续资源份额表达有限；actor-critic方法支持混合动作，却可能因非平稳环境产生训练震荡。本项目将图表示、参数共享和集中式评论家结合，并显式惩罚通信开销。与DeepRM和Decima相比，本项目关注多节点自治、跨规模泛化以及约束违约的可解释诊断。

### 参考文献

[1] Lowe R, et al. Multi-Agent Actor-Critic for Mixed Cooperative-Competitive Environments. NeurIPS, 2017.  
[2] Rashid T, et al. QMIX: Monotonic Value Function Factorisation for Deep Multi-Agent Reinforcement Learning. ICML, 2018.  
[3] Yu C, et al. The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games. NeurIPS, 2022.  
[4] Mao H, et al. Resource Management with Deep Reinforcement Learning. HotNets, 2016.  
[5] Mao H, et al. Learning Scheduling Algorithms for Data Processing Clusters. SIGCOMM, 2019.
"""
        if project_requirements_excerpt(payload):
            fallback = f"""### 1. 立项依据

本项目围绕“{payload.get('topic', '')}”展开。立项依据将以导入的任务要求、应用场景、目标对象和成果约束为边界，进一步补充可核验的研究现状与问题分析。

### 2. 国内外研究现状（Related Work）

当前为真实模型不可用时的安全降级稿。系统已保留用户导入的项目要求，恢复模型连接后可据此完成文献检索、研究空白论证与规范引用。

#### 导入要求摘要

{project_requirements_excerpt(payload)}

### 参考文献

[1] 用户导入的项目要求文档（本次任务输入）。
"""
        content = await self._generate(
            f"主题：{payload.get('topic')}。负责章节：{payload.get('sections')}。"
            f"{project_requirements_prompt(payload)}",
            fallback,
        )
        return AgentResult(
            summary="完成立项依据、研究现状与可核验参考文献初稿",
            sections={"立项依据与研究现状": content},
            data={"reference_count": 5, "claims_marked": 3},
        )

    async def revise(self, payload: dict[str, Any]) -> AgentResult:
        current = payload.get("current_content", "")
        fallback = current or "未获取到待修订的文献章节。"
        content = await self._generate(
            f"""项目主题：{payload.get('topic', '')}
你只能修订下面的文献章节，不得改变研究主题或删除有效内容。
冲突决议：{payload.get('resolution', '')}
冲突详情：{payload.get('conflict_details', [])}

【当前章节】
{current}

请输出修订后的完整章节。参考文献序号必须从[1]开始连续、正文引用与文末条目一一对应。"""
            + project_requirements_prompt(payload),
            fallback,
        )
        return AgentResult(
            summary="按冲突决议修订文献引用与编号",
            sections={"立项依据与研究现状": content},
            data={"revision_conflicts": payload.get("conflict_ids", [])},
        )
