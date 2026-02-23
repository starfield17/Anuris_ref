# System Admin Commands

## Proxy env commands

```bash
bash skills/scripts/system/change_proxy_cli.sh --proxy 127.0.0.1:7890 --shell bash
bash skills/scripts/system/change_proxy_cli.sh --clear --shell zsh
```

The script prints export/unset lines; evaluate them manually in the current shell.

## Text replacement (preview first)

```bash
python skills/scripts/system/replace_text_cli.py \
  --file /etc/apt/sources.list \
  --search old.domain.example \
  --replace new.domain.example
```

Apply changes:

```bash
python skills/scripts/system/replace_text_cli.py \
  --file /etc/apt/sources.list \
  --search old.domain.example \
  --replace new.domain.example \
  --apply
```

## EFI boot entries

```bash
# list only
bash skills/scripts/system/efi_boot_cleanup_cli.sh

# preview deletion
bash skills/scripts/system/efi_boot_cleanup_cli.sh --delete 0003,0004

# apply deletion (root)
sudo bash skills/scripts/system/efi_boot_cleanup_cli.sh --delete 0003,0004 --apply
```

## GRUB entries

```bash
# list only
bash skills/scripts/system/grub_cleanup_cli.sh --grub-cfg /boot/grub2/grub.cfg

# dry-run
bash skills/scripts/system/grub_cleanup_cli.sh --delete-indexes 2,4

# apply (root)
sudo bash skills/scripts/system/grub_cleanup_cli.sh --delete-indexes 2,4 --apply
```
