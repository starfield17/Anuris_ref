#!/usr/bin/env python3
"""Preview/apply text replacements without interactive prompts."""

from __future__ import annotations

import argparse
import difflib
import re
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replace text in files with optional regex.")
    parser.add_argument("--file", required=True, help="Target file path")
    parser.add_argument("--search", required=True, help="Search text or regex pattern")
    parser.add_argument("--replace", required=True, help="Replacement text")
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Maximum replacements (0 means replace all)",
    )
    parser.add_argument("--regex", action="store_true", help="Treat --search as regular expression")
    parser.add_argument("--apply", action="store_true", help="Write changes to disk")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Disable timestamp backup before writing",
    )
    parser.add_argument(
        "--diff-lines",
        type=int,
        default=200,
        help="Max unified diff lines to print in preview",
    )
    return parser


def do_replace(content: str, search: str, replace: str, count: int, regex: bool) -> tuple[str, int]:
    if regex:
        limit = 0 if count <= 0 else count
        return re.subn(search, replace, content, count=limit)
    replacements = content.count(search)
    if count > 0:
        replacements = min(replacements, count)
    return content.replace(search, replace, count if count > 0 else -1), replacements


def main() -> int:
    args = build_parser().parse_args()
    target = Path(args.file).expanduser()
    if not target.is_file():
        raise SystemExit(f"file does not exist: {target}")

    before = target.read_text(encoding="utf-8")
    after, replaced = do_replace(before, args.search, args.replace, args.count, args.regex)
    print(f"replacements: {replaced}")

    if before == after:
        print("no content change")
        return 0

    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{target} (before)",
            tofile=f"{target} (after)",
            lineterm="",
        )
    )
    if diff:
        shown = diff[: args.diff_lines]
        print("\n".join(shown))
        if len(diff) > len(shown):
            print(f"... ({len(diff) - len(shown)} more diff lines)")

    if not args.apply:
        print("preview only; re-run with --apply to persist")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = target.with_name(f"{target.name}.bak.{stamp}")
        backup_path.write_text(before, encoding="utf-8")
        print(f"backup: {backup_path}")

    target.write_text(after, encoding="utf-8")
    print(f"updated: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
