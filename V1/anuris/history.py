import json
import os
from typing import List, Optional

from .attachments import Attachment
from .prompts import DEFAULT_SYSTEM_PROMPT


class ChatHistory:
    """Manages chat message history with state-focused design."""

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
        self.messages = [{"role": "system", "content": system_prompt}]
        self.reasoning_history = []
        self.attachment_history = []

    def add_message(
        self,
        role: str,
        content: str,
        reasoning_content: Optional[str] = None,
        attachments: Optional[List[Attachment]] = None,
    ) -> None:
        """Add message to history with attachments."""
        self.messages.append({"role": role, "content": content})
        if reasoning_content:
            self.reasoning_history.append({"role": role, "reasoning_content": reasoning_content})

        if attachments:
            attachment_info = [attachment.to_dict() for attachment in attachments]
            self.attachment_history.append({"role": role, "attachments": attachment_info})
        else:
            self.attachment_history.append({"role": role, "attachments": []})

    def clear(self, system_prompt: Optional[str] = None) -> None:
        """Clear history, keeping system prompt."""
        if system_prompt is None:
            system_prompt = self.messages[0]["content"] if self.messages else DEFAULT_SYSTEM_PROMPT
        self.messages = [{"role": "system", "content": system_prompt}]
        self.reasoning_history = []
        self.attachment_history = []

    def save(self, filename: str) -> None:
        """Save history to file."""
        data = {
            "messages": self.messages,
            "reasoning_history": self.reasoning_history,
            "attachment_history": self.attachment_history,
        }
        with open(filename, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)

    def load(self, filename: str) -> bool:
        """Load history from file."""
        if not os.path.exists(filename):
            return False

        try:
            with open(filename, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
                loaded_messages = data.get("messages", [])
                has_system_prompt = loaded_messages and loaded_messages[0]["role"] == "system"
                if not has_system_prompt:
                    current_system_prompt = self.messages[0]["content"] if self.messages else DEFAULT_SYSTEM_PROMPT
                    loaded_messages.insert(0, {"role": "system", "content": current_system_prompt})

                self.messages = loaded_messages
                self.reasoning_history = data.get("reasoning_history", [])
                self.attachment_history = data.get("attachment_history", [])
                return True
        except Exception as exc:
            print(f"Error loading chat history: {str(exc)}")
            return False
