from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SearchResult:
    kind: str
    source_id: str
    title: str
    preview: str
    path: str

    def render(self) -> str:
        return f"- [{self.kind}] {self.source_id} :: {self.title} -> {self.preview} ({self.path})"


class WorkspaceSearch:
    """Local search across session snapshots, transcripts, traces, and exports."""

    def __init__(self, workspace_root: Path, debug_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.debug_root = Path(debug_root).expanduser().resolve()

    def search_all(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        lowered = query.strip().lower()
        if not lowered:
            return []
        results: List[SearchResult] = []
        results.extend(self._search_sessions(lowered))
        results.extend(self._search_traces(lowered))
        results.extend(self._search_exports(lowered))
        return results[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        return self._search_sessions(query.strip().lower())[:limit]

    def search_traces(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        return self._search_traces(query.strip().lower())[:limit]

    def quickopen(self, query: str) -> List[SearchResult]:
        lowered = query.strip().lower()
        if not lowered:
            return []
        results = self.search_all(lowered, limit=50)
        exact = [item for item in results if lowered in {item.source_id.lower(), item.title.lower()}]
        return exact or results[:10]

    def _search_sessions(self, lowered: str) -> List[SearchResult]:
        root = self.workspace_root / ".anuris" / "sessions"
        if not root.exists():
            return []
        results: List[SearchResult] = []
        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            snapshot = session_dir / "session.json"
            if not snapshot.exists():
                continue
            try:
                payload = json.loads(snapshot.read_text(encoding="utf-8"))
            except Exception:
                continue
            title = str(payload.get("title", "") or "").strip() or session_dir.name
            transcript = (session_dir / "transcript.md").read_text(encoding="utf-8") if (session_dir / "transcript.md").exists() else ""
            message_text = json.dumps(payload.get("messages", []), ensure_ascii=False)
            haystack = f"{session_dir.name}\n{title}\n{transcript}\n{message_text}".lower()
            if lowered not in haystack:
                continue
            preview = self._snippet(transcript or message_text or title, lowered)
            results.append(SearchResult("session", session_dir.name, title, preview, str(session_dir / "transcript.md")))
        return results

    def _search_traces(self, lowered: str) -> List[SearchResult]:
        root = self.debug_root / "sessions"
        if not root.exists():
            return []
        results: List[SearchResult] = []
        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            session_path = session_dir / "session.json"
            transcript_path = session_dir / "transcript.md"
            if not session_path.exists() or not transcript_path.exists():
                continue
            try:
                payload = json.loads(session_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            title = str(payload.get("session_name", "") or payload.get("session_id", session_dir.name))
            transcript = transcript_path.read_text(encoding="utf-8")
            haystack = f"{session_dir.name}\n{title}\n{transcript}".lower()
            if lowered not in haystack:
                continue
            results.append(SearchResult("trace", session_dir.name, title, self._snippet(transcript, lowered), str(transcript_path)))
        return results

    def _search_exports(self, lowered: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        for path in sorted(self.workspace_root.glob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if lowered not in text.lower() and lowered not in path.name.lower():
                continue
            results.append(SearchResult("export", path.stem, path.name, self._snippet(text, lowered), str(path)))
        return results

    @staticmethod
    def _snippet(text: str, lowered: str, width: int = 140) -> str:
        haystack = text.replace("\n", " ")
        position = haystack.lower().find(lowered)
        if position < 0:
            snippet = haystack[:width]
            return snippet.strip()
        start = max(0, position - width // 3)
        end = min(len(haystack), position + width)
        return haystack[start:end].strip()
