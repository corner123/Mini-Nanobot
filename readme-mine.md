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
```

### 14.3 工作流程

每轮 LLM 前：

```text
before = count_messages(state.messages)
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
history_snip:
  裁剪长 tool message。

microcompact:
  旧 tool result 替换成提示，保留最近 8 条。

context_collapse:
  更早历史变成摘要 meta message。

autocompact:
  极限压缩，保留 recovery attachment。
```

### 14.4 局限性

当前压缩是确定性规则：

```text
没有 LLM summary provider
History Snip 不完整
没有 cache-aware microcompact
没有 provider cache reference
压缩摘要质量有限
```

### 14.5 面试说法

> 我在每轮 LLM 调用前检查 token 预算，超过阈值就按低损耗到高损耗逐层压缩：先裁剪长工具结果，再清理旧工具结果，再折叠历史，最后 autocompact。这样能避免长任务因为工具结果膨胀而爆上下文。

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
