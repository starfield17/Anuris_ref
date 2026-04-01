from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..engine.session_store import SessionStore


@dataclass
class SearchResult:
    kind: str
    source_id: str
    title: str
    preview: str
    path: str
    score: int = 0
    role: str = ""
    message_kind: str = ""
    message_index: int = 0
    tool_name: str = ""

    def render(self) -> str:
        suffix = ""
        if self.message_index:
            suffix = f" [msg {self.message_index}]"
        elif self.role or self.message_kind:
            suffix = f" [{self.role}:{self.message_kind}]".rstrip(":")
        return f"- [{self.kind}] {self.source_id}{suffix} :: {self.title} -> {self.preview} ({self.path})"


class WorkspaceSearch:
    """Local search across sessions, messages, traces, and exported transcripts."""

    def __init__(self, workspace_root: Path, debug_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.debug_root = Path(debug_root).expanduser().resolve()

    def search_all(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        lowered = query.strip().lower()
        if not lowered:
            return []
        results: List[SearchResult] = []
        results.extend(self._search_session_documents(lowered, kinds={"session", "message", "compact"}))
        results.extend(self._search_traces(lowered))
        results.extend(self._search_exports(lowered))
        return self._rank_results(results, lowered)[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        lowered = query.strip().lower()
        return self._rank_results(self._search_session_documents(lowered, kinds={"session"}), lowered)[:limit]

    def search_messages(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        lowered = query.strip().lower()
        return self._rank_results(self._search_session_documents(lowered, kinds={"message", "compact"}), lowered)[:limit]

    def search_traces(self, query: str, *, limit: int = 20) -> List[SearchResult]:
        lowered = query.strip().lower()
        return self._rank_results(self._search_traces(lowered), lowered)[:limit]

    def quickopen(self, query: str) -> List[SearchResult]:
        lowered = query.strip().lower()
        if not lowered:
            return []
        results = self.search_all(lowered, limit=50)
        exact = [
            item
            for item in results
            if lowered in {item.source_id.lower(), item.title.lower()}
            or (item.message_index and lowered == f"{item.source_id.lower()}:{item.message_index}")
        ]
        return exact or results[:10]

    def _search_session_documents(self, lowered: str, *, kinds: set[str]) -> List[SearchResult]:
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
            for document in SessionStore.search_documents_from_payload(payload, session_dir):
                kind = str(document.get("kind", "message"))
                if kind not in kinds:
                    continue
                text = str(document.get("text", "") or "")
                title = str(document.get("title", "") or document.get("source_id", session_dir.name))
                haystack = f"{document.get('source_id', session_dir.name)}\n{title}\n{text}".lower()
                if lowered not in haystack:
                    continue
                results.append(
                    SearchResult(
                        kind="session" if kind == "session" else "message",
                        source_id=str(document.get("source_id", session_dir.name)),
                        title=title,
                        preview=self._snippet(text or title, lowered),
                        path=str(document.get("path", session_dir / "transcript.md")),
                        score=int(document.get("rank", 0)),
                        role=str(document.get("role", "")),
                        message_kind=str(document.get("message_kind", kind)),
                        message_index=int(document.get("message_index", 0) or 0),
                        tool_name=str(document.get("tool_name", "")),
                    )
                )
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
            results.append(
                SearchResult(
                    "trace",
                    session_dir.name,
                    title,
                    self._snippet(transcript, lowered),
                    str(transcript_path),
                    score=75,
                )
            )
        return results

    def _search_exports(self, lowered: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        for path in sorted(self.workspace_root.glob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            haystack = f"{path.name}\n{text}".lower()
            if lowered not in haystack:
                continue
            results.append(
                SearchResult(
                    "export",
                    path.stem,
                    path.name,
                    self._snippet(text, lowered),
                    str(path),
                    score=55,
                )
            )
        return results

    def _rank_results(self, results: List[SearchResult], lowered: str) -> List[SearchResult]:
        ranked: List[SearchResult] = []
        for item in results:
            score = item.score
            if lowered == item.source_id.lower():
                score += 120
            if lowered == item.title.lower():
                score += 100
            if lowered in item.title.lower():
                score += 40
            if lowered in item.preview.lower():
                score += 10
            if item.kind == "session":
                score += 25
            if item.kind == "message" and item.role == "assistant":
                score += 10
            if item.tool_name:
                score += 5
            item.score = score
            ranked.append(item)
        return sorted(
            ranked,
            key=lambda item: (
                -item.score,
                item.kind != "session",
                item.source_id,
                item.message_index or 0,
            ),
        )

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
