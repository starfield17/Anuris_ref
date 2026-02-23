---
description: Run migrated Windows administrative scripts from note_and_bash in non-interactive mode, including security policy presets and Windows-side package source switching. Use for scripted Windows environment setup or rollback without menu-driven UI.
tags: windows,security,bat,admin
---
Use non-interactive wrappers where possible:
- `cmd /c skills\\scripts\\windows\\windows_security_unlock_cli.bat status`
- `cmd /c skills\\scripts\\windows\\windows_security_unlock_cli.bat level1`
- `cmd /c skills\\scripts\\source\\switch_source_cli.bat pip tsinghua`

Detailed command cookbook:
- `skills/references/windows-admin-commands.md`

Legacy interactive script:
- `cmd /c skills\\scripts\\windows\\windows_security_unlock.bat`
