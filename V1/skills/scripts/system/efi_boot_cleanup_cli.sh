#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash efi_boot_cleanup_cli.sh
  bash efi_boot_cleanup_cli.sh --delete 0001,0003
  bash efi_boot_cleanup_cli.sh --delete 0001,0003 --apply
  bash efi_boot_cleanup_cli.sh --delete 0001 --apply --allow-current

Default behavior is safe preview mode (no deletion).
EOF
}

delete_list=""
apply_mode=0
allow_current=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete)
      delete_list="${2:-}"
      shift 2
      ;;
    --apply)
      apply_mode=1
      shift
      ;;
    --allow-current)
      allow_current=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v efibootmgr >/dev/null 2>&1; then
  echo "Error: efibootmgr not found" >&2
  exit 1
fi

mapfile -t boot_lines < <(efibootmgr | awk '/^Boot[0-9A-Fa-f]{4}\*/ {print}')
current_boot="$(efibootmgr | awk '/BootCurrent/ {print $2}')"

if [[ ${#boot_lines[@]} -eq 0 ]]; then
  echo "No EFI boot entries found."
  exit 0
fi

echo "Current EFI boot entries:"
for line in "${boot_lines[@]}"; do
  num="$(sed -E 's/^Boot([0-9A-Fa-f]{4})\*.*/\1/' <<<"$line")"
  desc="$(sed -E 's/^Boot[0-9A-Fa-f]{4}\*//g' <<<"$line" | sed 's/^[[:space:]]*//')"
  suffix=""
  if [[ "$num" == "$current_boot" ]]; then
    suffix=" (current)"
  fi
  echo "  Boot${num}: ${desc}${suffix}"
done

if [[ -z "$delete_list" ]]; then
  exit 0
fi

IFS=',' read -r -a delete_nums <<<"$delete_list"
for raw in "${delete_nums[@]}"; do
  num="$(echo "$raw" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
  if [[ ! "$num" =~ ^[0-9A-F]{4}$ ]]; then
    echo "Skip invalid boot number: $raw" >&2
    continue
  fi

  if [[ "$num" == "$current_boot" && "$allow_current" -ne 1 ]]; then
    echo "Skip current boot entry Boot${num}. Use --allow-current to override."
    continue
  fi

  if [[ "$apply_mode" -eq 1 ]]; then
    if [[ "$EUID" -ne 0 ]]; then
      echo "Error: run as root for --apply mode." >&2
      exit 1
    fi
    echo "Deleting Boot${num}..."
    efibootmgr -b "$num" -B
  else
    echo "[dry-run] would delete Boot${num}"
  fi
done
