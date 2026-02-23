import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ChatModel


class ContextCompactor:
    """s06-style context management (micro compact + summary compact)."""

    def __init__(
        self,
        model: ChatModel,
        transcript_dir: Path,
        keep_recent_tool_messages: int = 3,
        threshold_tokens: int = 50000,
    ):
        self.model = model
        self.transcript_dir = transcript_dir
        self.keep_recent_tool_messages = keep_recent_tool_messages
        self.threshold_tokens = threshold_tokens

    @staticmethod
    def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
        """Approximate token count used for compaction threshold checks."""
        return len(json.dumps(messages, default=str)) // 4

    def should_auto_compact(self, messages: List[Dict[str, Any]]) -> bool:
        return self.estimate_tokens(messages) > self.threshold_tokens

    def micro_compact(self, messages: List[Dict[str, Any]]) -> None:
        """Clear large older tool outputs, keeping the most recent ones intact."""
        tool_indices = [index for index, message in enumerate(messages) if message.get("role") == "tool"]
        if len(tool_indices) <= self.keep_recent_tool_messages:
            return

        to_clear = tool_indices[:-self.keep_recent_tool_messages]
        for index in to_clear:
            content = str(messages[index].get("content", ""))
            if len(content) > 120:
                tool_id = messages[index].get("tool_call_id", "unknown")
                messages[index]["content"] = f"[Previous tool output omitted: {tool_id}]"

    def auto_compact(self, messages: List[Dict[str, Any]], focus: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Summarize current conversation context and replace it with a compact version.
        A full transcript is saved under .anuris_transcripts for auditability.
        """
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with open(transcript_path, "w", encoding="utf-8") as file_obj:
            for message in messages:
                file_obj.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")

        system_message = messages[0] if messages and messages[0].get("role") == "system" else {
            "role": "system",
            "content": "You are a coding assistant.",
        }
        conversation_text = json.dumps(messages, ensure_ascii=False, default=str)[:120000]
        focus_hint = f"\nFocus: {focus}" if focus else ""
        summary_prompt = (
            "Summarize this conversation for continuity. "
            "Include: completed work, current state, open decisions, and next actions."
            f"{focus_hint}\n\n"
            f"{conversation_text}"
        )
        summary_response = self.model.create_completion(
            messages=[
                {
                    "role": "system",
                    "content": "You summarize coding conversations faithfully and concisely.",
                },
                {"role": "user", "content": summary_prompt},
            ],
            stream=False,
        )
        summary_text = summary_response.choices[0].message.content or "(summary unavailable)"

        compacted_user = {
            "role": "user",
            "content": (
                f"[Conversation compacted. Transcript: {transcript_path}]\n"
                f"{summary_text}"
            ),
        }
        compacted_assistant = {
            "role": "assistant",
            "content": "Understood. Continuing from compacted context.",
        }
        return [system_message, compacted_user, compacted_assistant]
