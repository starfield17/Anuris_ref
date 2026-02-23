# Anuris V1 Architecture

This document describes the current post-refactor structure of `Anuris_rebuild.py` and the `anuris` package.

## 1. Design Goals

- Keep `Anuris_rebuild.py` as a thin, stable entrypoint.
- Separate concerns into small modules (config, UI, model, state, commands, streaming).
- Preserve user-visible behavior while reducing file size and coupling.
- Make core paths easy to test with unit tests.

## 2. Runtime Flow

The runtime call chain is:

```text
Anuris_rebuild.py
  -> anuris.cli.main()
      -> bootstrap: parse args, merge config, prompt missing required fields
      -> ChatStateMachine.run()
          -> WAITING_FOR_USER
          -> PROCESSING (slash command or chat)
          -> RESPONDING (OpenAI stream + renderer)
```

State transitions are managed by a small finite-state machine (`ChatStateMachine`).

## 3. Module Map

```text
V1/
  Anuris_rebuild.py          # thin entrypoint
  ARCHITECTURE.md            # this file
  anuris/
    __init__.py
    cli.py                   # process orchestration only
    bootstrap.py             # parser + config/bootstrap pipeline
    config.py                # Config dataclass + ConfigManager
    prompts.py               # PromptManager + default prompt loading
    history.py               # ChatHistory persistence
    attachments.py           # attachment model + conversion for API
    ui.py                    # terminal UI and keybindings
    model.py                 # OpenAI API client wrapper
    commands.py              # slash command dispatcher + handlers
    streaming.py             # stream parser/renderer (<think> + reasoning)
    state_machine.py         # chat FSM and orchestration
  tests/
    test_bootstrap.py
    test_commands.py
    test_streaming.py
```

## 4. Responsibilities by Layer

### Entrypoint Layer

- `Anuris_rebuild.py`
  - Imports `main` from `anuris.cli`.
  - Keeps invocation compatibility (`python Anuris_rebuild.py ...`).

### Bootstrap Layer

- `anuris/bootstrap.py`
  - Builds argument parser.
  - Resolves `--system-prompt-file` to prompt text.
  - Merges CLI args with persisted config.
  - Handles `--save-config`.
  - Interactively collects missing required config (`base_url`, `model`, `api_key`).

- `anuris/cli.py`
  - Wires bootstrap outputs into `ChatStateMachine`.
  - Avoids business logic.

### Domain/Service Layer

- `anuris/config.py`
  - `Config` dataclass and TOML-backed `ConfigManager`.

- `anuris/prompts.py`
  - Prompt file loading and source resolution.

- `anuris/history.py`
  - Message history + reasoning/attachment metadata.
  - Save/load JSON snapshots.

- `anuris/attachments.py`
  - File validation and conversion to API content blocks.

- `anuris/model.py`
  - OpenAI client setup (including SOCKS proxy path).
  - Streaming request execution and network error mapping.

### Interaction Layer

- `anuris/ui.py`
  - Input session, shortcuts, rich output primitives.

- `anuris/commands.py`
  - Slash command dispatch map (`/clear`, `/save`, `/load`, `/attach`, `/detach`, `/files`, `/help`).
  - Encapsulates command side effects against history/attachments/UI.

- `anuris/streaming.py`
  - Parses streaming deltas.
  - Splits reasoning vs final answer output.
  - Handles `<think>...</think>` segments.
  - Returns `StreamResult(full_response, reasoning_content, interrupted)`.

### Orchestration Layer

- `anuris/state_machine.py`
  - Owns runtime state enum and transition map.
  - Delegates commands to `CommandDispatcher`.
  - Delegates output parsing/rendering to `StreamRenderer`.
  - Persists user/assistant turns into `ChatHistory`.

## 5. Testing Strategy

Current focused tests:

- `test_bootstrap.py`
  - parser shape
  - prompt arg resolution
  - config merge precedence
  - save-config branch
  - required-field prompting path

- `test_commands.py`
  - unknown command handling
  - attach/list/detach flow
  - clear behavior
  - save/load history
  - help rendering

- `test_streaming.py`
  - reasoning + content accumulation
  - think-tag routing
  - interruption returns partial output

Run all tests:

```bash
python -m unittest discover -s tests -v
```

## 6. Extension Points

Recommended extension points for next iterations:

- Add new slash commands by extending `CommandDispatcher.handlers`.
- Add stream parsing behavior in `StreamRenderer` without touching FSM.
- Add startup policies in `bootstrap.py` without touching runtime loop.
- Add advanced context management as a new service module, then call it from `state_machine.py`.

## 7. Non-goals (for now)

- No behavioral redesign of message protocol.
- No async event loop migration.
- No plugin loading framework yet.

The current architecture intentionally prioritizes stability and readability over feature expansion.
