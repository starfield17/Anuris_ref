#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/switch_source_en.py"

usage() {
  cat <<'EOF'
Usage:
  bash switch_source_cli.sh show
  bash switch_source_cli.sh pip <tsinghua|ustc|aliyun|tencent|douban|default>
  bash switch_source_cli.sh conda <tsinghua|ustc|default>

This wrapper enforces non-interactive usage of switch_source_en.py.
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

action="$1"
shift

case "$action" in
  show)
    exec python3 "$PY_SCRIPT" --show
    ;;
  pip)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    exec python3 "$PY_SCRIPT" --pip "$1"
    ;;
  conda)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    exec python3 "$PY_SCRIPT" --conda "$1"
    ;;
  *)
    usage
    exit 2
    ;;
esac
