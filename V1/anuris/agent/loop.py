import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..model import ChatModel
from .tools import TOOL_SCHEMAS, AgentToolExecutor


@dataclass
class AgentRunResult:
    """Result of one agent execution loop."""

    final_text: str
    rounds: int
    tool_events: List[str] = field(default_factory=list)


class AgentLoopRunner:
    """Minimal s01+s02 style loop: model -> tool calls -> tool results -> repeat."""

    def __init__(
        self,
        model: ChatModel,
        tool_executor: Optional[AgentToolExecutor] = None,
        max_rounds: int = 16,
        require_reasoning_content: bool = False,
    ):
        self.model = model
        self.tool_executor = tool_executor or AgentToolExecutor()
        self.max_rounds = max_rounds
        self.require_reasoning_content = require_reasoning_content

    def run(
        self,
        messages: List[Dict[str, Any]],
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentRunResult:
        if not messages or not isinstance(messages, list):
            raise ValueError("Invalid messages format")

        api_messages = [self._normalize_message(message) for message in messages]
        if attachments and api_messages and api_messages[-1].get("role") == "user":
            content = [{"type": "text", "text": api_messages[-1]["content"]}]
            content.extend(attachments)
            api_messages[-1]["content"] = content

        tool_events: List[str] = []

        for round_index in range(1, self.max_rounds + 1):
            response = self.model.create_completion(
                messages=api_messages,
                stream=False,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
            )
            message = response.choices[0].message
            tool_calls = message.tool_calls or []

            assistant_message = {
                "role": "assistant",
                "content": message.content or "",
            }
            if self.require_reasoning_content:
                assistant_message["reasoning_content"] = getattr(message, "reasoning_content", None)
            if tool_calls:
                assistant_message["tool_calls"] = [self._tool_call_to_dict(tool_call) for tool_call in tool_calls]
            api_messages.append(assistant_message)

            if not tool_calls:
                return AgentRunResult(
                    final_text=message.content or "",
                    rounds=round_index,
                    tool_events=tool_events,
                )

            for tool_call in tool_calls:
                args = self._parse_args(tool_call.function.arguments)
                tool_output = self.tool_executor.execute(tool_call.function.name, args)
                tool_events.append(f"{tool_call.function.name} -> {tool_output[:200]}")
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(tool_output),
                    }
                )

        raise RuntimeError(f"Agent loop exceeded max rounds ({self.max_rounds})")

    @staticmethod
    def _parse_args(raw_args: Optional[str]) -> Dict[str, Any]:
        if not raw_args:
            return {}
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _tool_call_to_dict(tool_call: Any) -> Dict[str, Any]:
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments or "{}",
            },
        }

    def _normalize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(message)
        if self.require_reasoning_content and normalized.get("role") == "assistant":
            normalized.setdefault("reasoning_content", None)
        return normalized
