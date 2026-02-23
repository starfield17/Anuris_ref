---
description: Configure AI coding tools (Claude Code, Codex, Crush, OpenCode) using non-interactive scripts migrated from note_and_bash. Use when requests involve API keys, base URLs, provider/model CRUD, or ~/.claude and ~/.codex setup without loading large script sources.
tags: ai-config,claude,codex,crush,opencode
---
Run these scripts directly; do not read their full source unless patching is required.

Primary non-interactive entrypoints:
- `python skills/scripts/ai/claude_code_config_cli.py --help`
- `python skills/scripts/ai/cc_config_tool.py --help`
- `python skills/scripts/ai/codex_config_tool.py --help`
- `python skills/scripts/ai/crush-config.py --help`
- `python skills/scripts/ai/opencode-config.py --help`

Detailed command cookbook:
- `skills/references/ai-config-commands.md`

Legacy/interactive script (keep only for compatibility):
- `bash skills/scripts/ai/claude_code_config.sh`
- `python skills/scripts/ai/ccr_config_tool_TUI.py`
