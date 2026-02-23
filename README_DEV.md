# README_DEV

Developer handoff and implementation notes for `Anuris_ref`.

## 1) Scope and Current Entry Point

- Active implementation lives in `V1/`.
- Main entrypoint remains `V1/Anuris_rebuild.py` (thin wrapper).
- Core package is `V1/anuris/`.
- Runtime architecture overview is in `V1/ARCHITECTURE.md`.
- Agent workspace root is anchored to `V1/` (resolved from `anuris/cli.py`), not the shell CWD.

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
- `/skills` (renders discovered local skills for `load_skill`)
- `/compact [focus]` (manually compacts conversation context for long sessions)
- `/background [task_id]` (shows background task status; alias: `/bg`)
- `/team` (renders teammate roster and status summary)
- `/inbox [name]` (drains inbox for lead or a specific teammate)
- `/plans` (shows tracked plan approval requests)
- `/shutdowns` (shows tracked shutdown request status)

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
- s05: skill loading (`load_skill`)
  - two-layer pattern: metadata in system instruction + full body via tool result
  - skill discovery dirs: `.anuris_skills/` then `skills/` under workspace
- s06: context management
  - micro-compact clears older large tool outputs
  - auto-compact triggers by token threshold with transcript snapshot in `.anuris_transcripts/`
  - manual compact available via `/compact [focus]`
- s08: background tasks
  - tools: `background_run`, `check_background`
  - background completion notifications are drained into loop context each round
  - CLI inspection via `/background [task_id]` (or `/bg`)
- s07: persistent task system (`task_create/task_get/task_update/task_list`)
  - task data is stored under `.anuris_tasks/` in the workspace
  - supports status transitions and dependency updates
- s09: team collaboration foundations
  - tools: `spawn_teammate`, `list_teammates`, `send_message`, `read_inbox`, `broadcast`
  - file-backed roster and inbox store under `.anuris_team/`
  - CLI inspection via `/team` and `/inbox [name]`
- s10: protocol governance for teammates
  - tools: `shutdown_request`, `shutdown_status`, `shutdown_list`
  - tools: `plan_review`, `plan_list` (teammates submit plan requests)
  - request tracking with request_id-based state under team manager
- s11: autonomous teammate behavior (initial integration)
  - teammate worker supports `idle` state and inbox polling
  - task auto-claim from `.anuris_tasks/` when unblocked tasks exist
  - identity re-injection for very short/compacted worker contexts

## 7) Key Files for Ongoing Work

Core orchestration:

- `V1/anuris/state_machine.py`
- `V1/anuris/model.py`
- `V1/anuris/streaming.py`

Agent internals:

- `V1/anuris/agent/loop.py`
- `V1/anuris/agent/executor.py`
- `V1/anuris/agent/schemas.py`
- `V1/anuris/agent/skills.py`
- `V1/anuris/agent/compact.py`
- `V1/anuris/agent/background.py`
- `V1/anuris/agent/team.py`
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
- `V1/tests/test_agent_compact.py`
- `V1/tests/test_agent_loop.py` (background notifications coverage)
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

- Harden team-mode safety policy (tool budgets, timeout ceilings, command policy by role).
- Add integration smoke tests for teammate lifecycle (`spawn -> message -> idle -> auto-claim -> shutdown`).
- Improve UX for team telemetry (single-line status for active teammates and pending plan/shutdown requests).

## 11) Daily Handoff Checklist

Before ending a dev session:

1. Run unit tests in `V1/`.
2. Confirm no unintended file changes via `git status`.
3. Update this file if behavior or command surface changed.
4. Record any unresolved issue with reproduction steps.
