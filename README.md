# Mini-Nanobot

Mini-Nanobot 是一个面向代码任务的轻量级 Agent 框架。它参考现代代码 Agent 的通用工程思想，但实现是原创 Python 版本：双层查询循环、插件式工具、Shell 安全策略、上下文压缩、checkpoint 恢复、两级记忆、技能懒加载、Hook 扩展和子 Agent 集成点。

## 架构映射

- `QueryEngine`：外层会话生命周期，负责 system prompt、memory、skills、预算、checkpoint。
- `query()`：内层单次 ReAct 循环，负责 LLM 决策、工具执行、结果注入、压缩和恢复。
- `ToolRegistry`：统一注册内置工具、MCP 适配工具和 AgentTool。
- `StreamingToolExecutor`：只读工具可并发，写入/危险工具串行并做权限检查。
- `ContextCompressor`：实现 tool result snip、history snip、microcompact、context collapse、autocompact。
- `SQLiteCheckpointStore`：每轮工具执行后保存状态，可 resume。
- `LongTermMemoryStore`：`user / feedback / project / reference` 四类记忆，索引注入 + 召回注入。
- `SkillManager`：只注入技能元数据，真正调用时再读取 `SKILL.md`。
- `HookManager`：暴露 `PreToolUse / PostToolUse / SessionStart / SessionEnd / CompactStart / CompactEnd`。

## 快速运行

```bash
python -m mini_nanobot tools
python -m mini_nanobot run "list files in the workspace"
python -m mini_nanobot sessions
python -m mini_nanobot bench --file benchmarks/tasks.json
```

默认使用离线 `RuleBasedLLM`，便于无 API key 测试。要接真实模型，可安装 `mini-nanobot[openai]` 并使用：

```bash
python -m mini_nanobot run "修复测试失败" --provider openai --execute --write
```

## 权限模型

- `READ_ONLY`：文件读取、搜索、git status/diff/log/show。
- `WRITE_WORKSPACE`：文件写入和 patch。
- `EXECUTE_SAFE`：执行非破坏性 shell 命令。
- `DANGEROUS`：破坏性命令，默认拒绝。

Shell 工具会做危险指令过滤、超时控制、环境变量脱敏、输出上限和工作区隔离。

## 面试讲法

这个项目不是 prompt wrapper，而是把代码任务 Agent 拆成五层：会话层、查询状态机、工具协议、安全执行层和上下文/记忆层。复杂任务失败后可以从 SQLite checkpoint 恢复；上下文逼近窗口时先做低成本压缩，最后才做 autocompact；工具错误不会让进程崩溃，而是作为 `tool_result` 返回给模型自我修正。
