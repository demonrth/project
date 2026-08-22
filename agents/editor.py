"""Final editorial and terminology-normalization agent."""

from __future__ import annotations

from typing import Any

from agents.base import (
    AgentResult,
    BaseAgent,
    project_requirements_excerpt,
    project_requirements_prompt,
)


class EditorialAgent(BaseAgent):
    agent_id = "editor_agent"
    display_name = "统稿润色智能体"
    system_prompt = (
        "你是基金申请书统稿智能体。保留技术事实与引用，统一术语、编号、标点和"
        "Markdown层级；不得重新引入已解决冲突。"
    )

    async def draft(self, payload: dict[str, Any]) -> AgentResult:
        snapshot = payload["snapshot"]
        resolutions = payload.get("resolutions", [])
        topic = str(payload.get("topic", "") or "科研项目申请书")
        requirements_excerpt = project_requirements_excerpt(payload)
        requirements_section = (
            f"\n## 导入要求摘要\n\n{requirements_excerpt}\n"
            if requirements_excerpt
            else ""
        )
        ordered = [
            snapshot["立项依据与研究现状"]["content"],
            snapshot["研究内容与技术路线"]["content"],
            snapshot["实验方案与预期成果"]["content"],
        ]
        resolution_lines = "\n".join(
            f"- **{item['conflict_id']}**：{item['decision']}" for item in resolutions
        )
        source_text = "\n\n".join(ordered)
        fallback = f"""# {topic}

> 科研项目申请书{' · 根据导入要求生成' if requirements_excerpt else ''}

## 项目摘要

本申请书围绕“{topic}”组织立项依据、研究内容、技术路线、实验方案、评价指标与预期成果。各章节由专职智能体并行起草，经数据与逻辑核查、冲突仲裁和最终统稿后形成。
{requirements_section}

{source_text}

## 11. 一致性核查与冲突解决记录

{resolution_lines}

核查结论：全文统一采用“8 × A100、总训练量不超过960 GPU小时”；SLA违约率降低25%为主要验收目标，降低35%为扩展目标。公式中的四个奖励项均已定义，参考文献序号连续，实验报告随机种子、置信区间和统计检验。

## 12. 项目实施计划

1. **第1—6个月**：完成工作负载清洗、仿真环境与单智能体基线；
2. **第7—18个月**：完成图状态编码、事件触发通信与多目标训练；
3. **第19—30个月**：开展跨规模迁移、故障鲁棒性及消融实验；
4. **第31—36个月**：完成系统集成、开源材料、论文与成果验收。

## 13. 风险控制

- 训练不稳定：采用课程学习、奖励归一化和集中式评论家预训练；
- 通信拥塞：设置事件阈值、摘要压缩和超时降级到本地安全策略；
- 仿真到真实差距：进行多轨迹回放、参数随机化和小规模影子测试；
- API或模型不可用：本系统默认离线确定性后端，真实模型仅作为可替换增强项。
"""
        user_prompt = f"""请合并并润色下面三个已经核查的章节，输出完整 Markdown 申请书。
保留原有技术事实、公式与引用，统一术语、标题层级和章节衔接，不得重新引入已解决冲突。

【已核查章节】
{source_text}

【必须遵守的冲突决议】
{resolution_lines or "无"}
""" + project_requirements_prompt(payload)
        content = await self._generate(
            user_prompt,
            fallback,
        )
        return AgentResult(
            summary="完成全文统稿、术语统一与冲突决议落稿",
            sections={"最终申请书": content},
            data={"word_count": len(content), "resolved_conflicts": len(resolutions)},
        )
