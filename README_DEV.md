# README_DEV

Developer handoff notes for the refactored `Anuris_ref`.

## 1) Current Architecture

- Active implementation lives in `V1/`.
- Entry point remains `V1/Anuris_rebuild.py`.
- Core runtime lives in:
  - `V1/anuris/session.py`
  - `V1/anuris/engine/`
  - `V1/anuris/tools/`
  - `V1/anuris/commands.py`
- Bootstrap/config/provider edges remain in:
  - `V1/anuris/bootstrap.py`
  - `V1/anuris/config.py`
  - `V1/anuris/model.py`

The runtime is no longer built around a separate legacy `AgentLoopRunner` path.
`ChatSession` now routes user input into either:

- `CommandDispatcher` for slash commands
- `QueryEngine` for model turns and tool loops

## 2) Quick Start

```bash
cd V1
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python Anuris_rebuild.py --base-url <URL> --model <MODEL> --api-key <KEY>
```

Optional config persistence:

```bash
python Anuris_rebuild.py --base-url <URL> --model <MODEL> --api-key <KEY> --save-config
```

Additional CLI overrides now exist for persisted UI settings:

```bash
python Anuris_rebuild.py --theme dark --output-style plain --vim-mode on
```

Headless debug server:

```bash
python Anuris_rebuild.py --debug-server --debug-host 127.0.0.1 --debug-port 8765
```

## 3) Important Runtime Concepts

### SessionStore

- Stores normalized messages independent of provider SDK objects
- Persists transcripts under `.anuris/sessions/<session_id>/transcript.md`
- Supports JSON save/load
- Writes explicit compact-boundary system messages during compaction

### QueryEngine

- Owns the model turn loop
- Exposes tools through `ToolRegistry`
- Converts tool failures into `tool_result` messages instead of crashing the turn
- Can spawn readonly subagents through the `task` tool

### ToolRegistry

Current built-in tools:

- `bash`
- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `grep`
- `todo_write`
- `task_create`
- `task_get`
- `task_update`
- `task_list`
- `load_skill`
- `tool_search`
- `list_mcp_resources`
- `read_mcp_resource`
- `enter_worktree`
- `exit_worktree`
- `task`

### CommandDispatcher

Current slash commands:

- `/help`
- `/clear`
- `/save [filename]`
- `/load [filename]`
- `/attach <glob...>`
- `/detach [index]`
- `/files`
- `/add-dir [list|clear|remove <path>|<path>...]`
- `/agent [on|off|status]`
- `/compact [focus]`
- `/todos`
- `/tasks`
- `/skills`
- `/status`
- `/model [name|pick]`
- `/config`
- `/agents`
- `/permissions [mode]`
- `/session [show|list|preview|pick]`
- `/resume [session_id]`
- `/rewind [turns]`
- `/mcp <servers|list|add-resource|read>`
- `/plugin [list|reload]`
- `/reload-plugins`
- `/worktree <list|enter|exit>`
- `/branch`
- `/env`
- `/output-style [plain|rich|pick]`
- `/theme [name|pick|toggle|switch]`
- `/vim [on|off|status]`
- `/cost`
- `/usage`
- `/stats`
- `/doctor`
- `/diff [full|pathspec]`
- `/review [pr_number]`
- `/plan [open|show|description]`
- `/hooks [list|add|remove|run]`
- `/summary`
- `/context`
- `/memory [show|append|clear]`
- `/rename [name]`
- `/export [filename]`
- `/copy [full|code [index]|message [index]]`
- `/commit [message]`

### Interactive UI

- `V1/anuris/ui.py` now renders a Claude Code-inspired terminal shell
- Default runtime theme is `claude`
- Available themes are `claude`, `dark`, `midnight`, and `default`
- `/theme toggle` and `/theme switch` alternate between `claude` and `dark`
- Interactive rendering distinguishes welcome/status, assistant replies,
  reasoning panels, and tool/activity lines
- `/theme`, `/output-style`, and `/vim` now persist to `~/.anuris_config.toml`

New grouped command registrations live under `V1/anuris/command_specs/`:

- `analysis.py` for `/cost`, `/diff`, `/review`, `/plan`
- `diagnostics.py` for `/usage`, `/stats`, `/doctor`
- `events.py` for `/hooks`
- `inspection.py` for `/summary`, `/context`, `/memory`
- `session_ops.py` for `/rename`, `/export`, `/copy`
- `workspace.py` for `/add-dir`
- `workflow.py` for `/commit`

## 4) Test Commands

Run all unit tests:

```bash
cd V1
python -m unittest discover -s tests -v
```

Quick syntax validation for touched files:

```bash
python -m py_compile anuris/session.py anuris/commands.py anuris/state_machine.py
```

## 5) Current Limits / Follow-ups

- The permission system is intentionally lightweight.
- The old background/team governance feature set was not migrated into the new
  engine yet.
- The refactor prioritized a clean QueryEngine/tool architecture first, leaving
  richer coordination features for a later phase.
- Usage/cost reporting is local session accounting only; provider billing and
  token pricing are not yet wired.
