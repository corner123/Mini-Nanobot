# Mini-Nanobot 巨详细面试笔记

这份文档是给自己看的深度复习资料，不是公开 README。目标是帮助我在 Agent 开发岗位面试中，能从工程问题出发，把 Mini-Nanobot 的设计、流程、边界和升级方向讲清楚。

统一阅读方式：

```text
每个功能点都按五个问题复盘：
1. 是什么：这个模块解决什么问题。
2. 怎么设计：核心类、数据结构、文件位置、关键抽象。
3. 工作流程：一次真实运行中它如何参与。
4. 局限性：当前 MVP 没做到什么。
5. 面试说法：一句能讲出口的总结。
```

项目定位：

> Mini-Nanobot 是一个面向代码任务的轻量级 Agent Runtime。它不是简单的 prompt wrapper，而是围绕 ReAct Loop、Tool System、Permission Model、Shell Sandbox、Checkpoint、Memory、Context Compression、Skill、Hook 和一层 Multi-Agent 委派，构建一个可恢复、可扩展、可控副作用的代码任务 Agent 框架。

---

## 1. 项目总览

### 1.1 是什么

Mini-Nanobot 是一个轻量级代码任务 Agent 框架，目标是解决 LLM 在工程任务中的几个典型问题：

```text
上下文会越来越长
工具调用会失败
Shell 和文件写入有副作用风险
长任务中断后难以恢复
用户偏好和项目知识无法跨会话保留
复杂任务都挤在一个 Agent 上，容易污染上下文
```

它的核心不是“调一次模型”，而是让模型处在一个可控 Runtime 里：

```text
模型负责推理和选择动作
Runtime 负责状态、工具、安全、恢复、记忆和压缩
```

### 1.2 怎么设计

核心模块：

```text
core/query_engine.py       会话级入口
core/query.py              手写 ReAct Loop
core/state.py              AgentState / Message / ToolCall
core/prompts.py            system prompt 分层
tools/base.py              Tool 抽象
tools/registry.py          ToolRegistry
tools/executor.py          StreamingToolExecutor
sandbox/                   Shell Sandbox
memory/checkpoint.py       SQLite checkpoint
memory/long_term.py        长期记忆
context/compressor.py      上下文压缩
skills/                    Skill 系统
hooks/manager.py           Hook 系统
core/subagent.py           一层子 Agent Runtime
core/graph.py              LangGraph 适配点
cli.py                     CLI
```

### 1.3 工作流程

一次用户请求进入系统后：

```text
用户输入
  -> QueryEngine.submit_message()
      -> 创建或恢复 AgentState
      -> 注入 system prompt / memory / skills / runtime metadata
      -> 追加当前用户消息
      -> 调 query()
          -> 检查上下文窗口
          -> 调 LLM
          -> 如果有 tool_calls，执行工具并写回 ToolResult
          -> 保存 checkpoint
          -> 继续 ReAct Loop
          -> 如果无 tool_calls，生成最终回复
      -> 返回 QueryResult
```

### 1.4 局限性

当前项目是学习和面试友好的 MVP，不是生产级 Claude Code：

```text
LangGraph 不是主执行路径
MCP 只是 Tool Adapter
Shell Sandbox 不是强隔离容器
Prompt Cache 不是 provider-level cache
Benchmark 是 smoke benchmark
Multi-Agent 是一层 in-process delegation，不是 swarm
```

### 1.5 面试说法

> 我做的是一个轻量代码任务 Agent Runtime。它用手写 ReAct Loop 串起 LLM 推理、工具调用、结果反馈和 checkpoint；用 Tool System 统一副作用执行；用权限模型和 Shell Sandbox 控制风险；用 Memory、Skill 和上下文压缩维护长任务连续性；再用一层 SubAgentRunner 支持受限子 Agent 委派。

---

## 2. 技术栈与项目结构

### 2.1 是什么

技术栈不是堆关键词，而是对应 Agent Runtime 的各层能力：

```text
Python:
  Runtime 主语言。

ReAct:
  推理、行动、观察的执行范式。

JSON Schema / Function Calling:
  让模型输出结构化 tool_call。

asyncio:
  支持异步工具执行和 shell subprocess。

tiktoken:
  上下文 token 统计。

SQLite:
  checkpoint 持久化。

LangGraph:
  未来图编排适配点。

MCP:
  外部工具协议适配方向。

pytest / benchmark:
  回归验证和能力评测。
```

### 2.2 怎么设计

模块分层：

```text
会话层:
  QueryEngine

执行层:
  query() ReAct Loop

状态层:
  AgentState / Message / ToolCall / Usage

工具层:
  Tool / ToolRegistry / StreamingToolExecutor

安全层:
  PermissionLevel / ShellSandbox / workspace path isolation

上下文层:
  PromptBuilder / ContextCompressor / TokenCounter

记忆层:
  SQLiteCheckpointStore / LongTermMemoryStore

扩展层:
  Skill / Hook / MCP Adapter / SubAgentRunner

入口层:
  CLI / tests / benchmark
```

### 2.3 工作流程

这些模块不是孤立的，运行时会这样组合：

```text
QueryEngine 准备 state、memory、skills、permissions
query() 调 LLMProvider
LLMProvider 返回 tool_calls
ToolRegistry 找工具
ToolExecutor 执行工具
Permission / Sandbox 控制风险
Checkpoint 保存状态
ContextCompressor 控制 token
Memory/Skill 在下一轮继续影响模型
```

### 2.4 局限性

当前为了可读性和面试解释，很多能力是轻量实现：

```text
没有企业级审计数据库
没有分布式任务队列
没有真实容器沙箱默认启用
没有大型 benchmark
没有向量记忆库
没有完整 MCP client lifecycle
```

### 2.5 面试说法

> 这个项目的设计重点是把 Agent Runtime 拆成清晰层次：会话、执行、状态、工具、安全、上下文、记忆和扩展。每一层都能单独解释和测试，而不是把所有逻辑写成一个大 prompt 或一个大函数。

---

## 3. QueryEngine 与 query 双层编排

### 3.1 是什么

双层编排指：

```text
QueryEngine.submit_message():
  管整个会话生命周期。

query():
  管单条用户消息内部的 ReAct Loop。
```

`session_id` 对应整个会话，不是一问一答。

### 3.2 怎么设计

`QueryEngine` 初始化：

```text
workspace
.nanobot 目录
LLMProvider
ToolRegistry
HookManager
Permission set
SystemPromptBuilder
SQLiteCheckpointStore
LongTermMemoryStore
SkillManager
ContextCompressor
SubAgentRunner
```

`query()` 接收已经准备好的运行环境：

```text
state
llm
registry
workspace
checkpoint
compressor
hooks
permissions
max_turns
```

这说明：

```text
QueryEngine 负责准备环境
query 负责消费环境并执行状态机
```

### 3.3 工作流程

新会话：

```text
submit_message(task)
  -> AgentState(task=task)
  -> build system prompt
  -> state.messages.append(system)
  -> inject dynamic context
  -> append current user message
  -> query(state)
```

继续会话：

```text
submit_message(task, session_id=...)
  -> checkpoint.load(session_id)
  -> state.completed = False
  -> inject new dynamic context
  -> append current user message
  -> query(state)
```

这里的“新 dynamic context”包括最新 memory recall、skills menu 等，不是简单复制上一轮。

### 3.4 局限性

当前 `submit_message()` 每轮都会注入 memory index / skills menu，可能有重复。生产级可以做：

```text
meta message 去重
只在变化时注入 skills menu
memory recall 增量更新
project instructions hash 缓存
```

### 3.5 面试说法

> 我把会话生命周期和 ReAct 执行循环拆开。QueryEngine 负责创建或恢复 AgentState、注入动态上下文和运行时对象；query 只负责这条用户消息内部的多轮 LLM-tool loop。这样 checkpoint、resume、multi-agent 和未来 LangGraph 迁移都更清晰。

---

## 4. ReAct Loop 手写编排

### 4.1 是什么

ReAct = Reason + Act + Observe。

在 Mini-Nanobot 里：

```text
Reason:
  LLM 根据上下文决定下一步。

Act:
  LLM 输出 tool_calls。

Observe:
  工具结果写回 role="tool" message。
```

### 4.2 怎么设计

核心在 `mini_nanobot/core/query.py`。

主要组件：

```text
ContextCompressor:
  每轮 LLM 前检查上下文。

LLMProvider:
  生成文本或 tool_calls。

StreamingToolExecutor:
  执行工具。

SQLiteCheckpointStore:
  保存状态。

ToolContext:
  提供 workspace、permissions、artifact_dir、metadata。
```

### 4.3 工作流程

伪代码：

```text
while not state.completed and state.turns < max_turns:
  report = compressor.compress_if_needed(state)

  response = llm.generate(
    state.messages,
    registry.to_model_tools(),
    state,
  )

  if response.text:
    state.add_message(assistant text)

  if response.tool_calls:
    state.add_message(assistant tool_calls record)
    checkpoint.save(state)

    results = executor.execute_many(response.tool_calls, tool_ctx)
    for result in results:
      state.add_tool_event(...)
      state.add_message(role="tool", ...)

    checkpoint.save(state)
    continue

  state.completed = True
  state.final_response = response.text
  checkpoint.save(state)
  break
```

### 4.4 局限性

当前 loop 是手写状态机：

```text
优点:
  简单、可读、容易讲清楚。

缺点:
  节点不可视化
  条件路由不如 LangGraph 明确
  复杂多 Agent 编排会变得难维护
```

### 4.5 面试说法

> 当前项目的主编排方式是手写 ReAct Loop，而不是 LangGraph。每轮先压缩上下文，再调用模型；有 tool_calls 就执行工具、写回 ToolResult、保存 checkpoint，再继续；没有 tool_calls 就生成最终回复。这个 loop 是整个 Agent Runtime 的心脏。

---

## 5. Prompt 与 Context Engineering

### 5.1 是什么

Prompt / Context Engineering 指：

```text
如何组织模型输入，让模型同时看到稳定规则、当前任务、工具能力、记忆、技能和压缩摘要。
```

模型输入不是只有用户当前问题。

### 5.2 怎么设计

输入由四类组成：

```text
system prompt:
  稳定规则，优先级最高。

meta messages:
  框架动态注入的上下文提醒。

真实 user message:
  用户本轮输入。

tools schema:
  ToolRegistry.to_model_tools() 导出的 JSON Schema。
```

系统提示词由 `SystemPromptBuilder` 构建，包含：

```text
identity:
  Mini-Nanobot 身份。

tool_policy:
  工具使用原则。

safety:
  副作用和危险命令规则。
```

动态 meta messages 使用：

```python
Message(role="user", is_meta=True, name="...")
```

常见类型：

```text
memory-index
recalled-memory
skills-menu
context-collapse
autocompact
recovery
parent-delegation
```

### 5.3 工作流程

新会话：

```text
build system prompt
  -> state.add_message(role="system")
```

每轮用户消息：

```text
memory.index_attachment()
  -> memory-index meta message

memory.recall_attachment(task)
  -> recalled-memory meta message

skills.render_attachment()
  -> skills-menu meta message

state.add_message(role="user", is_meta=False)
```

LLM 调用时：

```text
messages + tools schema -> llm.generate()
```

### 5.4 局限性

当前项目没有完整 provider-level prompt cache。

已经做了：

```text
稳定 system prompt 和动态上下文分层
本进程内 section cache
system prompt dynamic boundary
```

还没做：

```text
OpenAI/Claude cache_control
prompt prefix hash 跨进程缓存
cached_tokens 统计
provider-specific cache references
```

### 5.5 面试说法

> 我把 prompt 分成稳定 system prompt 和动态 meta messages。系统提示词保存长期不变的身份、工具规则和安全边界；memory、skills、压缩摘要这类每轮变化的信息作为 `is_meta=True` 的 user-role message 注入。这样既避免污染 system prompt，也为未来接入 provider prompt cache 保留稳定前缀。

---

## 6. LLMProvider 抽象

### 6.1 是什么

`LLMProvider` 是模型调用抽象。

它让 Runtime 不绑定某个模型厂商。

### 6.2 怎么设计

统一接口：

```python
async def generate(
    messages: list[Message],
    tools: list[dict],
    state: AgentState,
) -> LLMResponse
```

统一返回：

```text
LLMResponse:
  text
  tool_calls
  usage
  raw
```

当前 provider：

```text
RuleBasedLLM:
  离线演示。

ScriptedLLM:
  测试用。

OpenAIProvider:
  可选 OpenAI API。
```

### 6.3 工作流程

`query()` 不直接调 OpenAI 或 Claude，而是调：

```text
llm.generate(messages, tools, state)
```

然后根据统一的 `LLMResponse` 判断：

```text
有 tool_calls:
  进入工具执行

无 tool_calls:
  结束
```

### 6.4 局限性

当前 provider 抽象较轻：

```text
没有流式 token 输出
没有复杂 usage/cost 映射
OpenAIProvider 只做基础 tool call 解析
没有 Claude Provider
没有模型能力自动配置
```

### 6.5 面试说法

> 我把模型调用抽象成 LLMProvider，Runtime 只依赖统一的 LLMResponse，而不关心底层是离线规则模型、测试脚本模型还是 OpenAI。这样可以替换 provider，也方便测试 ReAct Loop 和工具执行。

---

## 7. Function Calling 与 Tool System

### 7.1 是什么

Function Calling 解决的是：

```text
模型如何把自然语言决策变成结构化 tool_call。
```

Tool System 解决的是：

```text
框架如何注册、暴露、校验和执行真实工具。
```

### 7.2 怎么设计

每个 Tool 都继承 `Tool` 基类：

```text
name
description
input_schema
output_schema
run()
is_read_only()
is_destructive()
is_concurrency_safe()
check_permissions()
```

`ToolRegistry` 保存：

```text
{
  "file.read": FileReadTool(),
  "shell.run": ShellTool(),
  "agent.run": AgentTool(),
}
```

`to_model_tools()` 把 Tool 转成模型可见 JSON Schema。

### 7.3 工作流程

LLM 看到工具 schema 后输出：

```json
{
  "name": "file.read",
  "args": {
    "path": "mini_nanobot/core/state.py"
  }
}
```

Runtime 执行：

```text
ToolExecutor
  -> registry.get("file.read")
  -> 得到 FileReadTool 实例
  -> validate_input
  -> PreToolUse Hook
  -> check_permissions
  -> run()
  -> PostToolUse Hook
  -> ToolResult
```

注意：

```text
file.read 是工具名
FileReadTool 是 Python 类
FileReadTool.run() 是真实执行逻辑
```

### 7.4 局限性

当前参数校验是轻量校验：

```text
检查 required
检查 unknown argument
```

没有完整使用 `jsonschema` 做类型和复杂约束校验。生产级可以加强：

```text
完整 JSON Schema validator
schema versioning
tool result schema 校验
工具调用审计记录
```

### 7.5 面试说法

> Function Calling 负责让模型输出结构化 tool_call；Tool System 负责把 tool_call.name 映射到 Python Tool 对象，并统一做参数校验、权限检查、Hook、执行和结果写回。模型只会“提出调用请求”，真正执行永远在 Runtime 里发生。

---

## 8. StreamingToolExecutor 与并发控制

### 8.1 是什么

`StreamingToolExecutor` 是统一工具执行器。

它解决：

```text
工具如何被安全、统一、可观测地执行
多个工具调用是否能并发
大结果如何落盘
错误如何返回给模型
```

### 8.2 怎么设计

核心方法：

```text
execute_many(calls, ctx)
execute_one(call, ctx)
_persist_if_large(...)
```

并发策略：

```text
tool.is_concurrency_safe(args) 为 True 的工具可以进入并发 batch。
默认只读工具可并发。
非只读工具串行。
```

### 8.3 工作流程

`execute_many()`：

```text
遍历 tool_calls
  -> 如果工具可并发，加入 batch
  -> 如果不可并发，先 flush batch，再单独执行
最后 flush 剩余 batch
```

并发 batch：

```text
asyncio.Semaphore(max_concurrency=4)
asyncio.gather(...)
```

`execute_one()`：

```text
找 Tool
校验参数
PreToolUse Hook
权限检查
tool.run()
大结果落盘
PostToolUse Hook
返回 ToolResult
```

### 8.4 局限性

当前并发模型较简单：

```text
按只读/非只读判断
没有细粒度文件锁
没有资源池
没有跨工具依赖分析
没有取消传播
```

生产级可以做：

```text
按文件路径加写锁
工具依赖 DAG
执行超时统一管理
并发度按工具类型配置
```

### 8.5 面试说法

> 我在 ToolExecutor 里做了保守并发：连续只读工具可以 batch 并发执行，写操作、shell 和 agent.run 这类可能有副作用的工具串行执行。这样能提升搜索和读取效率，同时避免写入状态竞争。

---

## 9. Permission Model

### 9.1 是什么

权限模型回答：

```text
这个 tool_call 能不能做？
```

它是执行前授权层，适用于所有工具，不只是 shell。

### 9.2 怎么设计

权限级别：

```text
READ_ONLY
WRITE_WORKSPACE
EXECUTE_SAFE
GIT_MUTATE
DANGEROUS
```

`ToolContext` 中携带：

```text
permissions: set[PermissionLevel]
```

每个工具通过：

```python
check_permissions(args, ctx)
```

判断是否允许。

### 9.3 工作流程

CLI 参数：

```text
默认:
  READ_ONLY

--write:
  WRITE_WORKSPACE

--execute:
  EXECUTE_SAFE

--dangerous:
  DANGEROUS
```

工具执行前：

```text
ToolExecutor
  -> tool.check_permissions(args, ctx)
  -> allowed: run
  -> denied: ToolResult(is_error=True)
```

### 9.4 局限性

当前权限模型是静态权限集合：

```text
没有交互式审批
没有 per-path ACL
没有用户/团队角色
没有持久审计策略
```

### 9.5 面试说法

> 权限模型是跨工具授权层。它决定 Agent 在当前运行上下文里能不能读、写、执行或做危险操作。即使模型输出了某个 tool_call，也必须经过 Tool.check_permissions 才能真正执行。

---

## 10. Shell Sandbox

### 10.1 是什么

Shell Sandbox 是 shell.run 的轻量安全执行层。

它回答：

```text
如果允许执行 shell，如何降低风险？
```

### 10.2 怎么设计

三层：

```text
CommandSafetyPolicy:
  检测危险命令、破坏性命令、只读命令。

ShellTool:
  暴露 shell.run，做权限语义判断。

ShellSandboxExecutor:
  真正启动 subprocess，限制 cwd、timeout、env、输出。
```

### 10.3 工作流程

模型输出：

```json
{"name": "shell.run", "args": {"command": "pytest -q"}}
```

执行：

```text
ShellTool.check_permissions()
  -> policy.inspect(command)
  -> blocked: 拒绝
  -> read-only: READ_ONLY 可执行
  -> normal: 需要 EXECUTE_SAFE
  -> destructive: 需要 DANGEROUS

ShellTool.run()
  -> ShellSandboxExecutor.run()
  -> create_subprocess_shell()
  -> wait_for(timeout)
  -> capture stdout/stderr
  -> ToolResult
```

### 10.4 局限性

当前不是强隔离：

```text
没有容器默认隔离
没有 seccomp
没有网络隔离
没有低权限用户
没有 CPU/内存限制
```

### 10.5 面试说法

> 当前 Shell Sandbox 是本地轻量安全层，能做危险命令过滤、工作目录限制、超时、环境变量脱敏和输出限制。它适合 MVP 和学习场景，但生产级还需要 Docker/Firecracker/seccomp 等强隔离。

---

## 11. 文件安全与 Workspace 隔离

### 11.1 是什么

文件安全保证 Agent 的读写不会逃出当前 workspace。

### 11.2 怎么设计

核心方法：

```python
ToolContext.resolve_workspace_path()
```

逻辑：

```text
相对路径:
  workspace / path

绝对路径:
  resolve 后检查是否在 workspace 内

逃逸:
  raise ValueError("path escapes workspace")
```

### 11.3 工作流程

`file.read` / `file.write` / `file.patch` / `file.list` 都会先解析路径。

示例：

```text
用户传 ../secret.txt
  -> resolve
  -> relative_to(workspace) 失败
  -> 拒绝
```

### 11.4 局限性

当前只限制路径，不提供：

```text
per-file ACL
敏感文件模式过滤
只读挂载
文件变更事务
自动回滚
```

### 11.5 面试说法

> 文件工具都通过 ToolContext.resolve_workspace_path 做路径解析和 workspace 边界检查，避免模型通过绝对路径或 `../` 读写工作区外文件。

---

## 12. Checkpoint 与 Resume

### 12.1 是什么

Checkpoint 是长任务恢复机制。

它解决：

```text
Agent 执行到一半中断后，不必从头开始。
```

### 12.2 怎么设计

当前是 snapshot checkpoint，不是 event sourcing。

存储：

```text
.nanobot/checkpoints.sqlite3
```

表：

```text
checkpoints:
  session_id
  task
  state_json
  completed
  created_at
  updated_at
```

`state_json` 来自：

```python
AgentState.to_json()
```

恢复：

```python
AgentState.from_json()
```

### 12.3 工作流程

保存时机：

```text
LLM 输出 tool_calls 后
工具执行完成后
最终回复后
max_turns 停止时
```

恢复流程：

```text
submit_message(task, session_id)
  -> checkpoints.load(session_id)
  -> AgentState.from_json()
  -> state.completed = False
  -> 重新注入 runtime metadata
  -> 注入本轮 memory/skills
  -> 追加当前用户消息
  -> query(state)
```

### 12.4 局限性

当前 checkpoint 只保存最新快照：

```text
不能回放每一步
不能任意时间点回滚
events 表没有作为 event sourcing 使用
大工具输出只保存 artifact path 和 preview
```

### 12.5 面试说法

> 我用 SQLite 保存 AgentState 的 snapshot checkpoint。它不是 event sourcing，而是为了快速恢复最新会话状态。运行时对象如 LLM client、SkillManager、SubAgentRunner 不进 JSON，恢复时由 QueryEngine 重新注入。

---

## 13. Memory System

### 13.1 是什么

Memory 让 Agent 具备两种连续性：

```text
短期连续性:
  当前 session 内接着做。

长期连续性:
  跨 session 记住用户偏好、项目知识和反馈。
```

### 13.2 怎么设计

短期记忆：

```text
AgentState.messages
AgentState.tool_events
AgentState.recent_files
AgentState.compacted_summaries
```

长期记忆：

```text
.nanobot/memory/{workspace_hash}/memory/
```

文件：

```text
MEMORY.md
user_*.md
feedback_*.md
project_*.md
reference_*.md
```

每条 memory 是 Markdown + frontmatter：

```text
kind
title
summary
updated_at
body
```

### 13.3 工作流程

长期记忆写入：

```text
memory.add(kind, title, summary, body)
  -> 写 Markdown 文件
  -> rebuild MEMORY.md
```

每轮召回：

```text
QueryEngine._inject_dynamic_context()
  -> memory.index_attachment()
  -> memory.recall_attachment(task)
```

召回算法：

```text
对当前 query 分词
遍历当前 workspace_hash 下的具体 memory 文件
跳过 MEMORY.md
在 title + summary + body 中匹配关键词
按命中数排序
取 top 5
包装成 recalled-memory meta message
```

### 13.4 局限性

当前是轻量关键词召回：

```text
中文分词弱
没有 embedding
没有 LLM rerank
没有团队记忆权限
没有过期自动清理
```

升级方向：

```text
中文分词
embedding 检索
LLM rerank
team memory
memory freshness score
memory write approval
```

### 13.5 面试说法

> 我把 Memory 分成短期 AgentState 和长期 Markdown Memory。短期记忆服务当前 session，长期记忆按 workspace hash 隔离，存用户偏好、反馈、项目知识和参考资料。每轮请求前会注入 MEMORY.md 索引，并根据当前 query 召回相关记忆，但召回内容只是 hint，使用前要验证当前代码。

---

## 14. Context Compression 与 Token 预算

### 14.1 是什么

上下文压缩解决：

```text
长任务中 messages 和 tool results 不断增长，最终超过模型上下文窗口。
```

### 14.2 怎么设计

核心类：

```text
ContextBudget
ContextCompressor
TokenCounter
CompressionReport
```

预算：

```text
max_context_tokens
output_reserve_tokens
effective_window = max_context_tokens - output_reserve_tokens
```

阈值：

```text
history_snip_threshold = 0.72
collapse_threshold = 0.90
autocompact_threshold = 0.93
microcompact_keep_tool_results = 5
cache_reference_ttl_seconds = 300
```

另外 `AgentState` 现在有两份上下文视图：

```text
messages:
  原始会话历史，保留 canonical history。

context_projection:
  给模型看的压缩投影视图。为空时使用 messages；非空时 query() 调 LLM 使用 projection。
```

### 14.3 工作流程

Level 1 发生在工具执行阶段：

```text
StreamingToolExecutor._persist_if_large()
  -> 单次 ToolResult 超过工具预算
  -> 完整内容写入 .nanobot/artifacts/<session_id>/tool-results/
  -> messages 里只放 head + tail + Full output path
```

每轮 LLM 前：

```text
before = count_messages(state.active_messages())
if before < 72% effective_window:
  不压缩

否则:
  emit CompactStart
  _history_snip()
  _microcompact()
  如果仍超过 90%:
    _context_collapse()
  如果仍超过 93%:
    _autocompact()
  emit CompactEnd
```

压缩层级：

```text
Level 1 Tool Result 预算裁剪:
  新工具结果刚产生时处理。
  完整结果落盘，上下文只保留紧凑 preview 和 artifact path。

history_snip:
  启发式清理历史冗余。
  包括重复工具输出、被后续编辑覆盖的中间编辑尝试，以及超长 tool message 头尾裁剪。

microcompact:
  只保留最近 5 条 tool result。
  更旧的 tool result 如果仍在 5 分钟 cache ttl 内，替换成 cache_reference 风格占位；
  如果过期，则直接替换成 removed placeholder，可附 artifact path。

context_collapse:
  创建 projection，不删除原始 messages。
  projection = system + context-collapse summary + 最近 6 条消息。

autocompact:
  最后兜底。
  优先调用 runtime compact_summarizer 生成摘要；没有配置或失败时用确定性 fallback。
  projection = autocompact summary + recovery attachment + 最近 4 条消息。
```

### 14.4 局限性

当前实现是对五级流水线的工程化版本：

```text
已实现：
  Tool Result 落盘预览
  History Snip 的重复输出清理、被覆盖编辑清理、长工具结果裁剪
  Microcompact 最近 5 条工具结果保留
  cache_reference 风格占位
  Context Collapse projection，不删除原始 messages
  Autocompact 可插拔 summary agent + fallback

仍是 MVP：
  cache_reference 只是占位协议，没有真实服务端 KV cache mask
  History Snip 启发式规则还很简单
  projection 是模型上下文投影，不是前端 UI 折叠
  summary agent 只在配置真实 LLM 时有意义，RuleBasedLLM 会 fallback
```

### 14.5 面试说法

> 我把上下文压缩做成渐进式流水线。工具结果刚产生时先做预算裁剪和落盘预览；每轮 LLM 前再做 History Snip，清理重复工具输出和被覆盖的编辑尝试；然后 Microcompact 只保留最近 5 条工具结果，并用 cache_reference 风格占位表示仍可能复用缓存；如果还超限，就创建不删除原始 messages 的 context projection；最后才 Autocompact，用 summary agent 或 fallback 摘要生成恢复上下文。

---

## 15. Skill System

### 15.1 是什么

Skill 是任务方法论，不是执行能力。

```text
Tool = 能力
Skill = 方法论
```

### 15.2 怎么设计

存储：

```text
.nanobot/skills/<skill-name>/SKILL.md
.claude/skills/<skill-name>/SKILL.md
```

`SkillManager` 负责：

```text
discover()
render_attachment()
load(name)
invoked_attachment()
```

`SkillTool` 暴露：

```text
skill.load
```

### 15.3 工作流程

每轮请求前：

```text
SkillManager.render_attachment()
  -> skills-menu meta message
```

模型看到 skills menu：

```text
如果需要某个 skill
  -> 输出 skill.load tool_call
```

执行：

```text
SkillTool.run()
  -> manager.load(name)
  -> 读取 SKILL.md body
  -> ToolResult 写回上下文
```

模型随后根据 Skill 方法论调用真实工具。

### 15.4 局限性

当前没有：

```text
skill trust level
skill 权限声明
skill hooks
压缩后 invoked skill 自动重注入
skill 版本管理
```

### 15.5 面试说法

> Skill 是工作流提示词，告诉模型某类任务应该怎么做；Tool 是实际执行能力。模型先通过 skills-menu 知道有哪些 Skill，再按需调用 skill.load 加载完整说明，之后仍然通过 file、search、shell 等 Tool 执行真实动作。

---

## 16. Hook System

### 16.1 是什么

Hook 是框架生命周期中的回调插槽。

它不是模型调用的工具，而是 Runtime 自动触发的函数。

### 16.2 怎么设计

核心类：

```text
HookManager
HookResult
```

事件：

```text
SessionStart
SessionEnd
PreToolUse
PostToolUse
CompactStart
CompactEnd
```

### 16.3 工作流程

工具执行：

```text
validate_input
-> PreToolUse Hook
-> check_permissions
-> tool.run
-> PostToolUse Hook
```

压缩执行：

```text
CompactStart
-> 压缩
-> CompactEnd
```

### 16.4 局限性

当前 Hook 是进程内回调：

```text
没有持久 hook 配置
没有 hook plugin loader
没有失败重试策略
没有 hook 级权限隔离
```

### 16.5 面试说法

> Hook 解决的是横切逻辑，比如审计、日志、指标统计、权限增强和危险行为拦截。它不暴露给模型，而是在工具执行前后、会话开始结束、上下文压缩前后由框架自动触发。

---

## 17. MCP Adapter

### 17.1 是什么

MCP = Model Context Protocol。

它是外部工具、资源和提示词接入 Agent 的协议。

### 17.2 怎么设计

当前项目只实现：

```text
MCPToolAdapter
```

它把外部 MCP tool 包装成 Mini-Nanobot 内部 Tool。

这样 MCP tool 可以复用：

```text
ToolRegistry
ToolExecutor
Permission Model
Hook
ToolResult
Checkpoint
```

### 17.3 工作流程

```text
外部 MCP server 暴露 tool
  -> MCPToolAdapter 包装成 Tool
  -> registry.register(adapter)
  -> ToolRegistry.to_model_tools()
  -> LLM 输出对应 tool_call
  -> ToolExecutor 执行 adapter.run()
  -> adapter 调 MCP client.call_tool()
  -> ToolResult 写回上下文
```

### 17.4 局限性

当前不是完整 MCP Client。

未实现：

```text
server discovery
resources
prompts
auth
server lifecycle
capability negotiation
```

### 17.5 面试说法

> 当前项目实现的是 MCP 工具适配层，不是完整 MCP Client。它能把外部 MCP Server 的 tools 包装成框架内部 Tool，从而复用现有的工具注册、权限、Hook 和结果写回流程。

---

## 18. Multi-Agent Runtime

### 18.1 是什么

Multi-Agent 解决：

```text
复杂任务不应该全部挤在主 Agent 上。
```

当前实现的是一层 in-process 子 Agent 委派，不是开放式 swarm。

### 18.2 怎么设计

核心文件：

```text
tools/agent.py:
  AgentTool / AgentStatusTool

core/subagent.py:
  SubAgentRunner
```

关键对象：

```text
agent.run:
  主 Agent 可调用的委派工具。

fork_depth:
  防止递归 fork。

fork_runner:
  QueryEngine 注入的子 Agent 运行函数。

SubAgentRunner:
  真正创建和运行 child AgentState。
```

### 18.3 工作流程

```text
LLM 输出 agent.run tool_call
  -> ToolRegistry 找到 AgentTool
  -> ToolExecutor 调 AgentTool.run()
  -> AgentTool 检查 fork_depth
  -> AgentTool 获取 fork_runner
  -> fork_runner(args, ctx)
  -> SubAgentRunner.run()
  -> 创建 child AgentState
  -> 注入 child system prompt
  -> 注入 parent-delegation meta message
  -> 根据 subagent_type 生成工具白名单
  -> 根据父权限生成子权限
  -> 调 query(child_state)
  -> 子 Agent 独立执行 ReAct Loop
  -> 保存 child checkpoint
  -> 返回 <subagent-result>
  -> 主 Agent 把结果作为 ToolResult 写回上下文
```

### 18.4 子 Agent Profile

Profile 是预设子 Agent 类型，不是独立 Python 类。

```text
default / researcher / reviewer:
  只读工具。

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
```

### 18.5 局限性

当前没有：

```text
remote worker
git worktree 隔离
开放式 swarm
任务投票
冲突解决
持久化后台队列
```

### 18.6 面试说法

> 我把子 Agent 设计成 agent.run 工具。主 Agent 通过 Function Calling 委派子任务，AgentTool 只负责协议入口和递归检查，真正运行由 SubAgentRunner 完成。Runner 创建独立 child AgentState，限制工具和权限，复用 query loop 独立执行，最后只把结构化摘要返回给主 Agent。

---

## 19. LangGraph 适配

### 19.1 是什么

LangGraph 是 LLM 工作流状态机框架。

它适合把 Agent Loop 拆成节点和条件边。

### 19.2 怎么设计

当前项目有：

```text
core/graph.py
build_query_graph()
```

表达未来节点：

```text
prepare_context
llm_decision
execute_tools
checkpoint
finish
```

但当前主路径不是 LangGraph。

### 19.3 工作流程

当前实际：

```text
QueryEngine.submit_message()
  -> query()
  -> 手写 while loop
```

未来迁移：

```text
query()
  -> compiled_graph.ainvoke(state)
```

图流程：

```text
prepare_context
  -> llm_decision
  -> 有 tool_calls: execute_tools -> checkpoint -> llm_decision
  -> 无 tool_calls: finish
```

### 19.4 局限性

当前 `graph.py` 中节点只是占位：

```text
没有真正调用 compressor
没有真正调用 LLM
没有真正执行工具
没有接 LangGraph checkpoint
```

### 19.5 面试说法

> 当前 Mini-Nanobot 是手写 ReAct Loop 编排，LangGraph 只是适配点。真正迁移 LangGraph 时，会把 query loop 拆成 prepare_context、llm_decision、execute_tools、checkpoint、finish 等节点，并用条件边实现循环。

---

## 20. CLI 使用流程

### 20.1 是什么

CLI 是项目的用户入口和演示入口。

### 20.2 怎么设计

命令：

```text
tools
run
resume
sessions
memory-add
memory-search
bench
```

### 20.3 工作流程

查看工具：

```bash
python -m mini_nanobot tools
```

运行任务：

```bash
python -m mini_nanobot run "list files in the workspace"
```

使用 OpenAI：

```bash
python -m mini_nanobot run "修复测试失败" --provider openai --execute --write
```

恢复：

```bash
python -m mini_nanobot resume <session_id>
```

记忆：

```bash
python -m mini_nanobot memory-add project "title" "summary" "body"
python -m mini_nanobot memory-search checkpoint
```

Benchmark：

```bash
python -m mini_nanobot bench --file benchmarks/tasks.json
```

### 20.4 局限性

当前 CLI 比较轻：

```text
没有交互式审批
没有 rich UI
没有持续任务监控
没有实时流式输出
```

### 20.5 面试说法

> CLI 让框架能力可以被直接演示，包括工具列表、运行任务、恢复会话、管理长期记忆和跑 benchmark。

---

## 21. Benchmark / Evaluation

### 21.1 是什么

Benchmark 在这里是工程回归基准，不是论文专属评测。

它解决：

```text
如何知道 Agent 改动后有没有退化？
如何统计任务完成率？
如何统计工具调用成功率？
```

### 21.2 怎么设计

任务文件：

```text
benchmarks/tasks.json
```

每条任务：

```json
{
  "name": "list-files-offline",
  "prompt": "list files in the workspace",
  "expect_contains": "...",
  "max_turns": 4
}
```

指标：

```text
completion_rate
tool_success_rate
seconds
```

### 21.3 工作流程

```text
读取 tasks.json
for task in tasks:
  result = engine.submit_message(task.prompt)
  检查 expect_contains
  统计 state.tool_events
输出 JSON 指标
```

### 21.4 局限性

当前是 smoke benchmark：

```text
任务少
离线模型能力有限
没有真实代码修复任务集
没有多模型对比
没有上下文压缩效果量化
```

升级方向：

```text
30+ 代码任务
文件修改任务
测试修复任务
checkpoint 恢复任务
multi-agent 任务
压缩前后 token 对比
```

### 21.5 面试说法

> 我用 benchmark 作为工程回归手段。它用固定 JSON 任务运行 Agent，统计 completion rate、tool success rate 和耗时，帮助验证工具调用、checkpoint、上下文压缩和权限策略有没有被改坏。

---

## 22. Test Suite

### 22.1 是什么

测试保证核心 Runtime 能稳定运行。

### 22.2 怎么设计

当前测试：

```text
test_query_engine.py:
  query loop
  max_turns
  agent.run child agent
  background subagent status

test_tools.py:
  file.write 权限
  file.write/read
  shell dangerous blocking
  recursive fork blocking

test_checkpoint_context.py:
  checkpoint
  context compression

test_memory_skills.py:
  memory
  skills
```

### 22.3 工作流程

运行：

```bash
python -m pytest -q
```

### 22.4 局限性

当前测试偏单元和 smoke：

```text
没有真实 LLM 集成测试
没有并发 race 测试
没有大上下文压力测试
没有 shell sandbox 逃逸测试
```

### 22.5 面试说法

> 我用 ScriptedLLM 做确定性测试，避免测试依赖真实模型输出，从而能稳定覆盖 query loop、工具执行、checkpoint、memory、skill 和 multi-agent。

---

## 23. Claude Agent SDK 迁移思路

### 23.1 是什么

Claude Agent SDK 是更高层的产品化 Agent Runtime。它提供 agent loop、内置工具、context management、sessions、subagents、skills、hooks 等能力。

### 23.2 怎么设计迁移

最稳方案不是完全替换，而是双后端：

```text
local runtime backend:
  当前 Mini-Nanobot 自研 Runtime。

claude-agent-sdk backend:
  用 Claude Agent SDK 跑生产级 Agent Loop。
```

### 23.3 工作流程变化

迁移后：

```text
query():
  不再手写 while loop，而是调用 SDK query/client。

ToolExecutor:
  大部分替换成 SDK 内置工具。

ContextCompressor:
  被 SDK compaction 弱化。

Checkpoint:
  从 AgentState JSON 变成 SDK session transcript + 本地业务索引。

SubAgentRunner:
  被 SDK subagents 替代。
```

保留：

```text
LongTermMemoryStore
Benchmark
CLI
权限策略映射
自定义 MCP tools
业务 session mapping
```

### 23.4 局限性

如果完全迁移到 SDK，就不能再说核心 ReAct Loop、ToolExecutor、ContextCompressor 都是自己实现的。

所以面试更稳的叙述是：

```text
自研 Runtime 展示底层理解
SDK backend 展示企业级接入能力
```

### 23.5 面试说法

> 如果转 Claude Agent SDK，Mini-Nanobot 会从自研 Agent Runtime 变成 SDK 应用层包装。SDK 负责 agent loop、工具执行、上下文管理和 session；Mini-Nanobot 保留长期记忆、benchmark、权限策略映射和自定义工具。最合理的是保留双后端。

---

## 24. 项目边界总表

| 模块 | 当前状态 | 不能夸大成 |
| --- | --- | --- |
| ReAct 编排 | 已实现手写 loop | LangGraph 主编排 |
| Tool System | 已实现 | 企业级插件市场 |
| Permission | 已实现基础权限 | 多租户权限系统 |
| Shell Sandbox | 轻量实现 | 强容器隔离 |
| Checkpoint | SQLite snapshot | event sourcing |
| Memory | Markdown + 关键词召回 | 向量记忆库 |
| Context Compression | 渐进式规则压缩 | 完整 Claude Code 复刻 |
| Skill | 发现 + 懒加载 | 完整技能权限系统 |
| Hook | 生命周期回调 | 分布式事件总线 |
| MCP | Tool adapter | 完整 MCP Client |
| Multi-Agent | 一层 in-process | swarm / remote worker |
| LangGraph | 适配点 | 主执行引擎 |
| Prompt Cache | cache-friendly layout | provider token cache |
| Benchmark | smoke benchmark | 论文级评测 |

---

## 25. 高频面试问答

### Q1：项目和普通 API wrapper 有什么区别？

普通 wrapper 是：

```text
user prompt -> LLM -> answer
```

Mini-Nanobot 是：

```text
状态管理
工具调用
权限控制
上下文压缩
checkpoint
memory
skill
hook
multi-agent
```

### Q2：为什么工具错误不直接抛异常？

因为工具失败是 Agent 可观察的数据。

模型看到：

```text
file not found
old string not found
permission denied
```

可以调整路径、参数或策略。

### Q3：为什么 checkpoint 用 snapshot？

MVP 只需要快速恢复最新状态。

Event sourcing 更适合审计和回放，但复杂度高。

### Q4：Memory 为什么要分类？

不同记忆可信度和用途不同：

```text
user:
  用户偏好

feedback:
  用户纠正

project:
  项目知识

reference:
  外部资料
```

分类后更容易召回和控制。

### Q5：Skill 和 Tool 有什么区别？

```text
Skill:
  说明怎么做。

Tool:
  真正执行。
```

`skill.load` 是 Tool，但 Skill 本身不是执行能力。

### Q6：Hook 和 Tool 有什么区别？

```text
Tool:
  模型主动调用。

Hook:
  框架自动触发。
```

### Q7：MCP 和 Function Calling 有什么区别？

```text
Function Calling:
  LLM -> Agent Runtime。

MCP:
  Agent Runtime -> 外部 MCP Server。
```

### Q8：Multi-Agent 是完整 swarm 吗？

不是。

当前是：

```text
一层 in-process delegation
禁止递归
权限降级
结构化返回
```

### Q9：LangGraph 在项目中真正做了什么？

当前只是适配点。

主执行路径仍是：

```text
手写 ReAct Loop
```

### Q10：项目最大亮点是什么？

> 最大亮点是把代码 Agent 做成可恢复、可控副作用、可扩展的 Runtime，而不是一次性 prompt 调用。特别是 ToolExecutor、Permission/Sandbox、Checkpoint、ContextCompressor、Memory 和一层 SubAgentRunner 组合起来，让长任务执行更稳定。

---

## 26. 简历表述

短版：

> Mini-Nanobot：面向代码任务的轻量级 Agent Runtime。基于 ReAct 实现 LLM 推理、工具调用、结果反馈循环；设计 JSON Schema 驱动的插件式 Tool System，内置文件、搜索、Git、Shell、Skill 和子 Agent 工具；实现权限分级、Shell 安全策略、工作区路径隔离和超时控制；基于 SQLite Checkpoint 支持中断恢复；通过 Memory、Skill 和上下文压缩提升长任务连续性，并实现一层受限 Multi-Agent 委派。

展开版：

> 我把代码 Agent 拆成了会话层、执行层、工具层、安全层、记忆层和恢复层。会话层由 QueryEngine 管理，执行层是手写 ReAct Loop，工具层用 ToolRegistry 和 ToolExecutor 统一工具协议，安全层用权限模型和 Shell Sandbox 控制副作用，记忆层用短期 AgentState 和长期 Markdown Memory 做上下文延续，恢复层用 SQLite Snapshot Checkpoint。后续我补了一层 SubAgentRunner，让主 Agent 能通过 agent.run 委派只读分析、代码审查或测试定位任务，同时保持权限降级和上下文隔离。

---

## 27. 最终复习口诀

```text
入口看 QueryEngine
循环看 query
状态看 AgentState
模型看 LLMProvider
工具看 ToolRegistry + ToolExecutor
权限看 PermissionLevel
Shell 安全看 Sandbox
文件安全看 resolve_workspace_path
恢复看 Checkpoint
长期记忆看 LongTermMemoryStore
上下文看 ContextCompressor
方法论看 Skill
横切逻辑看 Hook
外部协议看 MCPToolAdapter
子任务看 SubAgentRunner
图编排看 graph.py
评估看 benchmark
```

最终一句话：

> Mini-Nanobot 当前是一个以手写 ReAct Loop 为核心的轻量代码任务 Agent Runtime，重点展示 Agent 执行闭环、工具安全、状态恢复、上下文管理和一层子 Agent 委派；LangGraph、MCP、Prompt Cache 和强沙箱都保留了扩展方向，但不会在面试中夸成完整生产级实现。

---

## 28. 深度追问版：把之前问过的细节补齐

前面 1-27 章是结构化主线，这一章是面试追问版。它专门补那些容易被问到、也容易混淆的细节：一次完整对话怎么流动、meta message 为什么每轮注入、长期记忆到底查哪里、checkpoint 为什么存 JSON、上下文窗口里到底有什么、Multi-Agent 的 `fork_runner` 在哪里开始、LangGraph 为什么只是适配点、LLMProvider 怎么接真实模型。

### 28.1 从开启一个对话到连续两轮消息，系统完整做了什么

第一轮用户输入：

```text
帮我分析 checkpoint 是怎么做的
```

系统执行：

```text
1. QueryEngine.submit_message(task, session_id=None)
2. 因为没有 session_id，创建新的 AgentState
3. AgentState 生成新的 session_id
4. SystemPromptBuilder.build(workspace) 生成 system prompt
5. state.messages 加入 role="system" 的稳定规则
6. _inject_dynamic_context(state, task)
   - 注入 memory-index
   - 根据 task 召回 recalled-memory
   - 注入 skills-menu
7. 加入用户真实消息 role="user", is_meta=False
8. 注入 runtime metadata
   - skill_manager
   - fork_depth
   - fork_runner
   - subagent_runner
9. 调 query(state)
10. query 每轮先检查上下文压缩
11. LLMProvider.generate(messages, tools, state)
12. 模型根据工具 schema 可能输出 file.read / search.rg 等 tool_calls
13. ToolExecutor 执行工具
14. 工具结果写回 role="tool"
15. checkpoint.save(state)
16. LLM 再读工具结果继续推理
17. 模型最终不再调用工具，返回文本
18. state.completed = True
19. state.final_response = response.text
20. checkpoint.save(state)
```

第二轮用户输入：

```text
那它和 event sourcing 有什么区别？
```

系统执行：

```text
1. QueryEngine.submit_message(task, session_id=上一轮 session_id)
2. SQLiteCheckpointStore.load(session_id)
3. AgentState.from_json(state_json)
4. 恢复上一轮 messages / tool_events / recent_files / summaries
5. state.completed = False
6. 重新注入本轮动态上下文
   - memory-index 可能仍然注入
   - recalled-memory 会基于新问题重新召回
   - skills-menu 会重新扫描
7. 追加当前用户真实消息
8. 重新注入 runtime metadata
   - 因为 fork_runner / skill_manager 这类运行时对象不进 checkpoint
9. 调 query(state)
10. query 在历史上下文 + 本轮 meta + 本轮 user message 的基础上继续执行
```

关键理解：

```text
第二轮不是把旧 prompt 和新 prompt “融合成一个字符串”。
第二轮是在已有 AgentState.messages 后面追加本轮动态 meta messages 和当前用户真实消息。
```

易错说法：

```text
错：第二轮会重新生成一个全新的上下文。
对：第二轮会恢复同一个 session_id 对应的 AgentState，再追加本轮输入。

错：checkpoint 保存了 Python 运行时对象。
对：checkpoint 只保存 JSON 可序列化状态，运行时对象由 QueryEngine 重新注入。
```

### 28.2 system prompt、user message、meta message 到底怎么区分

真实 API message role 通常有：

```text
system
user
assistant
tool
```

Mini-Nanobot 的 `Message` 也有这些 role。

但是项目多了一个内部字段：

```python
is_meta: bool
```

它不是 API role，而是框架内部标记。

三类最重要的消息：

```text
system prompt:
  role="system"
  is_meta=False
  内容是稳定规则，例如 Agent 身份、安全原则、工具使用原则。

用户真实请求:
  role="user"
  is_meta=False
  内容是用户本轮真正说的话。

动态 meta message:
  role="user"
  is_meta=True
  内容是框架注入的 memory、skills、压缩摘要、parent delegation 等。
```

为什么很多动态信息也用 `role="user"`？

```text
因为不同 LLM API 对自定义 role 支持有限。
很多系统级动态信息只能通过 user-role message 注入。
为了避免和用户真实输入混淆，框架用 is_meta=True 标记。
```

为什么包 `<system-reminder>`？

```text
这是内容标签，不是 API role。
它提醒模型：这段内容是运行时系统提醒，不是用户自然语言请求。
```

用户说：

```text
你是一名腾讯大厂技术面试官，请帮我...
```

这不是 system prompt，而是：

```text
role="user", is_meta=False
```

它的优先级低于真正 system prompt。

面试说法：

> Mini-Nanobot 区分 API role 和内部 meta 标记。真正的 system prompt 放稳定规则；用户真实输入是普通 user message；memory、skills、压缩摘要等动态上下文用 role=user 但标记 is_meta=True，并用 `<system-reminder>` 包装。

### 28.3 为什么每一轮都要重新注入 meta messages

很多人会问：

```text
既然是同一个 session，上一轮已经注入过 memory 和 skills，为什么下一轮还要注入？
```

原因：

```text
1. 当前用户问题变了，recalled-memory 应该重新按新 query 召回。
2. 长任务中上下文可能被压缩，旧 meta message 可能已经不在 tail 附近。
3. skills 目录可能变化，需要重新扫描。
4. 动态信息靠近当前用户消息，模型更容易利用。
5. 未来 AGENTS.md、上传文件索引、MCP resources 都可能每轮变化。
```

当前每轮注入：

```text
memory-index
recalled-memory
skills-menu
```

未来可以注入：

```text
AGENTS.md / project instructions
用户上传文件索引
MCP resources
最近压缩恢复信息
安全策略变化
当前 git branch/status
```

局限性：

```text
当前 MVP 可能重复注入相似 meta messages。
生产级应该做去重、版本 hash、只在变化时注入。
```

一句话：

> 动态 meta message 不是“继承旧内容”，而是每轮根据当前问题和当前环境重新生成，让模型在最新上下文里拿到最相关的提示。

### 28.4 长期记忆到底查哪里，MEMORY.md 又是什么

长期记忆路径：

```text
.nanobot/memory/{workspace_hash}/memory/
```

例子：

```text
.nanobot/memory/abc123.../memory/
├── MEMORY.md
├── user_interview_style.md
├── feedback_explain_from_problem.md
├── project_checkpoint_snapshot.md
└── reference_claude_code_memory.md
```

`MEMORY.md` 是索引，不是完整正文。

它存：

```markdown
# Memory Index

- [Interview Style](user_interview_style.md) - 用户希望从问题出发讲解。
- [Checkpoint Snapshot](project_checkpoint_snapshot.md) - Mini-Nanobot 使用 SQLite snapshot。
```

真正参与召回的是具体 memory 文件：

```text
user_*.md
feedback_*.md
project_*.md
reference_*.md
```

召回范围：

```text
只查当前 workspace_hash 下的 memory。
不会查所有 workspace 的 memory。
```

召回字段：

```text
title + summary + body
```

不直接查 `MEMORY.md` 做匹配。`MEMORY.md` 的作用是：

```text
每轮作为 memory-index 给模型看，让模型知道长期记忆库里有哪些条目。
```

召回流程：

```text
1. 当前用户 query 分词。
2. 遍历当前 workspace_hash 下具体 memory 文件。
3. 跳过 MEMORY.md。
4. 解析 frontmatter 和 body。
5. 在 title + summary + body 中匹配关键词。
6. 命中越多 score 越高。
7. 取 top 5。
8. 包装成 recalled-memory meta message。
```

为什么召回后加这句话：

```text
Treat recalled memory as a hint. Verify paths/functions against the current workspace before relying on it.
```

因为长期记忆可能过期。模型应该把它当线索，而不是绝对事实。

易错说法：

```text
错：MEMORY.md 里存完整记忆。
对：MEMORY.md 是索引，完整内容在具体 memory 文件。

错：召回会查所有项目的记忆。
对：当前只查当前 workspace hash 下的记忆。

错：记忆召回后模型可以直接相信。
对：记忆只是 hint，当前代码和工具验证优先。
```

### 28.5 Checkpoint 为什么一定要 JSON，为什么运行时对象不能保存

Checkpoint 保存的是：

```text
AgentState 的可恢复业务状态
```

通过：

```python
AgentState.to_json()
```

存到 SQLite：

```text
checkpoints.state_json
```

为什么转 JSON：

```text
1. 可读，方便调试。
2. 可存 SQLite。
3. 跨语言友好。
4. 强迫状态只包含数据，不包含运行时对象。
5. 恢复路径明确：from_json -> AgentState。
```

保存：

```text
messages
tool_events
usage
turns
recent_files
compacted_summaries
completed
final_response
```

不保存：

```text
LLM client
SkillManager
HookManager
SubAgentRunner
fork_runner
数据库连接
函数对象
subprocess process
asyncio task
文件句柄
```

为什么不能保存运行时对象：

```text
1. JSON 无法序列化函数、连接、进程句柄等对象。
2. 这些对象和当前进程生命周期绑定，重启后即使反序列化也不可用。
3. 保存它们会让 checkpoint 变成不可移植、不可调试。
4. 恢复时应该重新创建运行环境，而不是复活旧对象。
```

`is_meta=True` 的消息是不是运行时对象？

```text
不是。
meta message 仍然是 Message 数据，content 是字符串，可以 JSON 序列化。
运行时对象是 Python 函数、manager、client、连接、任务等。
```

为什么是 snapshot，不是 event sourcing：

```text
snapshot:
  保存“现在是什么状态”。

event sourcing:
  保存“状态如何一步步变化”。
```

当前项目优先恢复最新状态，所以 snapshot 更简单。

### 28.6 上下文窗口中到底有什么，为什么 72% 就压缩

上下文窗口不只是用户输入。

里面包括：

```text
system prompt
tool schemas
memory-index
recalled-memory
skills-menu
历史 user messages
历史 assistant messages
tool call records
tool results
context-collapse summaries
autocompact summaries
当前用户消息
协议开销
输出 tokens 预留
模型 reasoning tokens
```

假设：

```text
max_context_tokens = 32k
output_reserve_tokens = 4k
```

有效输入窗口：

```text
effective_window = 28k
```

72% 的意思：

```text
不是上下文爆了。
而是进入黄色预警区，开始做低损耗压缩。
```

为什么不是等 100%：

```text
1. 工具结果可能突然很大。
2. token 估算可能有误差。
3. 模型还要输出。
4. 有些模型还有 reasoning token。
5. 提前做低损耗压缩比最后强制 autocompact 更安全。
```

超过 90% 是否直接 autocompact？

```text
不是。
当前会按层级走：
history_snip -> microcompact -> 重新计算 -> context_collapse -> 重新计算 -> autocompact
```

五级压缩分别做什么？

```text
Level 1: Tool Result 预算裁剪
  发生在工具刚执行完。
  对单次工具结果设置最大上下文预算。
  超过预算时完整内容落盘，messages 只保留紧凑 preview 和 artifact path。

Level 2: History Snip
  发生在上下文压缩阶段。
  用启发式规则清理历史冗余：
    - 重复工具输出，例如多次 ls/file.list 得到相同内容。
    - 被后续编辑覆盖的中间 file.write/file.patch 尝试。
    - 仍然很长的 tool message 做 head-tail 裁剪。

Level 3: Microcompact
  只保留全局最近 5 条 role="tool" 消息。
  更早的工具结果如果在 5 分钟 cache ttl 内，替换成 cache_reference 风格占位。
  如果已经过期，直接替换成 removed placeholder，并尽量保留 artifact path。

Level 4: Context Collapse
  不删除原始 state.messages。
  创建 state.context_projection，作为下一次 LLM 调用的模型上下文视图。
  projection 中保留 system、context-collapse summary 和最近 6 条消息。

Level 5: Autocompact
  最后兜底。
  优先调用 QueryEngine 注入的 compact_summarizer，使用 summary child state 生成摘要。
  如果没有真实 LLM 或 summarizer 失败，就用确定性 fallback summary。
  projection 中保留 autocompact summary、recovery attachment 和最近 4 条消息。
```

Microcompact 的“最近 5 条工具结果”是什么意思？

```text
是全局最近 5 条 role="tool" 消息，
不是每个工具各保留 5 条。
目的是保留最近观察结果，减少旧工具结果占用。
生产级可以基于任务、文件引用、冷热缓存和 provider prompt cache 做更细策略。
```

cache_reference 在当前项目里是什么？

```text
它是一个“协议占位”，不是服务端真实 KV cache。

当前项目没有 provider 级 prompt cache 控制权，所以不能真的让服务端 mask 某段工具结果。
我们做的是：
  - 如果旧 tool result 仍在 5 分钟 TTL 内，就写入 [microcompact cache_reference=xxx] 占位。
  - 这个占位表达“真实系统中这里可以引用服务端缓存”。
  - 未来接 provider cache 时，可以把这个 id 映射为真正 cache reference。
```

Context Collapse 为什么说不删除原始消息？

```text
因为 AgentState 现在有：
  messages: 原始历史
  context_projection: 模型上下文投影视图

query() 调 LLM 时使用 state.active_messages()。
如果 context_projection 存在，模型看到 projection；
但 state.messages 仍然保留完整 canonical history。
```

### 28.7 Tool、Skill、Hook、MCP、Function Calling 的边界

这五个概念最容易混。

一句话区分：

```text
Function Calling:
  模型如何表达“我要调用工具”。

Tool:
  框架内部统一执行能力。

Skill:
  工作流提示词，告诉模型怎么做。

Hook:
  框架生命周期回调，不暴露给模型。

MCP:
  外部工具/资源接入 Agent 的协议。
```

完整链路：

```text
用户自然语言
  -> LLM 结合 tools JSON Schema
  -> Function Calling 输出 tool_call
  -> ToolRegistry 根据 tool_call.name 找 Tool
  -> ToolExecutor 执行 Tool
  -> 如果 Tool 来自 MCP，则 MCPToolAdapter 调外部 server
  -> ToolResult 写回 messages
  -> LLM 继续推理
```

Skill 的位置：

```text
skills-menu 告诉模型有哪些 Skill
模型调用 skill.load
SkillTool 读取 SKILL.md
Skill 内容作为 ToolResult 写回上下文
模型根据 Skill 方法论调用真正 Tool
```

Hook 的位置：

```text
ToolExecutor 在执行 Tool 前后自动触发
模型不知道 Hook 存在
```

MCP 的位置：

```text
模型不知道 MCP
模型只看到普通 Tool schema
Runtime 通过 MCPToolAdapter 把外部 MCP tool 包装成内部 Tool
```

易错说法：

```text
错：Skill 也是 Tool。
对：skill.load 是 Tool，Skill 本身是提示词工作流。

错：Hook 是模型可以调用的工具。
对：Hook 是框架自动调用的生命周期函数。

错：Function Calling 会执行工具。
对：Function Calling 只让模型输出 tool_call。

错：MCP 和 Function Calling 是同一个东西。
对：Function Calling 是模型到 Runtime；MCP 是 Runtime 到外部 Server。
```

### 28.8 ShellTool 为什么看起来像接口，它到底做什么

`ShellTool` 的确像适配接口，因为它不是直接把所有 shell 逻辑写死。

它的作用是把 shell 命令执行包装成 Agent Tool：

```text
对模型:
  暴露 shell.run schema。

对权限模型:
  判断命令是 read-only、normal、destructive。

对 sandbox:
  调 ShellSandboxExecutor 真正执行。

对上下文:
  把 stdout/stderr 包装成 ToolResult。
```

层级：

```text
LLM:
  输出 shell.run tool_call

ShellTool:
  工具适配层，做 schema、权限语义、结果包装

CommandSafetyPolicy:
  命令安全判断

ShellSandboxExecutor:
  真正 subprocess 执行
```

CWD 是：

```text
current working directory
命令执行时所在目录
```

项目中固定为：

```text
ctx.workspace
```

避免命令跑到系统目录或未知目录。

### 28.9 权限模型为什么不放进 Sandbox

因为权限模型不是 Shell 专属。

它还管：

```text
file.write
file.patch
git mutation
agent.run 子 Agent 权限降级
MCP tools
未来外部服务写入
```

Sandbox 主要管：

```text
允许 shell 执行后，如何限制执行环境和风险。
```

所以两者分层：

```text
Permission Model:
  能不能做。

Sandbox:
  允许做之后怎么受限执行。
```

如果把权限都塞进 sandbox，会导致：

```text
file.write 等非 shell 工具无法复用权限逻辑
权限策略和执行隔离耦合
子 Agent 权限降级不好做
```

### 28.10 Multi-Agent 里 fork_depth、fork_runner、SubAgentRunner.run 到底在哪里

原始 ToolExecutor 流程没有变：

```text
ToolExecutor
  -> registry.get(tool_call.name)
  -> validate_input
  -> PreToolUse Hook
  -> check_permissions
  -> tool.run()
  -> PostToolUse Hook
  -> ToolResult
```

当：

```text
tool_call.name == "agent.run"
```

ToolRegistry 找到的是：

```text
AgentTool 实例
```

因为：

```python
class AgentTool(Tool):
    name = "agent.run"
```

所以：

```text
tool.run() 实际就是 AgentTool.run()
```

`AgentTool.run()` 里面：

```text
1. depth = ctx.metadata.get("fork_depth", 0)
2. depth >= 1 就拒绝 recursive fork
3. fork_runner = ctx.metadata.get("fork_runner")
4. await fork_runner(args, ctx)
```

`fork_runner` 从哪里来？

在 `QueryEngine.submit_message()` 中注入：

```python
state.metadata["fork_runner"] = self.subagents.run
state.metadata["subagent_runner"] = self.subagents
```

所以：

```text
fork_runner 实际就是 SubAgentRunner.run
```

为什么这样设计？

```text
AgentTool 是工具层，不应该直接 new SubAgentRunner。
SubAgentRunner 需要 workspace、llm、registry、checkpoint、hooks 等运行时依赖。
这些依赖由 QueryEngine 拥有。
所以 QueryEngine 注入 fork_runner，AgentTool 只调用它。
```

完整展开：

```text
ToolExecutor 调 AgentTool.run()
  -> AgentTool 检查 fork_depth
  -> AgentTool 调 fork_runner
      -> SubAgentRunner.run()
          -> 创建 child AgentState
          -> 注入 child system prompt
          -> 生成 child registry / permissions
          -> 调 query(child_state)
          -> 返回 SubAgentResult
  -> AgentTool 返回 ToolResult
  -> ToolExecutor 触发 PostToolUse
```

### 28.11 子 Agent profile 是不是提前写好的 Agent

是预先写在代码里的 profile，但不是独立 Agent 类。

当前：

```text
researcher
reviewer
tester
coder
default
```

它们只是工具和权限模板。

例如：

```text
reviewer:
  默认只读工具。

tester:
  只读 + shell.run，但前提是父 Agent 有 EXECUTE_SAFE。

coder:
  只读 + file.write/file.patch/shell.run，但前提是父 Agent 有对应权限。
```

创建子 Agent 时：

```text
SubAgentRunner 不是创建 ReviewerAgent 类。
它创建普通 child AgentState。
然后根据 subagent_type 给它不同工具白名单和权限。
```

一句话：

> profile 是子 Agent 权限模板，不是多个独立 Agent 实现。

未来可升级：

```yaml
profiles:
  reviewer:
    allowed_tools:
      - file.read
      - search.rg
      - git.diff
    max_turns: 8
    system_addendum: |
      You are a careful reviewer...
```

### 28.12 LangGraph 如果真正实现，要不要删 query

当前：

```text
query() = 主执行 loop
graph.py = 适配点
```

真正迁移 LangGraph 后，不一定删 `query()` 这个函数。

更合理：

```text
保留 query() 作为外部 API
把 query() 内部的 while loop 换成 compiled_graph.ainvoke()
```

不要同时维护：

```text
query() 里一套 loop
LangGraph 里又一套 loop
```

否则会导致：

```text
checkpoint 时机不一致
工具执行逻辑重复
上下文压缩时机不同
bug 难定位
```

LangGraph 节点应该是：

```text
prepare_context:
  压缩上下文、准备动态上下文。

llm_decision:
  调 LLM。

execute_tools:
  调 ToolExecutor。

checkpoint:
  保存状态。

finish:
  结束。
```

条件边：

```text
有 tool_calls -> execute_tools -> checkpoint -> llm_decision
无 tool_calls -> finish
```

### 28.13 LLMProvider、离线模型、本地模型、API 模型怎么区分

`LLMProvider` 是模型适配层。

项目统一调用：

```python
llm.generate(messages, tools, state)
```

当前 provider：

```text
RuleBasedLLM:
  离线规则模型，不是真本地大模型。

ScriptedLLM:
  测试用，按预设返回 LLMResponse。

OpenAIProvider:
  调 OpenAI API。
```

离线模型在当前项目中指：

```text
RuleBasedLLM
```

它不是：

```text
Ollama
Qwen 本地部署
DeepSeek 本地服务
Llama.cpp
vLLM
```

它只是规则 demo：

```text
看到 list files -> 返回 file.list
看到 search -> 返回 search.rg
```

API 模式：

```powershell
$env:OPENAI_API_KEY="你的 key"
python -m mini_nanobot run "分析项目结构" --provider openai --model gpt-4.1-mini
```

当前没有登录流程，OpenAI SDK 读取：

```text
OPENAI_API_KEY
```

如果要接本地大模型，需要新增：

```text
OllamaProvider
LocalOpenAICompatibleProvider
VLLMProvider
```

只要实现：

```python
generate(messages, tools, state) -> LLMResponse
```

`query()` 不需要改。

### 28.14 Benchmark 为什么不是论文专属

Benchmark 在论文里是评测基准，在工程里也可以是回归基准。

Agent 项目尤其需要 benchmark，因为它有很多不稳定因素：

```text
模型输出变化
工具调用失败
权限拦截
上下文压缩丢信息
checkpoint 恢复失败
multi-agent 委派失败
```

当前 benchmark 做：

```text
读取 benchmarks/tasks.json
运行每个 task
检查 expect_contains
统计 completion_rate
统计 tool_success_rate
统计 seconds
```

当前是 smoke benchmark。

未来要做成简历指标，需要扩展：

```text
30+ 文件搜索任务
30+ 文件修改任务
20+ 测试修复任务
10+ checkpoint resume 任务
10+ 上下文压缩任务
10+ multi-agent 委派任务
```

指标：

```text
任务完成率
工具调用成功率
平均工具调用次数
平均耗时
压缩前后 token 降幅
resume 成功率
子 Agent 委派成功率
```

### 28.15 Claude Agent SDK 迁移时状态会发生什么变化

如果迁移 Claude Agent SDK，Mini-Nanobot 的角色会变化。

当前：

```text
Mini-Nanobot 自己实现 Agent Runtime
```

迁移后：

```text
Claude Agent SDK 提供 Runtime
Mini-Nanobot 变成应用层策略包装
```

会被弱化或替换：

```text
query() ReAct Loop
ToolExecutor
ContextCompressor
SubAgentRunner
部分 Checkpoint
```

仍然保留价值：

```text
LongTermMemoryStore
Benchmark
CLI
权限策略映射
业务 session mapping
自定义 MCP tools
项目知识管理
```

状态变化：

```text
现在:
  SQLite 保存完整 AgentState JSON。

迁移后:
  SDK 保存 session transcript。
  Mini-Nanobot SQLite 保存业务索引：
    nanobot_session_id
    sdk_session_id
    workspace
    task
    final_response
    memory_refs
    benchmark metrics
```

最稳方案：

```text
保留 local runtime backend
新增 claude-agent-sdk backend
```

面试说法：

> 我不会把自研 Runtime 完全删掉。自研版本展示我理解 ReAct、工具执行、checkpoint 和上下文管理的底层机制；Claude Agent SDK backend 则展示如何对接企业级 Agent Runtime。
