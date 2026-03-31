# Anuris_ref

Anuris is a terminal AI coding assistant with a Python runtime that now follows
a Claude Code inspired architecture: a unified query engine, registry-backed
tools, registry-backed slash commands, session transcripts, and explicit
permission contexts.

## Features

- **Unified Query Engine**
  One model turn loop handles both plain chat and tool-using agent behavior.

- **Tool Registry**
  Built-in model-facing tools include shell, file read/write/edit, glob, grep,
  todo/task boards, skill loading, and bounded subagents.

- **Command Palette**
  Slash commands are now registry-backed instead of hardcoded branching logic.

- **Session Persistence**
  Sessions can be saved, loaded, compacted, and replayed from written
  transcripts under `.anuris/sessions/`.

- **Attachment Support**
  Attach images, documents, or text files to enrich requests.

- **Provider-aware Config**
  Configure API key, model, base URL, proxy, temperature, and reasoning mode.

## Installation

Clone the repository:

```bash
git clone https://github.com/starfield17/Anuris_ref.git
cd Anuris_ref/V1
````

Install requirements:

```bash
bash install_requirement.sh
```

(Optional) Add to system path:

```bash
bash add_to_sys.sh
```

## Usage

Run the CLI:

```bash
python Anuris_rebuild.py --api-key <YOUR_API_KEY> --model <MODEL_NAME>
```

Tool mode is enabled by default. Use `/agent off` to temporarily disable
model-facing tools and keep the session in plain completion mode.

### Headless Debug Server

Start a local HTTP debug server for LLM agents and automated debugging:

```bash
python Anuris_rebuild.py --debug-server --api-key <YOUR_API_KEY> --model <MODEL_NAME> --base-url <BASE_URL>
```

Default endpoint: `http://127.0.0.1:8765`

Key routes:

* `POST /sessions` – Create a headless session
* `POST /sessions/{id}/message` – Send a normal message
* `POST /sessions/{id}/task` – Send a task-style agent request
* `GET /sessions/{id}` – Inspect session state
* `GET /sessions/{id}/events` – Read structured debug events
* `GET /sessions/{id}/transcript` – Read replayable Markdown transcript

Debug artifacts are stored under `V1/.anuris_debug/sessions/`.

### Commands

* `/help`
* `/clear`
* `/save [filename]`
* `/load [filename]`
* `/attach <glob...>`
* `/detach [index]`
* `/files`
* `/agent [on|off|status]`
* `/compact [focus]`
* `/todos`
* `/tasks`
* `/skills`
* `/status`
* `/model [name]`
* `/config`
* `/agents`
* `/permissions [mode]`
* `/session [show|list]`
* `/resume [session_id]`
* `/rewind [turns]`
* `/mcp <servers|list|add-resource|read>`
* `/plugin [list|reload]`
* `/reload-plugins`
* `/worktree <list|enter|exit>`
* `/branch`
* `/env`
* `/output-style [plain|rich]`
* `/theme [name]`
* `/vim [on|off|status]`

### Keyboard Shortcuts

* **Enter**: Send message
* **Ctrl+D**: Send message
* **Ctrl+V**: Paste text
* **Ctrl+Z / Ctrl+Y**: Undo/Redo
* **Up/Down**: Navigate history

## Configuration

Configuration is stored in `~/.anuris_config.toml`.
Run with `--save-config` to persist current options.

## Tests

Run the current unit suite with:

```bash
cd V1
python -m unittest discover -s tests -v
```
