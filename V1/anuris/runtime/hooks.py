from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class HookExecutionResult:
    event: str
    command: str
    outcome: str
    returncode: int
    stdout: str
    stderr: str
    blocking: bool = False

    def to_legacy_dict(self, payload: Dict[str, Any]) -> Dict[str, str]:
        data = asdict(self)
        data["payload"] = json.dumps(payload, ensure_ascii=False)[:1000]
        data["returncode"] = str(self.returncode)
        return {key: str(value) for key, value in data.items()}


class StructuredHookManager:
    """Structured hook execution over the existing JSON registry file."""

    def __init__(self, workspace_root: Path, hooks_path: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.hooks_path = Path(hooks_path).resolve()

    def load_entries(self) -> List[Dict[str, Any]]:
        if not self.hooks_path.exists():
            return []
        try:
            payload = json.loads(self.hooks_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [dict(item) for item in payload.get("hooks", []) if isinstance(item, dict)]

    def execute(self, event: str, payload: Dict[str, Any]) -> List[HookExecutionResult]:
        results: List[HookExecutionResult] = []
        for entry in self.load_entries():
            if str(entry.get("event") or "").strip() != event:
                continue
            results.append(self._run_entry(event, entry))
        return results

    def _run_entry(self, event: str, entry: Dict[str, Any]) -> HookExecutionResult:
        command = str(entry.get("command") or "").strip()
        blocking = bool(entry.get("blocking", False))
        completed = subprocess.run(
            command,
            cwd=self.workspace_root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        outcome = "success" if completed.returncode == 0 else ("blocking_error" if blocking else "non_blocking_error")
        return HookExecutionResult(
            event=event,
            command=command,
            outcome=outcome,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
            blocking=blocking,
        )
