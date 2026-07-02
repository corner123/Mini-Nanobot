# Mini-Nanobot 项目剖析与面试准备

这份文档是给自己看的项目说明，用来快速掌握 Mini-Nanobot 的设计动机、代码结构、核心流程和面试讲法。公开 README 只讲项目是什么；这份文档讲“为什么这么做、怎么做、面试会怎么问”。

## 1. 项目一句话

Mini-Nanobot 是一个面向代码任务的轻量级 Agent 框架。它不是简单的 prompt wrapper，而是把代码 Agent 拆成会话管理、查询循环、工具协议、安全执行、上下文压缩、状态恢复和记忆系统几层，解决多步骤工程任务里的上下文溢出、工具调用不稳定、执行失败后难恢复等问题。

面试时可以这样开场：

> 我做的是一个轻量级代码 Agent runtime。核心不是把 LLM 接上几个工具，而是实现一套可恢复、可扩展、可控副作用的执行框架：外层 QueryEngine 管会话生命周期，内层 query loop 管 ReAct 执行，工具系统统一封装真实副作用，ContextCompressor 控制 token 增长，SQLite checkpoint 支持中断恢复，Memory 系统支持跨会话偏好和项目决策复用。

## 2. 技术栈

- Python 3.11+
- ReAct 执行范式
- LangGraph 可选适配：`mini_nanobot/core/graph.py`
- JSON Schema 工具协议
- tiktoken token 统计，缺失时用字符估算兜底
- SQLite checkpoint
- asyncio 异步执行
- Shell sandbox policy
- Docker sandbox 镜像草案
- pytest 测试
- OpenAI provider 可选接入
- MCP adapter 预留外部工具接入点

## 3. 总体架构

核心分层：

```text
User Task
  -> QueryEngine 外层会话
  -> 动态上下文注入：system prompt / memory / skills
  -> query() 内层 ReAct loop
  -> LLMProvider 决策
  -> ToolRegistry 查找工具
  -> StreamingToolExecutor 执行工具
  -> ToolResult 注入消息
  -> ContextCompressor 检查上下文
  -> SQLiteCheckpointStore 保存状态
  -> final response / resume
```

关键代码：

- `mini_nanobot/core/query_engine.py`：外层会话生命周期。
- `mini_nanobot/core/query.py`：内层单次查询循环。
- `mini_nanobot/core/state.py`：`AgentState`、`Message`、`ToolCall`、`Usage`。
- `mini_nanobot/tools/base.py`：工具接口和权限模型。
- `mini_nanobot/tools/executor.py`：工具执行器。
- `mini_nanobot/context/compressor.py`：上下文压缩。
- `mini_nanobot/memory/checkpoint.py`：状态恢复。
- `mini_nanobot/memory/long_term.py`：长期记忆。
- `mini_nanobot/sandbox/policy.py`：Shell 安全策略。

## 4. 双层 Query 设计

这个项目最重要的工程切分是双层结构：

```text
QueryEngine.submit_message()
  负责一轮用户交互的生命周期：
  - 创建或恢复 AgentState
  - 构建 system prompt
  - 注入 memory 和 skills
  - 设置权限、工具、checkpoint、compressor
  - 调用 query()

query()
  负责单次 ReAct 执行循环：
  - 压缩上下文
  - 调 LLMProvider
  - 解析 tool_calls
  - 执行工具
  - 注入 tool result
  - 保存 checkpoint
  - 判断继续或停止
```

为什么要拆两层：

- 外层关注“会话”：用户任务、session id、记忆、技能、权限和恢复。
- 内层关注“执行”：模型输出、工具调用、工具结果、压缩和循环退出。
- 这样测试更容易，后续接 LangGraph 或多 Agent 也不会污染会话层。

面试追问：

问：为什么不用一个大 Agent 类全包？

答：大类会把 session lifecycle、prompt 组装、tool execution 和 compression 混在一起，后续很难测试和恢复。拆成 QueryEngine/query 后，QueryEngine 可以持久化和恢复状态，query 可以作为状态机节点反复执行，天然适合 LangGraph 或 checkpoint。

## 5. ReAct Loop 如何工作

执行流程在 `mini_nanobot/core/query.py`：

1. 每轮开始先调用 `compressor.compress_if_needed(state)`。
2. 调用 `llm.generate(messages, tools, state)`。
3. 如果 LLM 返回纯文本，认为任务完成，保存 final response。
4. 如果 LLM 返回 tool calls，把 tool call 作为 assistant 消息写入状态。
5. 用 `StreamingToolExecutor.execute_many()` 执行工具。
6. 每个工具结果作为 `role="tool"` 的消息写回上下文。
7. 每轮工具执行后保存 checkpoint。
8. 继续下一轮，直到完成或超过 `max_turns`。

设计点：

- 工具错误不会抛到进程顶层，而是变成 `ToolResult(is_error=True)` 注入上下文。
- 模型可以看到错误并自我修正。
- 每轮关键节点都 checkpoint，失败后能 resume。

## 6. LLMProvider 抽象

代码在 `mini_nanobot/llm/base.py`。

核心接口：

```python
class LLMProvider:
    async def generate(messages, tools, state) -> LLMResponse:
        ...
```

当前有三个实现：

- `RuleBasedLLM`：离线演示用，不依赖 API key。
- `ScriptedLLM`：测试用，按脚本返回 tool calls。
- `OpenAIProvider`：可选真实模型接入。

为什么要保留离线模型：

- 面试或演示时不依赖外部 API。
- 测试稳定，不受网络和模型随机性影响。
- 证明框架逻辑和模型能力解耦。

面试追问：

问：接真实模型后会怎么改？

答：只需要新增一个 LLMProvider，把框架内 `Tool` 的 JSON Schema 转成目标 API 的 tool schema，然后把模型返回的 function/tool calls 映射为 `ToolCall`。Agent loop、工具执行、安全策略和 checkpoint 都不用改。

## 7. Tool 系统

工具接口在 `mini_nanobot/tools/base.py`。

一个工具包含：

- `name`
- `description`
- `input_schema`
- `output_schema`
- `max_result_size_chars`
- `is_read_only()`
- `is_destructive()`
- `is_concurrency_safe()`
- `validate_input()`
- `check_permissions()`
- `run()`

设计原则：

- 所有真实副作用都必须经过 Tool。
- 工具自己声明安全语义。
- 工具输入用 JSON Schema 描述，方便给 LLM 和 MCP 使用。
- 工具错误以数据返回，不让 Agent 进程崩溃。

已实现工具：

- `file.read`
- `file.write`
- `file.patch`
- `file.list`
- `search.rg`
- `git.status`
- `git.diff`
- `git.show`
- `git.log`
- `shell.run`
- `skill.load`
- `agent.run`

面试追问：

问：新增一个工具需要改哪些地方？

答：继承 `Tool`，定义 schema、权限逻辑和 `run()`，然后注册到 `ToolRegistry`。执行器、权限系统、LLM schema 输出都不需要改。

## 8. StreamingToolExecutor

代码在 `mini_nanobot/tools/executor.py`。

主要能力：

- 调用前执行 `PreToolUse` hooks。
- 做 schema 校验。
- 做权限检查。
- 执行工具。
- 大输出落盘并在上下文里保留 preview + artifact path。
- 执行后触发 `PostToolUse` hooks。
- 只读工具可以并发，写工具串行。

并发规则：

- `tool.is_concurrency_safe(args)` 为 true 的工具可以进入并发 batch。
- 非并发安全工具会先等待已有 batch 完成，再独占执行。
- 默认并发上限是 4。

为什么这么做：

- 文件读、搜索这类只读工具通常可以并发。
- 写文件、shell 命令、git mutation 可能有顺序依赖，不能乱并发。
- 这样能提升 I/O 型任务速度，同时降低副作用风险。

## 9. Shell Sandbox 与权限模型

权限定义在 `PermissionLevel`：

```text
READ_ONLY
WRITE_WORKSPACE
EXECUTE_SAFE
GIT_MUTATE
DANGEROUS
```

Shell 安全由两层实现：

- `sandbox/policy.py`：命令安全检查。
- `sandbox/executor.py`：执行时超时、输出限制、环境变量脱敏。

已做的安全控制：

- 拦截明显危险命令，如 `rm -rf /`、`format`、`diskpart`、`mkfs`、fork bomb、`sudo`。
- 识别破坏性命令，如 `rm`、`del`、`git reset`、`git clean`。
- 非只读命令需要 `EXECUTE_SAFE`。
- 破坏性命令需要 `DANGEROUS`。
- 执行 cwd 固定在 workspace。
- 环境变量中包含 `TOKEN / SECRET / PASSWORD / KEY` 的项会被剥离。
- 超时返回 exit code 124。

面试追问：

问：正则过滤 shell 命令安全吗？

答：不完全安全。这个项目把它定位成第一道本地防线，不是最终隔离。真正生产级需要 OS sandbox、container、seccomp、权限用户、网络隔离和文件系统 mount 限制。项目里也预留了 Docker sandbox 镜像。

## 10. 文件安全和工作区隔离

`ToolContext.resolve_workspace_path()` 会把用户传入路径解析成绝对路径，并检查它是否还在 workspace 内。

作用：

- 防止 `../../` 路径穿越。
- 防止 Agent 写到项目外部。
- 所有文件工具都复用这一个入口。

面试追问：

问：为什么路径校验放 ToolContext，而不是每个工具自己写？

答：路径安全是横切能力，集中在 ToolContext 能减少重复和遗漏。每个工具只管业务逻辑。

## 11. 上下文压缩

代码在 `mini_nanobot/context/compressor.py`。

核心目标：

- 防止多轮工具调用导致上下文无限增长。
- 优先用低成本方式减少 token。
- 最后才做更激进的摘要压缩。

压缩流水线：

1. `history_snip`：过长 tool message 保留 head/tail。
2. `microcompact`：旧工具结果替换成占位提示。
3. `context_collapse`：把早期消息折叠成摘要。
4. `autocompact`：最终兜底摘要，并注入恢复信息。

触发阈值：

- `history_snip_threshold`: 默认 72%。
- `collapse_threshold`: 默认 90%。
- `autocompact_threshold`: 默认 93%。

摘要保留内容：

- 原始任务。
- 当前 plan。
- 最近消息。
- 最近文件。
- 最近技能。

面试追问：

问：为什么工具大输出不直接截断？

答：直接截断会永久丢信息。项目里先把大输出写入 artifact，再把 preview 和路径放进上下文。模型如果后续需要完整内容，可以再用文件工具读取。

## 12. Token 统计

代码在 `mini_nanobot/context/tokenizer.py`。

策略：

- 优先使用 `tiktoken`。
- 如果环境没有 tiktoken 或模型编码不可用，退化为 `len(text) // 4` 估算。

面试追问：

问：为什么 token 统计可以估算？

答：压缩触发本身是保护性策略，不需要精确到每个 token。真实 API 返回 usage 后可以把 usage 作为更准的锚点；本项目的抽象保留了 `Usage`，后续可以接入真实模型 usage。

## 13. Checkpoint 恢复

代码在 `mini_nanobot/memory/checkpoint.py`。

保存内容：

- `session_id`
- `task`
- `state_json`
- `completed`
- `created_at`
- `updated_at`

保存时机：

- tool calls 写入状态后保存。
- tool results 写入状态后保存。
- final response 后保存。
- max_turns 停止时保存。

恢复方式：

```bash
python -m mini_nanobot sessions
python -m mini_nanobot resume <session_id>
```

细节：

- `AgentState.metadata` 里可能有运行时对象，比如 `SkillManager`。
- 序列化时会过滤掉不能 JSON dump 的字段，避免 checkpoint 写坏。

面试追问：

问：如果工具执行到一半进程崩了怎么办？

答：当前实现保存的是工具 batch 前后的状态。单个工具内部执行到一半崩溃时，最多重跑这一轮工具。生产增强可以为 tool call 增加 `started/running/done` 状态和幂等 key，恢复时判断是否需要补偿。

## 14. Memory 系统

代码在 `mini_nanobot/memory/long_term.py`。

长期记忆四类：

- `user`：用户偏好和背景。
- `feedback`：用户对 Agent 行为的纠正。
- `project`：项目动态和决策。
- `reference`：外部系统指针。

存储方式：

- 每条 memory 是带 frontmatter 的 Markdown。
- 自动维护 `MEMORY.md` 索引。
- 会话开始时注入索引。
- 根据 query 做简单语义召回。

召回安全：

- 注入时提示模型“memory 只是 hint，需要验证”。
- 超过 1 天的 memory 会带 freshness warning。

面试追问：

问：为什么不把代码逻辑也写进 memory？

答：代码逻辑可以从当前仓库读取，写进长期 memory 反而容易过期。Memory 应该只存无法从项目状态推导出的信息，比如用户偏好、架构决策、外部 issue 地址。

## 15. Skill 系统

代码在 `mini_nanobot/skills/loader.py` 和 `mini_nanobot/skills/tool.py`。

设计：

- 启动时只发现 `SKILL.md` 的元数据。
- 通过 `<system-reminder>` 注入技能菜单。
- 只有模型调用 `skill.load` 时才读取完整技能内容。

为什么懒加载：

- 技能很多时，全量注入会占大量 token。
- 大多数任务只需要少量技能。
- 元数据足够让模型判断何时调用。

面试追问：

问：技能和工具有什么区别？

答：工具是可执行能力，直接产生副作用或返回数据；技能是提示词工作流，是“如何做某类任务”的说明。技能可以指导模型更好地使用工具。

## 16. Hook 系统

代码在 `mini_nanobot/hooks/manager.py`。

事件：

- `PreToolUse`
- `PostToolUse`
- `SessionStart`
- `SessionEnd`
- `CompactStart`
- `CompactEnd`

用途：

- 工具执行前拦截或修改参数。
- 工具执行后审计日志。
- 会话开始加载环境。
- 压缩前后统计 token。
- 后续支持企业策略或项目特定规则。

面试追问：

问：Hook 为什么不要写死到工具里？

答：Hook 是扩展点，适合横切逻辑。比如审计、权限增强、指标上报不属于某个工具的核心逻辑，写成 Hook 能避免侵入工具实现。

## 17. AgentTool 与多 Agent 扩展

代码在 `mini_nanobot/tools/agent.py`。

当前实现：

- 定义 `AgentDefinition`。
- 暴露 `agent.run` 工具。
- 默认禁止递归 fork。
- 如果没有配置 `fork_runner`，返回集成提示。

为什么不直接完整实现多 Agent：

- MVP 重点是单 Agent runtime 的可恢复、可压缩、可控工具执行。
- 多 Agent 需要任务分解、通信协议、隔离工作区、结果聚合，复杂度更高。
- 项目先把 AgentTool 作为扩展点保留，面试可以讲后续演进路线。

面试追问：

问：为什么禁止递归 fork？

答：递归 fork 会导致资源指数级增长、结果聚合困难、调试复杂、上下文隔离混乱。MVP 只允许一层 fork，更可控。

## 18. LangGraph 适配

代码在 `mini_nanobot/core/graph.py`。

当前策略：

- 如果环境安装了 LangGraph，返回一个简单 StateGraph。
- 如果没有安装，返回 `GraphSpec`，不影响主框架运行。

为什么这么做：

- 项目主要逻辑不强依赖 LangGraph。
- LangGraph 适合作为编排层，把 query loop 的步骤节点化。
- 框架先保持轻量，后续可把 `prepare_context / llm_decision / execute_tools / checkpoint` 拆成真正节点。

面试追问：

问：LangGraph 在这个项目里真正价值是什么？

答：价值是状态机和 checkpoint。代码任务 Agent 不是一次 LLM 调用，而是多轮循环。LangGraph 能把每个阶段变成可观测、可恢复的节点，适合长任务和多 Agent 编排。

## 19. MCP 适配

代码在 `mini_nanobot/tools/mcp_adapter.py`。

当前实现是轻量 adapter：

- 把 MCP tool 包成 Mini-Nanobot 的 `Tool`。
- 复用 `ToolRegistry`、权限和执行器。

面试追问：

问：MCP 工具和内置工具怎么统一？

答：统一成 Tool 协议。只要有 name、description、input_schema 和 run 方法，就能进入同一个 registry 和 executor。区别只在 run 内部是调用本地函数还是远程 MCP server。

## 20. CLI

代码在 `mini_nanobot/cli.py`。

命令：

```bash
python -m mini_nanobot tools
python -m mini_nanobot run "list files in the workspace"
python -m mini_nanobot sessions
python -m mini_nanobot resume <session_id>
python -m mini_nanobot memory-add feedback "Terse replies" "prefer concise answers" "Use short final answers."
python -m mini_nanobot memory-search "concise answers"
python -m mini_nanobot bench --file benchmarks/tasks.json
```

权限参数：

```bash
--write
--execute
--dangerous
```

真实模型：

```bash
python -m mini_nanobot run "修复测试失败" --provider openai --execute --write
```

## 21. 测试

测试文件：

- `tests/test_tools.py`
- `tests/test_checkpoint_context.py`
- `tests/test_query_engine.py`
- `tests/test_memory_skills.py`

覆盖点：

- 文件写权限拦截。
- 文件写入和读取。
- 危险 shell 命令拦截。
- checkpoint 过滤运行时对象。
- 长上下文压缩。
- query loop 工具执行。
- max_turns 停止。
- memory 增加和召回。
- skill 发现和懒加载。

验证命令：

```bash
python -m pytest -q
```

当前结果：

```text
9 passed
```

## 22. Benchmark

样例文件：

```text
benchmarks/tasks.json
```

运行：

```bash
python -m mini_nanobot bench --file benchmarks/tasks.json
```

当前样例是离线演示任务，主要验证 CLI 和基础工具链。后续要填简历指标，需要扩展到 30 个任务：

- 8 个文件读写 / patch 任务。
- 6 个搜索理解任务。
- 6 个调试修复任务。
- 4 个 Git 操作任务。
- 4 个上下文压缩任务。
- 2 个 checkpoint 恢复任务。

指标定义：

- 任务完成率 = 通过验收任务数 / 总任务数。
- 工具调用成功率 = 成功工具调用数 / 总工具调用数。
- 恢复成功率 = 可 resume 的中断任务数 / 注入中断任务数。
- Token 降低率 = 1 - 压缩后 tokens / 压缩前 tokens。

## 23. 面试核心讲法

最稳的一段回答：

> 我把代码任务 Agent 拆成了七个核心模块。QueryEngine 管会话生命周期，query loop 负责 ReAct 执行，ToolRegistry 和 ToolExecutor 统一工具协议和副作用执行，Sandbox 控制 Shell 风险，ContextCompressor 控制上下文窗口，SQLiteCheckpointStore 支持恢复，Memory 和 Skill 系统负责动态上下文注入。这个拆分让框架不是一次性 prompt 调用，而是一个可恢复、可扩展、可测试的 agent runtime。

如果面试官追问“你项目最大亮点是什么”：

> 最大亮点是上下文和执行安全。很多 demo agent 能调工具，但长任务中 tool result 会把上下文撑爆，shell 执行也容易越权。我这里做了渐进式压缩、大输出落盘引用、checkpoint 恢复、权限分级和命令安全过滤，让 Agent 更像一个工程系统。

如果面试官追问“你和 LangChain agent 有什么区别”：

> LangChain 更像一套组件库，我这个项目强调 runtime 层：状态恢复、工具权限、安全执行、上下文压缩和 memory 注入。它可以接 LangChain 或 LangGraph，但核心目标是把代码任务 Agent 的工程闭环做清楚。

## 24. 常见面试问题

### Q1：为什么工具错误要作为数据返回？

因为 Agent 需要自我修正。如果工具异常直接抛出，整个任务中断；如果错误作为 `tool_result` 注入，模型能读到错误原因并调整下一步，比如换路径、改命令、重新读取文件。

### Q2：为什么要 checkpoint？

代码任务经常是长链路：搜索、读文件、修改、测试、修复、再测试。中间任何一步失败，如果没有 checkpoint 就得从头开始。checkpoint 保存 `AgentState`，可以从最近状态继续。

### Q3：上下文压缩会不会丢关键细节？

会有风险，所以项目采用渐进式策略：先裁剪旧工具结果，再折叠早期消息，最后才 autocompact。压缩摘要会保留任务、plan、最近文件和最近技能。大工具输出则落盘保留完整内容。

### Q4：为什么不用向量数据库做 memory？

MVP 阶段 memory 数量小，用 Markdown + frontmatter + 简单召回足够，且可解释性更好。后续可以把 `recall()` 替换成 embedding 检索，不影响注入协议。

### Q5：Shell sandbox 的不足是什么？

正则和本地 subprocess 不是强隔离。生产级需要 Docker/Firecracker/seccomp/低权限用户/只读挂载/网络策略。当前实现是轻量级安全控制，适合本地学习和 MVP。

### Q6：如何保证工具 schema 和真实执行一致？

schema 定义在工具类里，`validate_input()` 和 `run()` 属于同一个对象，注册时直接从工具对象导出给模型，减少外部配置和实现不一致的风险。

### Q7：为什么 memory 要分类？

分类能降低召回噪声。用户偏好、行为反馈、项目决策、外部引用的使用方式不同，混在一起会让模型误用。

### Q8：为什么 Skill 是懒加载？

技能可能很多，全量注入会浪费 token。元数据足够让模型判断是否需要，真正需要时再调用 `skill.load` 加载完整说明。

### Q9：为什么把 runtime object 从 checkpoint 过滤掉？

状态里可能有 `SkillManager`、hook 或 runner 这类运行时对象，不能 JSON 序列化，也不应该持久化。checkpoint 应只保存可恢复的业务状态。

### Q10：下一步怎么优化？

- 接入真实 LLM 并完善 tool call 解析。
- 把 LangGraph 从 fallback spec 升级为真实状态机执行。
- 增加 Docker 强沙箱。
- 扩展 benchmark 到 30+ 任务。
- 加入 structured logging 和 trace viewer。
- MCP server discovery 和权限隔离。
- 更细粒度 checkpoint：每个 tool call 的 pending/running/done。

## 25. 简历表述建议

可以写：

> Mini-Nanobot：面向代码任务的轻量级 Agent 框架。基于 ReAct 设计 LLM 推理、工具调用、结果反馈循环；实现 JSON Schema 驱动的插件式工具系统，内置 Shell、文件、搜索、Git 等工具；引入权限分级、命令安全过滤、工作区路径隔离和超时控制；基于 SQLite checkpoint 支持中断恢复；使用 tiktoken 实现上下文窗口监控和渐进式压缩；设计 session memory 与 long-term memory 注入机制，并通过 pytest 与 benchmark 验证工具调用和恢复流程。

不要写“复刻某商业产品源码”。更安全的说法是：

> 参考现代代码 Agent 的通用工程模式，自主实现了一个轻量 runtime。

## 26. 当前项目边界

当前是学习型 MVP，不要在面试中夸大：

- 默认 LLM 是离线 rule-based，不代表有真实复杂推理能力。
- OpenAIProvider 是可选接入，还需要真实环境验证。
- Shell sandbox 是轻量安全策略，不是强隔离。
- LangGraph 当前是适配点，不是完整图执行主路径。
- AgentTool 是扩展点，不是完整多 Agent 系统。
- Benchmark 样例还少，真实指标需要扩充任务集后再填。

这样讲反而更可信：你知道项目做了什么，也知道边界在哪里。
