from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from anuris.config import ConfigManager
from anuris.debug_server import DebugSessionManager, DebugTraceRunner


def load_spec(source: str) -> Dict[str, Any]:
    if source == "-":
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Trace spec must decode to a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an injectable Anuris debug trace and export a Markdown transcript.",
    )
    parser.add_argument("--spec", required=True, help="Path to JSON spec file, or '-' to read JSON from stdin.")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root used for the debug session. Defaults to the current directory.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / ".anuris_debug_runs"),
        help="Directory for events/session files and default exported Markdown.",
    )
    parser.add_argument("--session-id", default="", help="Optional session id override.")
    parser.add_argument("--session-name", default="", help="Optional session name override.")
    parser.add_argument("--model", default="", help="Optional model override.")
    parser.add_argument("--base-url", default="", help="Optional base_url override.")
    parser.add_argument(
        "--agent-mode",
        choices=["on", "off"],
        default="",
        help="Optional agent mode override for the created session.",
    )
    return parser


def apply_cli_overrides(spec: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    payload = dict(spec)
    session = dict(payload.get("session") or {})
    if args.session_id:
        session["session_id"] = args.session_id
    if args.session_name:
        session["session_name"] = args.session_name
    if args.model:
        session["model"] = args.model
    if args.base_url:
        session["base_url"] = args.base_url
    if args.agent_mode:
        session["agent_mode"] = args.agent_mode == "on"
    payload["session"] = session
    return payload


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = ConfigManager().load_config()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manager = DebugSessionManager(
        base_config=config,
        workspace_root=workspace_root,
        debug_dir=output_dir,
    )
    runner = DebugTraceRunner(manager)
    spec = apply_cli_overrides(load_spec(args.spec), args)
    result = runner.run_trace(spec)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
