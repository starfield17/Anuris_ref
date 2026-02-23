#!/usr/bin/env python3
"""Non-interactive Claude Code + Z.AI config writer."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
DEFAULT_TIMEOUT_MS = "3000000"
DEFAULT_MODEL = "default"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write ~/.claude/settings.json and ~/.claude.json without interactive prompts."
    )
    parser.add_argument("--api-key", required=True, help="Z.AI API key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Anthropic compatible base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude Code model alias or full model")
    parser.add_argument("--timeout-ms", default=DEFAULT_TIMEOUT_MS, help="API timeout in milliseconds")
    parser.add_argument("--glm-map", default="", help="Optional GLM model for opus/sonnet/haiku aliases")
    parser.add_argument(
        "--settings-file",
        default=str(Path.home() / ".claude" / "settings.json"),
        help="Path to Claude Code settings.json",
    )
    parser.add_argument(
        "--claude-json-file",
        default=str(Path.home() / ".claude.json"),
        help="Path to ~/.claude.json",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not create timestamp backups")
    parser.add_argument("--dry-run", action="store_true", help="Show merged content without writing files")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    settings_path = Path(args.settings_file).expanduser()
    claude_json_path = Path(args.claude_json_file).expanduser()

    settings = load_json(settings_path)
    env = settings.get("env")
    if not isinstance(env, dict):
        env = {}

    env["ANTHROPIC_AUTH_TOKEN"] = args.api_key
    env["ANTHROPIC_BASE_URL"] = args.base_url
    env["API_TIMEOUT_MS"] = str(args.timeout_ms)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = 1

    model = args.model.strip()
    if model:
        settings["model"] = model
        env["ANTHROPIC_MODEL"] = model

    glm_map = args.glm_map.strip()
    if glm_map:
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = glm_map
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = glm_map
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = glm_map

    settings["env"] = env

    claude_json = load_json(claude_json_path)
    claude_json["hasCompletedOnboarding"] = True

    if args.dry_run:
        print(f"[dry-run] would write: {settings_path}")
        print(json.dumps(settings, ensure_ascii=False, indent=2))
        print(f"[dry-run] would write: {claude_json_path}")
        print(json.dumps(claude_json, ensure_ascii=False, indent=2))
        return 0

    if not args.no_backup:
        backup1 = backup(settings_path)
        backup2 = backup(claude_json_path)
        if backup1:
            print(f"backup created: {backup1}")
        if backup2:
            print(f"backup created: {backup2}")

    save_json(settings_path, settings)
    save_json(claude_json_path, claude_json)
    print(f"updated: {settings_path}")
    print(f"updated: {claude_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
