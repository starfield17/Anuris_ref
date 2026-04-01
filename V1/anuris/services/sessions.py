from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class SessionCatalog:
    """List and resume stored sessions from `.anuris/sessions/`."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def set_workspace_root(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    @property
    def sessions_root(self) -> Path:
        return self.workspace_root / ".anuris" / "sessions"

    def list_sessions(self) -> List[Dict[str, str]]:
        root = self.sessions_root
        if not root.exists():
            return []
        entries: List[Dict[str, str]] = []
        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            snapshot = session_dir / "session.json"
            transcript = session_dir / "transcript.md"
            if not snapshot.exists():
                continue
            try:
                payload = json.loads(snapshot.read_text(encoding="utf-8"))
                message_count = len(payload.get("messages", []))
                title = str(payload.get("title", "") or "").strip()
            except Exception:
                message_count = 0
                title = ""
            updated_at = datetime.fromtimestamp(snapshot.stat().st_mtime).isoformat(timespec="seconds")
            entries.append(
                {
                    "session_id": session_dir.name,
                    "title": title,
                    "message_count": str(message_count),
                    "updated_at": updated_at,
                    "transcript_path": str(transcript),
                }
            )
        entries.sort(key=lambda item: item["updated_at"], reverse=True)
        return entries

    def latest_session_id(self) -> str:
        sessions = self.list_sessions()
        if not sessions:
            raise ValueError("No saved sessions available.")
        return sessions[0]["session_id"]

    def snapshot_path(self, session_id: str) -> Path:
        path = self.sessions_root / session_id / "session.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return path

    def preview(self, session_id: str, limit: int = 6) -> str:
        path = self.snapshot_path(session_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        messages = payload.get("messages", [])
        title = str(payload.get("title", "") or "").strip() or session_id
        lines = [
            f"session_id: {session_id}",
            f"title: {title}",
            f"messages: {len(messages)}",
            "recent:",
        ]
        for message in messages[-limit:]:
            role = str(message.get("role", "unknown"))
            kind = str(message.get("kind", "message"))
            content = self._preview_text(message.get("content", ""))
            label = f"{role}:{kind}" if kind != "message" else role
            lines.append(f"- {label}: {content or '(empty)'}")
        return "\n".join(lines)

    def choices(self) -> List[str]:
        return [item["session_id"] for item in self.list_sessions()]

    def render(self) -> str:
        sessions = self.list_sessions()
        if not sessions:
            return "No saved sessions."
        return "\n".join(
            f"- {item['session_id']} [{item['title'] or 'untitled'}] ({item['message_count']} messages, updated {item['updated_at']})"
            for item in sessions
        )

    @staticmethod
    def _preview_text(content: object, max_chars: int = 120) -> str:
        if isinstance(content, str):
            text = content.replace("\n", " ").strip()
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            text = " ".join(part.strip() for part in parts if str(part).strip())
        else:
            text = str(content or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
