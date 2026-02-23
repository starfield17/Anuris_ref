#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash grub_cleanup_cli.sh [--grub-cfg /boot/grub2/grub.cfg]
  bash grub_cleanup_cli.sh --delete-indexes 2,4
  bash grub_cleanup_cli.sh --delete-indexes 2,4 --apply
  bash grub_cleanup_cli.sh --delete-indexes 2,4 --apply --no-mkconfig

Behavior:
  - No --delete-indexes: list menuentries only.
  - With --delete-indexes and without --apply: preview only.
  - With --apply: rewrite grub.cfg and optionally run grub2-mkconfig.
EOF
}

grub_cfg="/boot/grub2/grub.cfg"
delete_indexes=""
apply_mode=0
run_mkconfig=1
mkconfig_output=""
backup_mode=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --grub-cfg)
      grub_cfg="${2:-}"
      shift 2
      ;;
    --delete-indexes)
      delete_indexes="${2:-}"
      shift 2
      ;;
    --apply)
      apply_mode=1
      shift
      ;;
    --no-mkconfig)
      run_mkconfig=0
      shift
      ;;
    --mkconfig-output)
      mkconfig_output="${2:-}"
      shift 2
      ;;
    --no-backup)
      backup_mode=0
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

if [[ ! -f "$grub_cfg" ]]; then
  echo "Error: grub cfg not found: $grub_cfg" >&2
  exit 1
fi

echo "Menuentries in $grub_cfg"
awk '/^menuentry / {i+=1; sub(/^menuentry '\''/, "", $0); sub(/'\''.*/, "", $0); printf("  %d) %s\n", i, $0)}' "$grub_cfg"

if [[ -z "$delete_indexes" ]]; then
  exit 0
fi

if [[ "$apply_mode" -ne 1 ]]; then
  echo "[dry-run] selected indexes: $delete_indexes"
  echo "[dry-run] re-run with --apply to modify file."
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Error: run as root for --apply mode." >&2
  exit 1
fi

tmp_cfg="$(mktemp)"
python3 - "$grub_cfg" "$tmp_cfg" "$delete_indexes" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
selected = {int(x.strip()) for x in sys.argv[3].split(",") if x.strip()}

lines = src.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
output = []
idx = 0
skip = False
depth = 0

for line in lines:
    if not skip and line.lstrip().startswith("menuentry "):
        idx += 1
        if idx in selected:
            skip = True
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                skip = False
            continue

    if skip:
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            skip = False
        continue

    output.append(line)

dst.write_text("".join(output), encoding="utf-8")
PY

if [[ "$backup_mode" -eq 1 ]]; then
  backup="${grub_cfg}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$grub_cfg" "$backup"
  echo "backup: $backup"
fi

cp "$tmp_cfg" "$grub_cfg"
rm -f "$tmp_cfg"
echo "updated: $grub_cfg"

if [[ "$run_mkconfig" -eq 1 ]]; then
  if [[ -z "$mkconfig_output" ]]; then
    mkconfig_output="$grub_cfg"
  fi
  echo "running: grub2-mkconfig -o $mkconfig_output"
  grub2-mkconfig -o "$mkconfig_output"
fi
