from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .messages import ConversationMessage


class SessionStore:
    """Conversation state and transcript persistence for one session."""

    def __init__(self, system_prompt: str, workspace_root: Path, session_id: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.session_id = session_id
        self.session_dir = self.workspace_root / ".anuris" / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.messages: List[ConversationMessage] = [
            ConversationMessage(role="system", content=system_prompt, kind="system")
        ]

    def reset(self, system_prompt: Optional[str] = None) -> None:
        prompt = system_prompt or self.system_prompt
        self.messages = [ConversationMessage(role="system", content=prompt, kind="system")]
        self.write_transcript()

    @property
    def system_prompt(self) -> str:
        if not self.messages:
            return ""
        first = self.messages[0]
        return str(first.content if isinstance(first.content, str) else "")

    def append(self, message: ConversationMessage) -> ConversationMessage:
        self.messages.append(message)
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
        self.write_transcript()
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
        return summary

    def write_transcript(self) -> Path:
        transcript_path = self.session_dir / "transcript.md"
        lines = [
            f"# Session {self.session_id}",
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
