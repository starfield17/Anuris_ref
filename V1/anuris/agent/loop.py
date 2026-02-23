import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..model import ChatModel
from .tools import AgentToolExecutor, build_tool_schemas


@dataclass
class AgentRunResult:
    """Result of one agent execution loop."""

    final_text: str
    rounds: int
    tool_events: List[str] = field(default_factory=list)


class AgentLoopRunner:
    """s01+s02 loop with optional s03/s04/s07 capabilities."""

    def __init__(
        self,
        model: ChatModel,
        tool_executor: Optional[AgentToolExecutor] = None,
        max_rounds: int = 16,
        require_reasoning_content: bool = False,
        include_todo: bool = True,
        include_task: bool = True,
        include_write_edit: bool = True,
        include_task_board: bool = True,
    ):
        self.model = model
        self.max_rounds = max_rounds
        self.require_reasoning_content = require_reasoning_content
        self.include_todo = include_todo
        self.include_task = include_task
        self.include_write_edit = include_write_edit
        self.include_task_board = include_task_board

        if tool_executor is None:
            self.tool_executor = AgentToolExecutor(
                include_write_edit=include_write_edit,
                include_todo=include_todo,
                include_task=include_task,
                include_task_board=include_task_board,
            )
        else:
            self.tool_executor = tool_executor

        self.tool_schemas = build_tool_schemas(
            include_write_edit=include_write_edit,
            include_todo=include_todo,
            include_task=include_task,
            include_task_board=include_task_board,
        )

        if include_task and getattr(self.tool_executor, "subagent_runner", None) is None:
            self.tool_executor.set_subagent_runner(self._run_subagent)

    def run(
        self,
        messages: List[Dict[str, Any]],
        attachments: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> AgentRunResult:
        if not messages or not isinstance(messages, list):
            raise ValueError("Invalid messages format")

        api_messages = [self._normalize_message(message) for message in messages]
        api_messages = self._inject_agent_instruction(api_messages)
        if attachments and api_messages and api_messages[-1].get("role") == "user":
            content = [{"type": "text", "text": api_messages[-1]["content"]}]
            content.extend(attachments)
            api_messages[-1]["content"] = content

        tool_events: List[str] = []

        for round_index in range(1, self.max_rounds + 1):
            if progress_callback:
                progress_callback(f"[agent] round {round_index}...")
            response = self.model.create_completion(
                messages=api_messages,
                stream=False,
                tools=self.tool_schemas,
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
                event = f"{tool_call.function.name} -> {tool_output[:200]}"
                tool_events.append(event)
                if progress_callback:
                    progress_callback(f"[tool] {event}")
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(tool_output),
                    }
                )

        raise RuntimeError(f"Agent loop exceeded max rounds ({self.max_rounds})")

    def get_todo_snapshot(self) -> str:
        """Expose current TodoWrite board for UI commands."""
        return self.tool_executor.get_todo_snapshot()

    def get_task_snapshot(self) -> str:
        """Expose current persistent task board for UI commands."""
        return self.tool_executor.get_task_snapshot()

    def _run_subagent(self, prompt: str, agent_type: str = "Explore") -> str:
        """Run a fresh-context subagent and return only its final summary."""
        allow_write_edit = agent_type != "Explore"
        sub_executor = AgentToolExecutor(
            workspace_root=self.tool_executor.workspace_root,
            include_write_edit=allow_write_edit,
            include_todo=False,
            include_task=False,
            include_task_board=False,
        )
        sub_runner = AgentLoopRunner(
            model=self.model,
            tool_executor=sub_executor,
            max_rounds=max(4, self.max_rounds // 2),
            require_reasoning_content=self.require_reasoning_content,
            include_todo=False,
            include_task=False,
            include_write_edit=allow_write_edit,
            include_task_board=False,
        )
        sub_messages = [
            {
                "role": "system",
                "content": "You are a coding subagent. Complete the task and return a concise summary.",
            },
            {"role": "user", "content": prompt},
        ]
        result = sub_runner.run(sub_messages)
        return result.final_text or "(no summary)"

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

    def _inject_agent_instruction(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        instruction_lines = [
            "You are a coding agent. Prefer tools over long prose.",
        ]
        if self.include_todo:
            instruction_lines.append(
                "Use TodoWrite for multi-step tasks. Keep exactly one item in_progress."
            )
        if self.include_task:
            instruction_lines.append(
                "Use task to delegate subtasks with fresh context when helpful."
            )
        if self.include_task_board:
            instruction_lines.append(
                "Use task_create/task_update/task_list to persist longer-running plans."
            )
        instruction = "\n".join(instruction_lines)
        return [{"role": "system", "content": instruction}] + messages
