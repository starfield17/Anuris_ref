# Anuris V1 Architecture

This document describes the current post-refactor structure of `Anuris_rebuild.py`
and the `anuris` package.

## 1. Design Goals

- Keep `Anuris_rebuild.py` as a thin Python entrypoint.
- Replace the older split between "chat mode" and "agent mode" with one
  QueryEngine-style turn loop.
- Adopt Claude Code inspired concepts in Python:
  - normalized conversation messages
  - registry-backed tools
  - registry-backed slash commands
  - explicit permission contexts
  - session transcripts and compact boundaries
- Keep provider/config/bootstrap code reusable and testable.

## 2. Runtime Flow

The runtime call chain is:

```text
Anuris_rebuild.py
  -> anuris.cli.main()
      -> bootstrap: parse args, merge config, prompt missing required fields
      -> ChatStateMachine.run()
          -> ChatSession.handle_input()
              -> CommandDispatcher (for /commands)
              -> QueryEngine.submit() (for model turns)
                  -> ToolRegistry -> Tool -> tool_result
                  -> SessionStore transcript / compaction
```

`ChatStateMachine` is now intentionally thin. The real orchestration lives in
`ChatSession`, `QueryEngine`, `SessionStore`, and `ToolRegistry`.

## 3. Module Map

```text
V1/
  Anuris_rebuild.py
  ARCHITECTURE.md
  anuris/
    __init__.py
    cli.py
    bootstrap.py
    config.py
    prompts.py
    attachments.py
    model.py
    session.py           # ChatSession + HeadlessUI + SessionResponse
    commands.py          # registry-backed slash command layer
    state_machine.py     # thin TTY shell
    ui.py                # terminal UI / prompt-toolkit bindings
    engine/
      context.py         # PermissionContext + SessionServices + ToolUseContext
      messages.py        # normalized message + tool call types
      query_engine.py    # QueryEngine turn loop
      session_store.py   # transcript persistence + compaction
    tools/
      base.py            # BaseTool + ToolExecutionResult
      builtin.py         # bash/read/write/edit/glob/grep/task/skill tools
      registry.py        # active tool filtering + schema export
    agent/
      todo.py            # reused TodoManager
      tasks.py           # reused PersistentTaskManager
      skills.py          # reused SkillLoader
```

## 4. Responsibilities by Layer

### Bootstrap / Runtime Edge

- `anuris/bootstrap.py`
  - CLI argument parser
  - saved config merge
  - prompting for missing required settings
- `anuris/cli.py`
  - startup wiring
  - debug server switch
- `anuris/state_machine.py`
  - interactive prompt loop only

### Session / Engine Core

- `anuris/session.py`
  - owns one session
  - attachments queue
  - command dispatch
  - headless compatibility wrapper
  - engine event forwarding
- `anuris/engine/session_store.py`
  - normalized message storage
  - transcript writing under `.anuris/sessions/<id>/`
  - JSON save/load
  - compact boundary generation
- `anuris/engine/query_engine.py`
  - one query lifecycle
  - tool-call loop
  - assistant/tool message normalization
  - subagent delegation
  - auto-compaction trigger

### Tools / Commands

- `anuris/tools/registry.py`
  - active tool filtering based on `PermissionContext`
  - provider-facing schema generation
- `anuris/tools/builtin.py`
  - shell, file, search, todo, task, skill, and subagent tools
- `anuris/commands.py`
  - registry-backed slash commands
  - help, save/load, attach/detach, compact, status, model, tasks, skills

## 5. Current Command Surface

Interactive commands:

- `/help`
- `/clear`
- `/save [filename]`
- `/load [filename]`
- `/attach <glob...>`
- `/detach [index]`
- `/files`
- `/agent [on|off|status]`
- `/compact [focus]`
- `/todos`
- `/tasks`
- `/skills`
- `/status`
- `/model [name]`
- `/config`
- `/agents`

## 6. Built-in Tools

The model-facing tool registry currently exposes:

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
- `task` (spawn bounded subagent work)

Readonly subagents are restricted to read/search/task-read tools by default.

## 7. Testing Strategy

Focused tests now cover:

- bootstrap/config merge behavior
- command registry behavior
- QueryEngine tool loop behavior
- compaction behavior
- provider wrapper behavior
- headless session and debug server flows

Run the suite with:

```bash
cd V1
python -m unittest discover -s tests -v
```

## 8. Known Constraints

- The new tool loop is synchronous.
- Provider support still assumes OpenAI-compatible chat-completions APIs.
- Permission handling is intentionally lightweight compared to Claude Code's
  full interactive approval system.
- Legacy background/team governance features were not carried into the new
  runtime yet; the refactor centers on the single-session engine first.
