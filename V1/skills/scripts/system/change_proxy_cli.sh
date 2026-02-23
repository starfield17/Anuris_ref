#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash change_proxy_cli.sh --proxy 127.0.0.1:7890 [--shell bash]
  bash change_proxy_cli.sh --clear [--shell zsh]

Output:
  Prints shell commands to stdout. Evaluate them manually in your shell.
EOF
}

normalize_shell() {
  local sh="${1##*/}"
  case "$sh" in
    fish|zsh|bash|sh|ksh|dash|csh|tcsh) echo "$sh" ;;
    *) echo "bash" ;;
  esac
}

detect_shell() {
  local parent=""
  parent="$(ps -p "$PPID" -o comm= 2>/dev/null || true)"
  parent="${parent##*/}"
  if [[ -n "$parent" ]]; then
    normalize_shell "$parent"
  elif [[ -n "${SHELL:-}" ]]; then
    normalize_shell "$SHELL"
  else
    echo "bash"
  fi
}

print_set_cmds() {
  local sh="$1"
  local proxy="$2"
  local no_proxy_val="localhost,127.0.0.1,::1"
  case "$sh" in
    fish)
      cat <<EOF
set -gx http_proxy "http://$proxy"
set -gx https_proxy "https://$proxy"
set -gx ftp_proxy "ftp://$proxy"
set -gx socks_proxy "socks://$proxy"
set -gx no_proxy "$no_proxy_val"
EOF
      ;;
    csh|tcsh)
      cat <<EOF
setenv http_proxy "http://$proxy"
setenv https_proxy "https://$proxy"
setenv ftp_proxy "ftp://$proxy"
setenv socks_proxy "socks://$proxy"
setenv no_proxy "$no_proxy_val"
EOF
      ;;
    *)
      cat <<EOF
export http_proxy="http://$proxy"
export https_proxy="https://$proxy"
export ftp_proxy="ftp://$proxy"
export socks_proxy="socks://$proxy"
export no_proxy="$no_proxy_val"
EOF
      ;;
  esac
}

print_clear_cmds() {
  local sh="$1"
  case "$sh" in
    fish)
      cat <<'EOF'
set -e http_proxy
set -e https_proxy
set -e ftp_proxy
set -e socks_proxy
set -e no_proxy
EOF
      ;;
    csh|tcsh)
      cat <<'EOF'
unsetenv http_proxy
unsetenv https_proxy
unsetenv ftp_proxy
unsetenv socks_proxy
unsetenv no_proxy
EOF
      ;;
    *)
      cat <<'EOF'
unset http_proxy
unset https_proxy
unset ftp_proxy
unset socks_proxy
unset no_proxy
EOF
      ;;
  esac
}

target_shell=""
proxy=""
clear_mode=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --shell|-s)
      target_shell="$(normalize_shell "${2:-}")"
      shift 2
      ;;
    --proxy)
      proxy="${2:-}"
      shift 2
      ;;
    --clear)
      clear_mode=1
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

if [[ -z "$target_shell" ]]; then
  target_shell="$(detect_shell)"
fi

if [[ "$clear_mode" -eq 1 ]]; then
  print_clear_cmds "$target_shell"
  exit 0
fi

if [[ -z "$proxy" ]]; then
  echo "Error: --proxy is required unless --clear is provided." >&2
  exit 2
fi

print_set_cmds "$target_shell" "$proxy"
