#!/usr/bin/env python3
"""Non-interactive wrapper around ob_cli_v3.py."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OB calculations with a compact CLI (delegates to ob_cli_v3.py)."
    )
    parser.add_argument(
        "--mix",
        help='Mixture spec, e.g. "KClO4:65,Al:35" or "KClO4:65 Al:35"',
    )
    parser.add_argument(
        "--optimize",
        help='Optimization reactants, e.g. "Fe2O3 Al"',
    )
    parser.add_argument("--target", type=float, default=0.0, help="Target OB%% for optimization")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    backend = script_dir / "ob_cli_v3.py"

    if not args.mix and not args.optimize:
        raise SystemExit("one of --mix or --optimize is required")

    cmd = [sys.executable, str(backend)]
    if args.mix:
        mix = " ".join(args.mix.replace(",", " ").split())
        cmd.extend(["--input", mix])
    if args.optimize:
        cmd.extend(["--optimize", args.optimize, "--target", str(args.target)])

    completed = subprocess.run(cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
