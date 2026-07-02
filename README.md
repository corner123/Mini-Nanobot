# Mini-Nanobot

Mini-Nanobot is a lightweight Python framework for code-task agents. It provides a small but complete runtime for ReAct-style execution, tool calling, checkpoint recovery, context compression, long-term memory, hooks, and sandboxed shell commands.

The project is designed for learning and experimentation: the default offline model lets the framework run without API keys, while the LLM layer can be swapped for an API-backed provider.

## Features

- ReAct-style agent loop with tool feedback.
- Plugin-like tool registry based on JSON Schema.
- Built-in tools for files, ripgrep search, Git, shell commands, skills, and sub-agent integration.
- Shell command safety checks, workspace path isolation, timeouts, output caps, and permission levels.
- SQLite checkpoints for resumable long-running tasks.
- Token-aware context compression with progressive cleanup strategies.
- Session and long-term memory primitives.
- Lifecycle hooks for tool and session events.

## Quick Start

```bash
python -m mini_nanobot tools
python -m mini_nanobot run "list files in the workspace"
python -m mini_nanobot sessions
python -m mini_nanobot bench --file benchmarks/tasks.json
```

By default, Mini-Nanobot uses an offline rule-based LLM for deterministic demos. To use an OpenAI-compatible provider:

```bash
python -m mini_nanobot run "修复测试失败" --provider openai --execute --write
```

## Permission Model

- `READ_ONLY`: file reads, search, and safe Git inspection.
- `WRITE_WORKSPACE`: file writes and patching.
- `EXECUTE_SAFE`: non-destructive shell commands.
- `DANGEROUS`: destructive commands, denied by default.

## Development

```bash
python -m pytest -q
python -m mini_nanobot bench --file benchmarks/tasks.json
```

## Status

This is a compact educational implementation, not a production coding assistant. It intentionally keeps the architecture easy to inspect and extend.
