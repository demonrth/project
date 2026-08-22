# 第一题：多智能体通信协议设计与科研协作写作系统

本项目是一个可运行、可审计、可复现的科研协作系统。中央 `Coordinator` 通过星型消息总线和版本化黑板，组织五个专业 Agent 完成基金申请书的并行起草、交叉审查、冲突协商、增量修订、复核与最终统稿。

演示主题为：**基于多智能体强化学习的分布式计算资源调度方法研究**。

## 1. 角色与职责

| 角色 | 实现文件 | 职责 |
|---|---|---|
| Coordinator | `coordinator.py` | 任务分解、消息路由、黑板写入、版本控制、冲突仲裁 |
| LiteratureAgent | `agents/literature_agent.py` | 立项依据、国内外研究现状、真实可核验参考文献 |
| MethodAgent | `agents/method_agent.py` | 研究目标、科学问题、研究内容、方法与技术路线 |
| ExperimentAgent | `agents/experiment_agent.py` | 实验方案、评价指标、统计检验与预期成果 |
| VerificationAgent | `agents/verification_agent.py` | 七类跨章节冲突、公式、引用与逻辑核查 |
| EditorAgent | `agents/editor_agent.py` | 术语与格式统一、章节合并、最终 Markdown |

`agents/*_agent.py` 是课程要求的标准入口，具体实现位于同目录的模块中。

## 2. 正式通信协议

协议实现见 `protocol.py`，机器可读定义见 `schemas/message_schema.json` 和 `schemas/agent_output_schema.json`。每条消息至少包含：

```text
message_id, conversation_id, parent_message_id, message_type,
sender, receiver, timestamp, priority, payload, summary,
status, token_count
```

支持的业务消息类型包括：

```text
TASK_ASSIGN / INFO_REQUEST / RESULT_SUBMIT / CONFLICT_NOTICE / ACK
REVISION_REQUEST / REVISION_SUBMIT / CONFLICT_RESOLUTION / FINAL_RESULT
```

另有 `ERROR` 类型用于显式失败通知。消息还带有 `correlation_id`、`related_message_id`、载荷摘要和幂等 ID，便于完整追踪、回复关联、完整性检查与去重。

### 拓扑与共享黑板

采用“中央协调器 + 共享黑板”的星型拓扑。叶子 Agent 不能直接互发消息，`MessageBus` 会拒绝违规通信；跨 Agent 交流必须由 Coordinator 中转。正文由 Coordinator 根据章节所有权提交到 `DocumentStore`，每个章节有独立锁和单调递增版本号。

乐观并发控制使用 compare-and-swap：若 Agent 基于过期版本提交，系统拒绝写入，读取最新版本后重写并重试。这样同时处理物理写冲突与语义冲突，避免最后写入者静默覆盖他人工作。

## 3. 七类冲突与闭环流程

`VerificationAgent` 支持以下冲突类型：

1. `RESOURCE_MISMATCH`：硬件或资源配置不一致；
2. `NUMBER_MISMATCH`：预算、规模等普通数字不一致；
3. `TERM_MISMATCH`：关键术语不统一；
4. `METHOD_EXPERIMENT_MISMATCH`：方法没有对应实验验证；
5. `METRIC_MISMATCH`：同一指标的验收口径不一致；
6. `REFERENCE_MISMATCH`：引用缺失、重复或序号异常；
7. `TIMELINE_MISMATCH`：计划超出项目周期或前后不一致。

每个冲突对象包含 `conflict_id/type/severity/agents/evidence/description/suggestion/status`。离线演示实际触发三处冲突：GPU 资源、SLA 目标、GPU 小时预算。系统会真实执行：

```text
检测 CONFLICT_NOTICE
  → 询证 INFO_REQUEST
  → 仲裁
  → 修订 REVISION_REQUEST / REVISION_SUBMIT
  → 再次核查
  → 关闭 CONFLICT_RESOLUTION(status=RESOLVED)
```

复核仍有未解决冲突时，不允许进入最终统稿。

## 4. 运行方法

项目不创建虚拟环境；直接使用已有 Python 环境即可。

### Windows 图形界面（无需命令行）

安装依赖后双击 `desktop_app.pyw` 即可启动桌面界面；
如需分发预编译程序，`多智能体科研协作写作系统.exe` 作为附件上传。

### 完整离线演示（推荐评分方式）

```powershell
cd task1
python -m pip install -r requirements.txt
python demo_proposal_writing.py --mock
```

`--mock` 不需要网络或 API Key，但仍会执行全部消息、并行、冲突和修订流程，并从真实 JSONL 日志生成图表。

### 真实模型模式

复制 `.env.example` 为 `.env`，填写 OpenAI、DeepSeek 或其他 OpenAI-compatible 服务之一，然后运行：

```powershell
python demo_proposal_writing.py --real
```

如需按实际题目或申请指南生成，可在桌面界面的 Real 模式点击“导入要求文件”，也可使用命令行：

```powershell
python demo_proposal_writing.py --real --requirements "G:\资料\项目要求.pdf"
```

系统会从要求中推断项目名称，把完整要求作为统一上下文传给文献、方法、实验、核查和统稿智能体，并在结果目录的 `inputs/` 中保存规范化文本与哈希元数据，便于复核本次申请书使用的输入依据。扫描版 PDF 需要先完成 OCR。

密钥只从环境变量或 `.env` 读取，不写入代码和日志；`.env` 已加入忽略列表。真实接口失败时会指数退避重试两次，仍失败则单个 Agent 使用内置确定性文本降级，整体流程继续执行。

### 自动测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖协议往返校验、幂等去重、拓扑约束、章节所有权、过期版本拒绝与重试、七类冲突声明、模型失败降级，以及完整“冲突—修订—复核—统稿”流程。

## 5. 输出文件

每次运行会生成或覆盖：

```text
outputs/final_proposal.md             最终基金申请书（10个核心章节）
logs/messages.jsonl                   完整逐条协议消息
logs/run_log.json                     每个Agent的开始/结束/耗时/模型/token/错误/重试
logs/console_output.txt                最近一次标准运行的可读终端输出
logs/blackboard_snapshot.json          task、sections、agent_results、conflicts、messages
logs/conflicts_and_resolutions.json    初检、仲裁、复核和关闭状态
logs/communication_metrics.json        按Agent和消息类型统计消息数与token
workspace/*.json                       各Agent提交记录
figures/sequence_diagram.png           由真实消息日志生成的时序图
figures/communication_load.png         各Agent通信负载
figures/message_type_distribution.png  消息类型分布
```

为兼容原题文件名，系统同时保留 `outputs/proposal.md`、`logs/run_log.jsonl`、`protocol.schema.json` 和若干快照文件。

最近一次 `--mock` 标准运行结果：62 条消息、17,346 token、3/3 冲突解决，九种业务消息均有实际记录；Literature、Method、Experiment 三个初稿任务在 `run_log.json` 中存在时间区间重叠，证明不是伪并行。

## 6. 通信开销与优化

总通信开销按日志中的消息 token 估算：

```text
C = Σ token_count = N × L_avg
```

优化策略包括摘要通信、修订增量传输、正文放入黑板而非反复随消息传输、幂等去重，以及只在发现冲突后启动协商分支。统计同时按发送 Agent 与消息类型展开，便于定位协调器瓶颈。

## 7. GitHub 项目参考

- [A2A](https://github.com/a2aproject/A2A)：结构化任务消息、关联 ID 和异步交互理念。
- [LangGraph](https://github.com/langchain-ai/langgraph)：持久状态、条件分支与失败恢复理念。
- [STORM](https://github.com/stanford-oval/storm)：检索、大纲和长报告协作写作流程。
- [PaperOrchestra](https://github.com/google-research/paper-orchestra)：科研写作专业 Agent 分工。
- [Agent Laboratory](https://github.com/SamuelSchmidgall/AgentLaboratory)：文献—实验—报告的科研工作流。

