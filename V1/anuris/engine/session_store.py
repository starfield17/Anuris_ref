from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .messages import ConversationMessage


class SessionStore:
    """Conversation state and transcript persistence for one session."""

    def __init__(self, system_prompt: str, workspace_root: Path, session_id: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.session_id = session_id
        self.title: Optional[str] = None
        self.session_dir = self.workspace_root / ".anuris" / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.messages: List[ConversationMessage] = [
            ConversationMessage(role="system", content=system_prompt, kind="system")
        ]
        self._persist_snapshot()

    def reset(self, system_prompt: Optional[str] = None) -> None:
        prompt = system_prompt or self.system_prompt
        self.messages = [ConversationMessage(role="system", content=prompt, kind="system")]
        self.write_transcript()
        self._persist_snapshot()

    @property
    def system_prompt(self) -> str:
        if not self.messages:
            return ""
        first = self.messages[0]
        return str(first.content if isinstance(first.content, str) else "")

    def append(self, message: ConversationMessage) -> ConversationMessage:
        self.messages.append(message)
        self._persist_snapshot()
        return message

    def add_user_message(self, content: Any, metadata: Optional[Dict[str, Any]] = None) -> ConversationMessage:
        return self.append(
            ConversationMessage(
                role="user",
                content=content,
                kind="message",
                metadata=metadata or {},
            )
        )

    def add_assistant_message(
        self,
        content: Any,
        *,
        tool_calls: Optional[List[Any]] = None,
        reasoning: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationMessage:
        return self.append(
            ConversationMessage(
                role="assistant",
                content=content,
                kind="message",
                tool_calls=list(tool_calls or []),
                reasoning=reasoning,
                metadata=metadata or {},
            )
        )

    def add_tool_result(
        self,
        tool_name: str,
        tool_call_id: str,
        content: str,
        *,
        is_error: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationMessage:
        return self.append(
            ConversationMessage(
                role="tool",
                name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
                kind="tool_result",
                metadata={"is_error": is_error, **(metadata or {})},
            )
        )

    def to_api_messages(self) -> List[Dict[str, Any]]:
        return [message.to_api_message() for message in self.messages]

    def save(self, filename: str) -> Path:
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "title": self.title,
            "messages": [message.to_dict() for message in self.messages],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, filename: str) -> Path:
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        messages = [ConversationMessage.from_dict(item) for item in payload.get("messages", [])]
        if not messages or messages[0].role != "system":
            raise ValueError("Saved session is missing the system prompt message")
        self.messages = messages
        self.title = _normalize_title(payload.get("title"))
        self.write_transcript()
        self._persist_snapshot()
        return path

    def approximate_size(self) -> int:
        return sum(len(message.preview(10000)) + len(message.reasoning) for message in self.messages)

    def compact_history(self, focus: str = "", keep_last: int = 8) -> str:
        if len(self.messages) <= keep_last + 1:
            return "Context is already compact."

        system_message = self.messages[0]
        tail = self.messages[-keep_last:]
        removable = self.messages[1:-keep_last]
        if not removable:
            return "Context is already compact."

        lines = ["Conversation compacted into a working summary."]
        if focus.strip():
            lines.append(f"Focus: {focus.strip()}")
        for message in removable:
            role = message.role.upper()
            snippet = message.preview(220) or "(empty)"
            lines.append(f"- {role}: {snippet}")
            if message.reasoning:
                lines.append(f"  reasoning: {message.reasoning[:180].replace(chr(10), ' ')}")
        summary = "\n".join(lines)
        compact_boundary = ConversationMessage(
            role="system",
            kind="compact_boundary",
            content=summary,
            metadata={"focus": focus.strip()},
        )
        self.messages = [system_message, compact_boundary, *tail]
        self.write_transcript()
        self._persist_snapshot()
        return summary

    def retarget_workspace(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.session_dir = self.workspace_root / ".anuris" / "sessions" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.write_transcript()
        self._persist_snapshot()

    def load_snapshot_path(self, snapshot_path: Path) -> None:
        payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        messages = [ConversationMessage.from_dict(item) for item in payload.get("messages", [])]
        if not messages or messages[0].role != "system":
            raise ValueError("Saved session is missing the system prompt message")
        self.messages = messages
        self.title = _normalize_title(payload.get("title"))
        self.write_transcript()
        self._persist_snapshot()

    def rewind_turns(self, turns: int = 1) -> int:
        turns = max(1, int(turns))
        if len(self.messages) <= 1:
            return 0

        removed = 0
        user_turns_removed = 0
        while len(self.messages) > 1 and user_turns_removed < turns:
            message = self.messages.pop()
            if message.role == "user":
                user_turns_removed += 1
            removed += 1
        self.write_transcript()
        self._persist_snapshot()
        return removed

    def describe(self) -> str:
        lines = [
            f"session_id: {self.session_id}",
            f"title: {self.title or '(untitled)'}",
            f"workspace_root: {self.workspace_root}",
            f"message_count: {len(self.messages)}",
            f"transcript_path: {self.session_dir / 'transcript.md'}",
        ]
        return "\n".join(lines)

    def context_report(self) -> str:
        role_counts: dict[str, int] = {}
        compact_boundaries = 0
        tool_results = 0
        reasoning_chars = 0
        for message in self.messages:
            role_counts[message.role] = role_counts.get(message.role, 0) + 1
            if message.kind == "compact_boundary":
                compact_boundaries += 1
            if message.kind == "tool_result":
                tool_results += 1
            reasoning_chars += len(message.reasoning or "")

        lines = [
            f"messages_total: {len(self.messages)}",
            f"approx_chars: {self.approximate_size()}",
            f"compact_boundaries: {compact_boundaries}",
            f"tool_results: {tool_results}",
            f"reasoning_chars: {reasoning_chars}",
        ]
        for role in sorted(role_counts):
            lines.append(f"{role}_messages: {role_counts[role]}")
        return "\n".join(lines)

    def summary_report(self, limit: int = 8) -> str:
        if len(self.messages) <= 1:
            return "No conversation history yet."

        lines = [
            f"Session summary for {self.title or self.session_id}",
            "",
            f"Total messages: {len(self.messages)}",
            f"Approx context size: {self.approximate_size()} chars",
            "",
            "Recent timeline:",
        ]
        for message in self.messages[-limit:]:
            title = f"{message.role}:{message.kind}" if message.kind != "message" else message.role
            lines.append(f"- {title}: {message.preview(180) or '(empty)'}")
            if message.kind == "compact_boundary":
                lines.append(f"  compact_focus: {message.metadata.get('focus', 'automatic') or 'automatic'}")
        return "\n".join(lines)

    def recent_message_views(self, limit: int = 10) -> List[Dict[str, Any]]:
        views: List[Dict[str, Any]] = []
        for index, message in enumerate(self.messages[-limit:], start=max(1, len(self.messages) - limit + 1)):
            label = f"{message.role}:{message.kind}" if message.kind != "message" else message.role
            views.append(
                {
                    "index": index,
                    "label": label,
                    "preview": message.preview(200) or "(empty)",
                    "role": message.role,
                    "kind": message.kind,
                }
            )
        return views

    def message_at(self, index: int) -> ConversationMessage:
        if index < 1 or index > len(self.messages):
            raise IndexError("message index out of range")
        return self.messages[index - 1]

    def recent_edit_diffs(self, limit: int = 10) -> List[Dict[str, Any]]:
        diffs: List[Dict[str, Any]] = []
        for message in reversed(self.messages):
            if message.role != "tool" or message.kind != "tool_result":
                continue
            metadata = message.metadata or {}
            if "diff" not in metadata and "path" not in metadata:
                continue
            diffs.append(
                {
                    "tool_name": message.name or "",
                    "path": metadata.get("path", ""),
                    "diff": metadata.get("diff", ""),
                    "summary": metadata.get("summary", message.preview(160)),
                    "is_error": bool(metadata.get("is_error", False)),
                }
            )
            if len(diffs) >= limit:
                break
        return diffs

    def rename(self, title: str) -> str:
        normalized = _normalize_title(title)
        if not normalized:
            raise ValueError("session title is required")
        self.title = normalized
        self.write_transcript()
        self._persist_snapshot()
        return normalized

    def suggested_title(self, max_length: int = 50) -> str:
        candidates = [
            message.preview(500)
            for message in self.messages
            if message.role == "user" and message.kind == "message" and message.preview(500)
        ]
        if not candidates:
            candidates = [
                message.preview(500)
                for message in self.messages
                if message.role != "system" and message.preview(500)
            ]
        if not candidates:
            return ""
        text = re.sub(r"\s+", " ", candidates[0]).strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 3].rstrip() + "..."

    def export_text(self) -> str:
        lines = [f"Session: {self.title or self.session_id}", f"Session ID: {self.session_id}", ""]
        for message in self.messages:
            title = f"{message.role.upper()} ({message.kind})" if message.kind != "message" else message.role.upper()
            lines.append(title)
            lines.append("-" * len(title))
            content = message.preview(10000)
            if content:
                lines.append(content)
            if message.reasoning:
                lines.extend(["", "Reasoning:", message.reasoning])
            if message.tool_calls:
                lines.extend(["", "Tool Calls:"])
                for tool_call in message.tool_calls:
                    lines.append(f"- {tool_call.name} {tool_call.arguments_json}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write_transcript(self) -> Path:
        transcript_path = self.session_dir / "transcript.md"
        lines = [
            f"# Session {self.title or self.session_id}",
            "",
            f"- session_id: `{self.session_id}`",
            "",
        ]
        for message in self.messages:
            title = f"{message.role.upper()} ({message.kind})" if message.kind != "message" else message.role.upper()
            lines.append(f"## {title}")
            lines.append("")
            content = message.preview(10000)
            if content:
                lines.append(content)
                lines.append("")
            if message.reasoning:
                lines.extend(["### Reasoning", "", "```text", message.reasoning, "```", ""])
            if message.tool_calls:
                lines.extend(["### Tool Calls", ""])
                for tool_call in message.tool_calls:
                    lines.append(f"- `{tool_call.name}` {tool_call.arguments_json}")
                lines.append("")
        transcript_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return transcript_path

    def _persist_snapshot(self) -> Path:
        snapshot_path = self.session_dir / "session.json"
        payload = {
            "session_id": self.session_id,
            "title": self.title,
            "messages": [message.to_dict() for message in self.messages],
        }
        snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return snapshot_path


def _normalize_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
