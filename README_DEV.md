# README_DEV

Developer handoff and implementation notes for `Anuris_ref`.

## 1) Scope and Current Entry Point

- Active implementation lives in `V1/`.
- Main entrypoint remains `V1/Anuris_rebuild.py` (thin wrapper).
- Core package is `V1/anuris/`.
- Runtime architecture overview is in `V1/ARCHITECTURE.md`.

## 2) Quick Start (Local Development)

```bash
cd V1
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python Anuris_rebuild.py --base-url <URL> --model <MODEL> --api-key <KEY>
```

Optional first-run persistence:

```bash
python Anuris_rebuild.py --base-url <URL> --model <MODEL> --api-key <KEY> --save-config
```

Config is stored at:

- `~/.anuris_config.toml`

## 3) Runtime Modes

### Standard chat mode

- Uses streaming response path.
- Supports attachments and reasoning display from stream parser.

### Agent mode

- Default status is `ON` at startup (`self.agent_mode = True`).
- Toggle with `/agent on` and `/agent off`.
- Status via `/agent status`.
- New UX behavior: immediate status output is shown before tool loop starts:
  - `[agent] processing request...`
  - `[agent] round N...`
  - `[tool] ...`

## 4) Command Surface

From interactive CLI:

- `/clear`
- `/save [filename]`
- `/load [filename]`
- `/attach <file...>`
- `/detach [index]`
- `/files`
- `/help`
- `/agent [on|off|status]`
- `/todos` (renders current TodoWrite board from agent loop)
- `/tasks` (renders persistent file-backed task board from agent loop)

## 5) Reasoning Switch (Provider-aware)

A provider-aware reasoning switch is implemented in config and CLI args:

- Config field: `reasoning` (bool, default `true`)
- CLI arg: `--reasoning on|off`

Current behavior:

- For DeepSeek-like providers (detected by `base_url` or `model` containing `deepseek`), request payload includes:
  - `extra_body = {"thinking": {"type": "enabled"|"disabled"}}`
- For non-DeepSeek providers, no extra reasoning payload is attached.

## 6) Agent Implementation Status (learn-claude-code inspired)

Implemented:

- s01/s02: tool-call loop + tool execution
  - `bash`
  - `read_file`
  - `write_file`
  - `edit_file`
- DeepSeek compatibility fix for tool-call turns:
  - assistant messages in loop preserve `reasoning_content` when required
- s03: TodoWrite support
  - in-memory todo board with validation
  - single `in_progress` constraint
- s04: subagent support (`task` tool)
  - child agent loop can run with fresh context
  - capability gating by `agent_type`
- s07: persistent task system (`task_create/task_get/task_update/task_list`)
  - task data is stored under `.anuris_tasks/` in the workspace
  - supports status transitions and dependency updates

## 7) Key Files for Ongoing Work

Core orchestration:

- `V1/anuris/state_machine.py`
- `V1/anuris/model.py`
- `V1/anuris/streaming.py`

Agent internals:

- `V1/anuris/agent/loop.py`
- `V1/anuris/agent/executor.py`
- `V1/anuris/agent/schemas.py`
- `V1/anuris/agent/todo.py`
- `V1/anuris/agent/tools.py` (compatibility facade)
- `V1/anuris/agent/tasks.py`
- `V1/anuris/agent/__init__.py`

CLI/bootstrap/config:

- `V1/anuris/bootstrap.py`
- `V1/anuris/config.py`
- `V1/anuris/commands.py`
- `V1/anuris/ui.py`

Tests:

- `V1/tests/test_agent_loop.py`
- `V1/tests/test_agent_tools.py`
- `V1/tests/test_model.py`
- `V1/tests/test_bootstrap.py`
- `V1/tests/test_commands.py`
- `V1/tests/test_streaming.py`

## 8) Test and Validation Commands

Run all unit tests:

```bash
cd V1
python -m unittest discover -s tests -v
```

Quick syntax check for touched files:

```bash
python -m py_compile anuris/agent/loop.py anuris/state_machine.py tests/test_agent_loop.py
```

## 9) Known Pitfalls

- DeepSeek tool-call flows may fail if historical assistant tool-call messages omit `reasoning_content`.
- Running tests from wrong directory can break imports (run from `V1/`, not repository root).
- `AgentToolExecutor.run_bash` is intentionally constrained but still shell-based; keep prompts/tool policies conservative.

## 10) Suggested Next Steps

- Improve progress UX (single-line spinner, throttled tool logs, optional verbose mode).
- Add s05 skill loading (`load_skill`) with two-layer metadata/body injection.
- Add s06 context management (micro-compact + manual compact command).
- Expand subagent policy (explicit read-only explore mode and stricter tool budgets).
- Add higher-level integration smoke tests for `/agent` command flow.

## 11) Daily Handoff Checklist

Before ending a dev session:

1. Run unit tests in `V1/`.
2. Confirm no unintended file changes via `git status`.
3. Update this file if behavior or command surface changed.
4. Record any unresolved issue with reproduction steps.
