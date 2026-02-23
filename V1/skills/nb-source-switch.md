---
description: Switch Python package mirrors (pip/conda) across Linux, macOS, and Windows using scripts migrated from note_and_bash. Use when user asks to set Tsinghua/USTC/Aliyun/Tencent/Douban sources, restore defaults, or inspect current mirror config in non-interactive mode.
tags: pip,conda,mirror,source-switch
---
Use non-interactive wrappers first:
- `bash skills/scripts/source/switch_source_cli.sh show`
- `bash skills/scripts/source/switch_source_cli.sh pip tsinghua`
- `bash skills/scripts/source/switch_source_cli.sh conda ustc`
- `cmd /c skills\\scripts\\source\\switch_source_cli.bat show`

Detailed command cookbook:
- `skills/references/source-switch-commands.md`

Legacy menu scripts (avoid unless user explicitly wants interactive mode):
- `bash skills/scripts/source/switch_source.sh`
- `cmd /c skills\\scripts\\source\\switch_source.bat`
