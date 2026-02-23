---
description: Execute note_and_bash system administration scripts in safer non-interactive mode, including proxy env generation, file text replacement, EFI boot cleanup, GRUB entry cleanup, and PlatformIO udev setup. Use when user asks for these maintenance operations from CLI automation.
tags: system-admin,proxy,grub,efi,replace
---
Default to non-interactive scripts and dry-run modes:
- `bash skills/scripts/system/change_proxy_cli.sh --proxy 127.0.0.1:7890`
- `python skills/scripts/system/replace_text_cli.py --help`
- `bash skills/scripts/system/efi_boot_cleanup_cli.sh`
- `bash skills/scripts/system/grub_cleanup_cli.sh`
- `bash skills/scripts/system/console_platformio.sh`

Detailed command cookbook:
- `skills/references/system-admin-commands.md`

Legacy interactive scripts (compatibility only):
- `bash skills/scripts/system/change_proxy.sh`
- `bash skills/scripts/system/replace.sh`
- `bash skills/scripts/system/remove_useless_startup.sh`
- `bash skills/scripts/system/fix-broken-grub.sh`
