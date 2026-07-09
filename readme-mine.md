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
  负责一次完整用户对话的全生命周期：
  - 创建或恢复 AgentState
  - 构建 system prompt
  - 注入 memory 和 skills
  - 设置权限、工具、checkpoint、compressor
  - 调用 query()
query()基于状态机的异步生成器循环
  负责单次 ReAct 执行循环：
  - 压缩上下文
  - 调 LLMProvider
  - 解析 tool_calls
  - 执行工具
  - 注入 tool result
  - 保存 checkpoint
  - 判断继续或停止
```

```
QueryEngine 实例
├── submit_message("第一条消息")
│   └── query()
│       ├── LLM
│       ├── tool
│       ├── LLM
│       └── final answer
│
├── submit_message("第二条消息")
│   └── query()
│       ├── LLM
│       ├── tool
│       └── final answer
```

面试里你可以这样说：

> `QueryEngine` 是会话级对象，贯穿整个对话生命周期；用户每发一条消息，会进入一次 `submit_message()`；而 `submit_message()` 会启动一次 `query()`，由 `query()` 执行这条消息对应的多轮 ReAct 循环。也就是说，`submit_message()` 是单条用户消息的外层入口，`query()` 是这条消息内部的执行状态机。

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

提示词缓存：

- 稳定内容缓存起来
- 动态内容放到边界之后
- memory / skills 作为动态附件注入 messages



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

## 27. 关键概念再梳理：Skill / Tool / Hook / MCP / Function Calling

这一节专门整理最近复习中最容易混的几个概念。它们都围绕“模型如何使用外部能力”，但处在不同层级。

```text
Function Calling:
  模型如何表达“我要调用哪个工具，参数是什么”

Tool:
  Mini-Nanobot 内部真正可执行的能力抽象

Skill:
  一份任务工作流说明，指导模型如何组织步骤和使用工具

Hook:
  框架在生命周期节点自动触发的回调，用于审计、拦截、统计等横切逻辑

MCP:
  外部工具、资源、提示词接入 Agent 的标准协议
```

完整链路可以记成：

```text
用户自然语言
  -> LLM 结合 tools JSON Schema
  -> Function Calling 输出 tool_call
  -> ToolRegistry 找到 Tool
  -> StreamingToolExecutor 执行 Tool
  -> 如果 Tool 来自 MCP，则通过 MCPToolAdapter 调外部 MCP Server
  -> ToolResult 写回 messages
  -> LLM 继续推理或输出最终回答
```

## 28. Tool 系统

Tool 是模型访问真实环境的唯一入口。模型不能直接读文件、写文件、执行 shell 或访问 Git；它只能输出结构化的 `tool_call`，框架再根据 `tool_call.name` 找到对应 Tool 执行。

Tool 的核心字段：

```text
name:
  工具唯一名称，例如 file.read、shell.run

description:
  给模型看的工具说明

input_schema:
  JSON Schema，描述工具参数

run():
  真正执行工具的函数

is_read_only / is_destructive / is_concurrency_safe:
  工具的安全语义

check_permissions():
  权限检查
```

Tool 执行流程：

```text
1. LLM 输出 tool_call
2. ToolRegistry 根据 tool_call.name 找 Tool
3. ToolExecutor 校验参数
4. 触发 PreToolUse Hook
5. 检查权限
6. 执行 tool.run()
7. 大结果落盘
8. 触发 PostToolUse Hook
9. 返回 ToolResult
10. ToolResult 作为 role="tool" 写回 AgentState.messages
```

重要面试表述：

> Tool 是框架内部的统一能力抽象。无论工具来自本地代码、Shell、Git、Skill 还是 MCP，最终都要注册成 Tool，导出 JSON Schema 给模型，并通过统一执行器做校验、权限、Hook、结果落盘和上下文注入。

## 29. Skill 系统

Skill 不是执行能力，而是工作流提示词。它告诉模型遇到某类任务时应该怎么做。

```text
Tool = 能力
Skill = 方法论
```

例如 debug skill 可能写：

```text
1. 先运行测试
2. 读失败 traceback
3. 读取最相关测试文件
4. 搜索实现代码
5. 做最小修改
6. 重新运行测试
```

Skill 的存储形式：

```text
.nanobot/skills/<skill-name>/SKILL.md
.claude/skills/<skill-name>/SKILL.md
```

`SKILL.md` 分两部分：

```text
frontmatter:
  name
  description
  when_to_use
  source

body:
  完整工作流说明
```

模型如何知道 Skill 存在：

```text
QueryEngine 每轮调用 SkillManager.render_attachment()
  -> 扫描技能目录
  -> 只提取 name / description / when_to_use
  -> 生成 skills-menu meta message
  -> 以 role="user", is_meta=True 注入 messages
```

模型如何调用 Skill：

```text
模型看到 skills-menu
  -> 判断当前任务需要某个 skill
  -> Function Calling 输出 skill.load
  -> SkillTool 读取完整 SKILL.md body
  -> body 作为 ToolResult 写回上下文
  -> 模型根据 Skill 指导继续调用 shell.run / file.read / search.rg 等真正 Tool
```

最容易混的点：

```text
Skill 本身不是 Tool。
skill.load 是 Tool。
SkillTool 是读取 Skill 的工具。
Skill 被加载后变成模型可参考的提示词上下文。
```

当前项目边界：

- 已实现技能发现、元数据注入、按需加载。
- 已有 `SkillManager._invoked` 和 `AgentState.invoked_skills` 的设计入口。
- 还没有完整打通“skill.load 后同步到 AgentState.invoked_skills，再在压缩后自动重注入”的闭环。
- 还没有实现技能信任等级、技能级 Hook、技能安全属性白名单。

## 30. Hook 系统

Hook 是框架预留在关键生命周期节点上的回调插槽。它不是模型调用的 Tool，而是框架自动触发的函数。

一句话：

```text
Tool 是模型主动调用的能力。
Hook 是框架在执行流程中自动调用的扩展函数。
```

当前事件点：

```text
PreToolUse:
  工具执行前

PostToolUse:
  工具执行后

SessionStart:
  会话开始

SessionEnd:
  会话结束

CompactStart:
  上下文压缩前

CompactEnd:
  上下文压缩后
```

Hook 解决的是横切逻辑。横切逻辑指“不属于某一个具体工具，但很多工具都需要”的公共逻辑，例如：

```text
日志记录
审计
权限增强
敏感信息扫描
指标统计
自动 lint
错误上报
```

如果没有 Hook，这些逻辑就要写进每一个 Tool。Hook 让这些逻辑集中注册，一次生效。

PreToolUse 的能力：

```text
执行前审计
拦截危险工具调用
修改 args
附加 metadata
```

PostToolUse 的能力：

```text
记录工具结果
统计成功率
扫描输出
触发后处理
```

面试表述：

> Hook 是 Mini-Nanobot 的生命周期扩展机制。Tool 是模型可调用的执行能力，Hook 不暴露给模型，而是在工具执行前后、会话开始结束、上下文压缩前后由框架自动触发。它适合承载审计、日志、权限增强、指标统计这类横切逻辑，从而避免污染具体 Tool 的核心实现。

## 31. MCP

MCP 是 Model Context Protocol，是把外部工具、资源、提示词标准化接入 Agent 的协议。

完整 MCP 能力包括：

```text
Tools:
  外部可执行动作

Resources:
  外部可读取资源，如文档、schema、知识库

Prompts:
  外部提供的提示词模板或工作流

Server Lifecycle:
  server 启动、连接、重连、关闭

Auth / Capability Negotiation:
  授权和能力协商
```

Mini-Nanobot 当前只实现了 MCP 的最小落地点：

```text
MCP tool -> MCPToolAdapter -> Mini-Nanobot Tool
```

当前项目中的 MCP 执行链路：

```text
外部 MCP Server 暴露 tool
  -> MCP client 获取 name / description / input_schema
  -> MCPToolAdapter 包装成 Mini-Nanobot Tool
  -> 注册到 ToolRegistry
  -> ToolRegistry.to_model_tools() 发给 LLM
  -> LLM 输出 tool_call
  -> ToolExecutor 执行 MCPToolAdapter.run()
  -> mcp_client.call_tool(name, args)
  -> 外部 MCP Server 执行
  -> 返回 ToolResult
  -> 写回 messages
```

为什么 MCP 听起来像 Tool：

```text
因为我们项目当前只实现了 MCP tools adapter。
完整 MCP 很强，但 MVP 只把 MCP Server 提供的 tool 接入了统一 Tool 系统。
```

准确表述：

> 当前项目没有完整实现 MCP Client，而是实现了 MCP 工具适配层。它能把外部 MCP Server 暴露的 tools 包装成框架内部统一 Tool，复用 ToolRegistry、权限、Hook、ToolResult 和 checkpoint 流程。后续可以扩展 MCP server discovery、resources 注入和 prompts-to-skills。

## 32. Function Calling

Function Calling 是模型输出结构化工具调用请求的能力。

它做的不是：

```text
自然语言 -> JSON Schema
```

而是：

```text
用户自然语言 + 工具 JSON Schema -> tool_call
```

边界要记清楚：

```text
Tool 定义:
  框架做

Tool -> JSON Schema:
  ToolRegistry 做

用户自然语言 + JSON Schema -> tool_call:
  Function Calling / 模型做

tool_call -> 执行 Tool:
  ToolRegistry + ToolExecutor 做
```

例子：

```text
用户：
  帮我跑测试

工具 schema：
  shell.run(command: string, timeout_seconds: integer)

Function Calling 输出：
  {
    "name": "shell.run",
    "args": {
      "command": "python -m pytest -q",
      "timeout_seconds": 120
    }
  }

框架执行：
  ToolRegistry.get("shell.run")
  -> ShellTool.run()
```

Function Calling、Tool、MCP 的区别：

```text
Function Calling:
  LLM -> Agent Runtime
  模型如何表达调用意图

Tool:
  Agent Runtime 内部统一抽象
  框架如何执行能力

MCP:
  Agent Runtime -> 外部服务
  外部能力如何接入 Agent
```

一句话：

> Function Calling 是模型侧的结构化调用机制；Tool 是框架侧的执行抽象；MCP 是外部工具和资源接入协议。三者不是同一层，但会在 Agent 工具调用链路中串起来。

## 33. 上下文压缩

上下文窗口不是只包含当前用户输入，而是包含：

```text
system prompt
tools schema
memory / skills meta messages
历史 user / assistant / tool messages
历史工具结果
压缩摘要
当前用户消息
协议开销
预留输出 tokens
部分模型的 reasoning tokens
```

所以即使当前用户只发一句话，input context 也可能已经很大。

预算公式：

```text
max_context_window = input_context + output_tokens + reasoning_tokens
```

在项目里：

```python
effective_window = max_context_tokens - output_reserve_tokens
```

也就是说，我们不能把整个上下文窗口都塞输入，必须给模型输出和推理预留空间。

压缩阈值：

```text
72%:
  黄色预警，开始轻量压缩

90%:
  进入 context collapse

93%:
  进入 autocompact
```

72% 的含义：

```text
不是说 72% 就爆了，而是提前做低损耗清理。
Agent 上下文增长不稳定，一次工具调用可能突然返回大量日志、diff、搜索结果或文件内容。
提前清理可以避免等到接近上限时被迫大摘要。
```

当前压缩流程是渐进式的：

```text
1. 超过 72% 后，先 history_snip
2. 再 microcompact
3. 重新计算 token
4. 如果仍超过 90%，context_collapse
5. 再重新计算 token
6. 如果仍超过 93%，autocompact
```

即使一开始超过 95%，也不会直接跳到 autocompact，而是按低损耗到高损耗逐层尝试。

当前实现和 Claude Code 五级压缩的对应关系：

```text
Claude Code Tool Result:
  我们在 StreamingToolExecutor 中做大输出落盘和 preview

Claude Code History Snip:
  我们当前没有完整实现
  当前 _history_snip 实际更像“剩余长 tool message 裁剪”

Claude Code Microcompact:
  我们保留全局最近 8 条 role="tool" 的消息，旧 tool result 替换成占位

Claude Code Context Collapse:
  我们把早期非 system messages 折叠成 summary meta message

Claude Code Autocompact:
  我们做 deterministic summary + recovery attachment
```

Microcompact 的“最近 8 条”指：

```text
整个会话中最近 8 条 role="tool" 的消息
不是每个工具各保留 8 条
```

为什么是 8：

```text
这是 MVP 经验值，通常能覆盖最近几轮“读文件 -> 搜索 -> 修改 -> 测试”的工具观察链。
生产版本应该配置化，并结合任务类型、工具类型和重要性评分。
```

为什么没有做缓存冷/热和 cache_edits：

```text
因为当前 Mini-Nanobot 是 provider-agnostic 的轻量实现，
没有接入真正的 provider prompt cache，
也没有 message cache reference / cache_edits。
```

准确边界：

> 当前压缩是 Claude Code 思路的轻量工程化版本，不是完整复刻。它优先使用低损耗规则压缩，再使用摘要压缩；但尚未实现完整 History Snip、cache-aware microcompact 和 provider-level cache edits。

## 34. 当前掌握度与待补知识

根据目前提问，你已经逐渐掌握了这些核心链路：

```text
1. QueryEngine / query 双层结构
2. session_id 与 AgentState 的关系
3. system / user / assistant / tool role 和 is_meta 的区别
4. meta messages 为什么每轮动态注入
5. recalled_memory 的轻量关键词召回
6. checkpoint 为什么用 JSON + SQLite
7. snapshot checkpoint 和 event sourcing 的区别
8. Tool / Skill / Hook / MCP / Function Calling 的大致边界
9. 上下文窗口为什么需要预留输出和提前压缩
```

目前还容易混的点：

```text
1. “模型做了什么”和“框架做了什么”的边界
   例如 Function Calling 只负责模型输出 tool_call，
   ToolRegistry 和 ToolExecutor 才负责查找和执行工具。

2. “给模型看的数据”和“Python 运行时对象”的区别
   meta message 会存 checkpoint；
   SkillManager、HookManager、LLM client 这类运行时对象不会存。

3. “MCP 很强”和“当前项目 MCP 只做 adapter”的区别
   面试时要说清楚当前实现边界，不能说完整实现了 MCP。

4. “Skill 是工作流提示词”和“Tool 是执行能力”的区别
   skill.load 是 Tool，但 Skill 本身不是 Tool。

5. 上下文压缩五级策略和我们 MVP 实现之间的差异
   当前不是完整 Claude Code 压缩系统。
```

还建议继续补的模块：

```text
1. Shell Sandbox
   重点掌握命令危险检测、权限分级、路径隔离、环境变量脱敏、timeout。

2. Tool 并发控制
   为什么只读工具可并发，写工具/危险工具串行。

3. Permission Model
   READ_ONLY / WRITE_WORKSPACE / EXECUTE_SAFE / DANGEROUS 如何影响工具执行。

4. Prompt Cache 与动态上下文
   稳定 system prompt、dynamic boundary、meta messages、未来 provider cache_control 的关系。

5. Long-term Memory 的边界
   当前是关键词召回，后续如何升级 embedding + rerank。

6. LangGraph 适配
   当前只是 fallback graph spec，如何升级成真正状态机节点。

7. OpenAIProvider / 真实模型接入
   如何把不同模型 API 返回的 tool call 映射成统一 ToolCall。

8. Benchmark 设计
   如何定义任务完成率、工具成功率、压缩 token 降低率、恢复成功率。

9. 多 Agent / AgentTool
   当前只是扩展点，后续如何实现 fork agent、coordinator、swarm。

10. 生产级 MCP Client
   server discovery、list_tools、resources、prompts、auth、server lifecycle。
```

下一轮复习建议顺序：

```text
1. Shell Sandbox + Permission Model
2. Tool 并发控制与 ToolExecutor 细节
3. Prompt Cache / dynamic meta context
4. Long-term Memory 升级路线
5. LangGraph 和多 Agent 扩展路线
6. Benchmark 指标如何真实跑出来
```
## 31. Multi-Agent Runtime：从 AgentTool 扩展点到可运行子 Agent

之前项目里的 `agent.run` 只是 AgentTool 扩展点：模型能看到“可以委派子任务”的工具 schema，但如果没有 `fork_runner`，它不会真的创建子 Agent。现在已经补成一个可运行的一层 Multi-Agent Runtime。

### 31.1 它解决什么问题

单 Agent 做复杂代码任务时，容易把搜索、分析、修改、验证和总结都挤在同一个上下文里，导致上下文污染、任务链过长、失败难定位。Multi-Agent 的目的不是炫技，而是把独立子任务隔离出去：

```text
主 Agent：
  负责理解用户目标、拆分任务、决定是否委派、汇总结果。

子 Agent：
  负责完成一个边界清晰的小任务，例如只读分析、代码审查、测试定位。
```

### 31.2 当前实现在哪些文件

```text
mini_nanobot/tools/agent.py
  定义 agent.run 和 agent.status 两个 Tool。

mini_nanobot/core/subagent.py
  定义 SubAgentRunner，真正创建 child AgentState 并调用 query()。

mini_nanobot/core/query_engine.py
  初始化 SubAgentRunner，并在每轮请求前把 fork_runner 注入 state.metadata。

mini_nanobot/tools/registry.py
  把 AgentTool 和 AgentStatusTool 注册进默认工具集。
```

### 31.3 完整执行流程

```text
1. 用户给主 Agent 一个复杂任务。
2. QueryEngine 创建或恢复主 AgentState。
3. QueryEngine 注入 fork_runner / subagent_runner 到 runtime metadata。
4. query() 调 LLM。
5. LLM 判断需要委派，输出 agent.run tool_call。
6. StreamingToolExecutor 执行 AgentTool。
7. AgentTool 检查 fork_depth，禁止递归 fork。
8. AgentTool 调用 SubAgentRunner.run()。
9. SubAgentRunner 创建 child AgentState。
10. 子 Agent 注入独立 system prompt 和 parent-delegation meta message。
11. SubAgentRunner 根据 subagent_type 生成受限工具白名单和权限集合。
12. 子 Agent 调用 query(child_state)，自己走 ReAct 循环。
13. 子 Agent 工具结果和消息写入自己的 AgentState。
14. SQLiteCheckpointStore 保存子 Agent checkpoint。
15. 子 Agent 完成后生成 <subagent-result> 结构化结果。
16. 主 Agent 把这个结果作为 agent.run 的 ToolResult 写回上下文。
17. 主 Agent 基于子 Agent 结论继续推理或最终回复。
```

### 31.4 子 Agent 的隔离方式

当前实现的是 `in_process` 一层子 Agent，不是无限递归 swarm。

```text
上下文隔离：
  子 Agent 有自己的 AgentState.messages，不直接污染主 Agent 历史。

状态隔离：
  子 Agent 有自己的 session_id 和 checkpoint。

工具隔离：
  子 Agent 只能看到 reduced ToolRegistry。

权限隔离：
  子 Agent 权限从父 Agent 权限降级而来，不能凭空获得写权限或执行权限。

递归隔离：
  fork_depth >= 1 时 agent.run 会拒绝，避免子 Agent 再 fork 子 Agent。
```

### 31.5 子 Agent 类型和工具权限

当前有几种 profile：

```text
default / researcher / reviewer:
  默认只读，只能 file.read、file.list、search.rg、git.status、git.diff、git.show、git.log、skill.load。

tester:
  在父 Agent 有 EXECUTE_SAFE 权限时，额外允许 shell.run。

coder:
  在父 Agent 有 WRITE_WORKSPACE 权限时，允许 file.write / file.patch；
  在父 Agent 有 EXECUTE_SAFE 权限时，允许 shell.run。
```

这体现了一个关键原则：

```text
子 Agent 权限 <= 父 Agent 权限
```

也就是说，父 Agent 没有写权限时，子 Agent 即使声明自己是 coder，也不会拿到写工具。

### 31.6 为什么结果要结构化返回

子 Agent 不应该把所有中间工具输出原样塞回主 Agent，否则多 Agent 反而会制造上下文噪声。当前返回的是：

```xml
<subagent-result id="..." type="researcher" status="completed">
Name: researcher
Child session: ...
Allowed tools: ...
Permissions: ...

Summary:
子 Agent 最终结论

Recent files:
- ...

Tool events:
- file.list: ok

Notes:
- 权限降级或工具过滤说明
</subagent-result>
```

主 Agent 拿到的是子任务结论、最近文件、工具执行概览和风险说明，而不是完整原始日志。

### 31.7 和 Claude Code 思路的对应关系

这个实现借鉴的是“子任务委派 + 上下文隔离 + 工具作用域控制”的思想，而不是完整复刻 Claude Code。

相似点：

```text
- 子 Agent 作为工具暴露给主 Agent。
- 子 Agent 有独立上下文。
- 子 Agent 只返回压缩后的结果。
- 通过 fork_depth 控制递归风险。
- 通过 tool scope 控制子 Agent 能力边界。
```

当前边界：

```text
- 已实现 in-process 子 Agent。
- 已实现一层 delegation。
- 已实现子 Agent checkpoint。
- 已实现工具白名单和权限降级。
- 已实现后台 task_id + agent.status 的轻量查询入口。
- 未实现真正 git worktree 隔离。
- 未实现 remote worker。
- 未实现复杂 coordinator/swarm/投票聚合。
```

### 31.8 面试回答

可以这样说：

> 我最初把多 Agent 设计成 `agent.run` 扩展点，后来补成了一个可运行的一层子 Agent Runtime。主 Agent 可以通过 Function Calling 调用 `agent.run`，把独立子任务委派给 `SubAgentRunner`。Runner 会创建新的 child AgentState，注入子 Agent system prompt 和 parent-delegation meta message，再根据 subagent_type 生成受限 ToolRegistry 和权限集合，调用同一个 query loop 让子 Agent 独立完成任务。子 Agent 的 messages、tool_events 和 checkpoint 都和主 Agent 分离，完成后只把结构化摘要作为 ToolResult 返回给主 Agent。为了控制复杂度和安全风险，我禁止递归 fork，并保证子 Agent 权限不会超过父 Agent。

如果面试官问“这和完整多 Agent swarm 有什么区别”，回答：

> 我这里实现的是工程上更可控的一层 delegation，不是开放式 swarm。开放式 swarm 需要 coordinator、worker pool、任务投票、冲突解决、远程调度和更强隔离。我当前重点是让代码任务 Agent 能安全地把只读分析、测试定位、局部审查这类子任务委派出去，同时保持 checkpoint、工具权限和上下文边界清晰。

## 32. 核心模块总复盘：从一次请求串起整个 Agent Runtime

这一节把已经学过的 ReAct 编排、Tool System、Shell Sandbox、Memory、Checkpoint、上下文压缩与召回、Skill、Hook、权限模型、Multi-Agent、LangGraph 串成一条完整链路，用来面试前快速复盘。

### 32.1 一段话总览

Mini-Nanobot 当前采用手写 ReAct Loop 编排，而不是 LangGraph 主编排。用户发来一条消息后，外层 `QueryEngine.submit_message()` 负责创建或恢复 `AgentState`，注入稳定 system prompt、长期记忆索引、相关记忆召回、skills menu 和 runtime metadata，然后追加当前用户真实消息；内层 `query()` 进入 ReAct 循环，每轮先由 `ContextCompressor` 检查上下文窗口，必要时压缩历史和工具结果，再把 messages 与 ToolRegistry 导出的 JSON Schema 一起交给 LLM。LLM 如果返回 tool_calls，就由 `StreamingToolExecutor` 统一完成参数校验、PreToolUse Hook、权限检查、工具执行、大结果落盘、PostToolUse Hook 和 ToolResult 写回；随后 `SQLiteCheckpointStore` 保存 AgentState 快照，模型再基于工具结果继续推理。若模型不再调用工具，则保存最终回复并结束。Shell Sandbox 负责降低 shell.run 风险，Memory 负责跨会话知识召回，Skill 负责按需加载任务方法论，Hook 负责审计和横切逻辑，Multi-Agent 允许主 Agent 通过 agent.run 委派一层受限子 Agent，LangGraph 当前只是未来把这个手写循环节点化的适配点。

### 32.2 ReAct 编排：项目主执行方式

当前项目真正运行任务的是：

```text
QueryEngine.submit_message()
  -> query()
      -> while not completed:
           compress_if_needed()
           llm.generate()
           if tool_calls:
             executor.execute_many()
             write tool results
             checkpoint.save()
             continue
           else:
             final response
             checkpoint.save()
             break
```

`QueryEngine` 是会话级编排器，负责 session 生命周期、prompt/memory/skill 注入、权限和运行时对象注入。`query()` 是单条用户消息内部的请求级编排器，负责真正的多轮 ReAct Loop。

面试说法：

> 当前 Mini-Nanobot 用手写 ReAct Loop 做主编排，外层 QueryEngine 管会话生命周期，内层 query 管 LLM 推理、工具调用、结果反馈和 checkpoint。这样比一上来依赖框架更容易解释状态流和恢复逻辑，也方便后续迁移到 LangGraph。

### 32.3 Prompt 与动态上下文

模型每次看到的内容不是只有用户输入，而是多个部分共同组成：

```text
system prompt:
  稳定身份、ReAct 规则、工具使用原则、安全边界。

meta messages:
  memory-index、recalled-memory、skills-menu、context summary、parent-delegation 等动态信息。

user message:
  用户本轮真实请求。

tool schemas:
  ToolRegistry.to_model_tools() 导出的 JSON Schema。
```

`is_meta=True` 不是 API role，而是框架内部标记，用来区分“系统动态注入的 user-role 信息”和“用户真实输入”。动态信息用 `<system-reminder>` 包装，是为了让模型知道这些内容是运行时提醒，不是用户自然语言请求。

### 32.4 Tool System 与 Function Calling

LLM 不直接读文件、写文件或执行 shell。它只能根据工具 JSON Schema 输出结构化 tool_call：

```json
{"name": "file.read", "args": {"path": "mini_nanobot/core/state.py"}}
```

框架侧流程是：

```text
LLM 输出 tool_call.name
  -> ToolRegistry.get(name)
  -> 找到对应 Tool 实例
  -> StreamingToolExecutor.execute_one()
  -> tool.run(args, ctx)
  -> ToolResult 写回 messages
```

这里要区分：

```text
tool name:
  暴露给模型看的字符串，例如 agent.run、file.read、shell.run。

Tool class:
  Python 里实现这个工具的类，例如 AgentTool、FileReadTool、ShellTool。

run():
  Tool 抽象基类定义的统一执行接口。
```

面试说法：

> Function Calling 负责让模型根据 JSON Schema 生成 tool_call；ToolSystem 负责把这个 tool_call 映射到 Python Tool 对象，并执行参数校验、权限检查、Hook、结果落盘和上下文写回。

### 32.5 权限模型：能不能做

权限模型回答的是：

```text
这个工具调用是否被允许？
```

当前权限级别：

```text
READ_ONLY:
  读文件、搜索、git status/diff/log/show。

WRITE_WORKSPACE:
  file.write、file.patch。

EXECUTE_SAFE:
  非破坏性 shell.run。

DANGEROUS:
  高风险破坏性命令，默认不开放。
```

每个 Tool 可以实现 `check_permissions()`。例如 `file.write` 需要 `WRITE_WORKSPACE`；`shell.run` 会结合命令安全策略判断只读、普通执行或危险命令。

面试说法：

> 权限模型是执行前授权层，决定某个 tool_call 能不能做；它是跨工具的，不只属于 shell。

### 32.6 Shell Sandbox：允许后怎么受限执行

Shell Sandbox 不是强虚拟化沙箱，而是本地轻量安全层。它回答的是：

```text
如果允许执行 shell，如何尽量降低风险？
```

当前实现包括：

```text
CommandSafetyPolicy:
  危险命令黑名单、破坏性命令识别、只读命令识别。

ShellTool:
  对模型暴露 shell.run schema，做权限判断。

ShellSandboxExecutor:
  真正启动子进程，限制 cwd、timeout、环境变量、stdout/stderr 输出长度。
```

注意：

```text
权限模型 = 能不能执行。
Shell Sandbox = 执行时怎么限制风险。
```

生产级还需要 Docker/Firecracker/seccomp/低权限用户/网络隔离/只读挂载等。

### 32.7 Checkpoint：长任务中断恢复

Checkpoint 保存的是 `AgentState` 当前快照，不是 event sourcing。

```text
AgentState.to_json()
  -> SQLite checkpoints.state_json

SQLite state_json
  -> AgentState.from_json()
```

保存时机：

```text
LLM 输出 tool_calls 后
工具执行完成后
最终回复后
max_turns 停止时
```

它保存的是可恢复的业务状态：

```text
messages
tool_events
plan
usage
recent_files
compacted_summaries
completed
final_response
```

运行时对象不会保存，例如：

```text
LLM client
SkillManager
HookManager
SubAgentRunner
fork_runner
```

这些对象在恢复会话时由 `QueryEngine` 重新注入。

### 32.8 Memory System：短期记忆与长期记忆

短期记忆是当前 session 的 `AgentState`：

```text
messages
tool_events
recent_files
compacted_summaries
plan
```

长期记忆是跨会话的 Markdown 记录，存储在：

```text
.nanobot/memory/{workspace_hash}/memory/
```

结构大概是：

```text
MEMORY.md
user_*.md
feedback_*.md
project_*.md
reference_*.md
```

`MEMORY.md` 是索引，存标题和摘要；真正参与召回的是具体 `user/project/feedback/reference` 文件的 `title + summary + body`。

每轮请求前：

```text
1. 注入 memory-index。
2. 根据当前 query 做关键词召回。
3. 把 top 记忆包装成 recalled-memory meta message。
4. 提醒模型：memory 只是 hint，使用前要验证当前代码。
```

### 32.9 上下文压缩与召回

上下文窗口里包括：

```text
system prompt
tool schemas
meta messages
历史 user/assistant/tool messages
工具结果
压缩摘要
当前用户消息
输出预留 tokens
```

压缩不是等上下文满了才做，而是提前在阈值附近逐级处理：

```text
history_snip:
  裁剪长工具结果。

microcompact:
  清理旧工具结果，只保留最近若干条。

context_collapse:
  把更早历史折叠成摘要。

autocompact:
  极限压缩，保留恢复信息和最近上下文。
```

召回和压缩是配套的：

```text
压缩负责把当前上下文压小。
召回负责在下一轮重新拿回与当前任务相关的长期记忆。
```

### 32.10 Skill System：方法论懒加载

Skill 是工作流提示词，不是执行能力。

```text
Tool = 能力
Skill = 方法论
```

存储形式：

```text
.nanobot/skills/<name>/SKILL.md
.claude/skills/<name>/SKILL.md
```

每轮 `QueryEngine` 会注入 `skills-menu`，只列出技能名称和使用场景。模型如果认为需要某个技能，就调用：

```text
skill.load
```

`SkillTool` 读取完整 `SKILL.md`，把方法论作为 ToolResult 写回上下文。之后模型根据 Skill 指导再调用真实工具，例如 `file.read`、`search.rg`、`shell.run`。

### 32.11 Hook：横切逻辑扩展点

Hook 不是模型可调用工具，而是框架在生命周期节点自动触发的回调。

当前事件：

```text
SessionStart
SessionEnd
PreToolUse
PostToolUse
CompactStart
CompactEnd
```

典型用途：

```text
审计日志
指标统计
权限增强
危险行为拦截
结果扫描
上下文压缩观测
```

面试说法：

> Hook 解决的是横切逻辑。它不属于某个具体 Tool，但很多阶段都需要，比如审计、日志、权限增强和指标统计。把这些写成 Hook，可以避免污染 Tool 的核心实现。

### 32.12 Multi-Agent：一层受限子 Agent

当前已经实现可运行的一层 in-process 子 Agent。

主 Agent 调用：

```text
agent.run
```

流程是：

```text
LLM 输出 agent.run tool_call
  -> ToolRegistry 找到 AgentTool
  -> ToolExecutor 调 AgentTool.run()
  -> AgentTool 检查 fork_depth
  -> AgentTool 调 fork_runner
  -> fork_runner 实际是 SubAgentRunner.run()
  -> SubAgentRunner 创建 child AgentState
  -> 注入子 Agent system prompt 和 parent-delegation meta message
  -> 根据 subagent_type 生成工具白名单和权限
  -> 调 query(child_state)
  -> 返回 <subagent-result>
```

子 Agent 隔离内容：

```text
独立 AgentState
独立 messages
独立 tool_events
独立 session_id
独立 checkpoint
受限 ToolRegistry
受限权限集合
```

当前 profile：

```text
researcher / reviewer / default:
  默认只读。

tester:
  父 Agent 有 EXECUTE_SAFE 时才允许 shell.run。

coder:
  父 Agent 有 WRITE_WORKSPACE 时才允许 file.write/file.patch；
  父 Agent 有 EXECUTE_SAFE 时才允许 shell.run。
```

核心原则：

```text
子 Agent 权限 <= 父 Agent 权限
禁止递归 fork
子 Agent 不直接污染主 Agent messages
子 Agent 只把结构化结论返回给主 Agent
```

### 32.13 LangGraph：当前是适配点，不是主编排

当前项目的主编排方式是手写 ReAct Loop，不是 LangGraph。

`mini_nanobot/core/graph.py` 里的 `build_query_graph()` 只是表达未来可以拆出的节点：

```text
prepare_context
llm_decision
execute_tools
checkpoint
finish
```

如果未来改成 LangGraph，应该把 `query()` 里的 while loop 拆成真实节点：

```text
prepare_context:
  上下文压缩、动态上下文准备。

llm_decision:
  调 LLM，得到 text/tool_calls。

execute_tools:
  调 StreamingToolExecutor。

checkpoint:
  保存 AgentState。

finish:
  设置 final_response 和 completed。
```

然后用条件边控制：

```text
有 tool_calls -> execute_tools -> checkpoint -> llm_decision
无 tool_calls -> finish
```

面试说法：

> 当前 Mini-Nanobot 是手写 ReAct Loop 编排，LangGraph 是未来状态机化的适配点。真正迁移 LangGraph 后，query() 可以保留为外部 API，但内部应调用 compiled graph，而不是再维护一套 while loop。

### 32.14 最容易被追问的边界

面试时要诚实区分“已实现”和“MVP/预留”：

```text
已实现：
  手写 ReAct Loop
  ToolRegistry / ToolExecutor
  ShellTool 轻量 sandbox
  SQLite snapshot checkpoint
  长期记忆 Markdown 存储和关键词召回
  上下文渐进式压缩
  Skill 发现和 skill.load 懒加载
  Hook 生命周期扩展点
  PermissionLevel 权限模型
  一层 in-process Multi-Agent

MVP / 预留：
  LangGraph 不是主执行引擎
  MCP 只是工具适配层，不是完整 MCP Client
  Shell Sandbox 不是强容器隔离
  Prompt Cache 只是 cache-friendly prompt layout，不是 provider-level token cache
  Benchmark 当前是 smoke benchmark，不是大规模论文级评测
  Multi-Agent 没有 remote worker / git worktree 隔离 / 开放式 swarm
```

### 32.15 一句话面试总述

> Mini-Nanobot 是一个以手写 ReAct Loop 为核心的轻量代码任务 Agent Runtime。它通过 QueryEngine 管理会话，通过 AgentState 承载短期状态，通过 ToolRegistry 和 StreamingToolExecutor 统一工具协议与副作用执行，通过权限模型和 Shell Sandbox 控制风险，通过 Checkpoint 支持中断恢复，通过 Memory、Skill 和上下文压缩维持长任务连续性，通过 Hook 提供审计和横切扩展，通过一层 SubAgentRunner 支持受限子 Agent 委派；LangGraph 当前作为未来图编排适配点，而不是主执行路径。
