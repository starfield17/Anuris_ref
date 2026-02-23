import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..model import ChatModel
from .compact import ContextCompactor
from .tools import AgentToolExecutor, build_tool_schemas


@dataclass
class AgentRunResult:
    """Result of one agent execution loop."""

    final_text: str
    rounds: int
    tool_events: List[str] = field(default_factory=list)


class AgentLoopRunner:
    """s01+s02 loop with optional s03/s04/s05/s06/s07/s08 capabilities."""

    def __init__(
        self,
        model: ChatModel,
        tool_executor: Optional[AgentToolExecutor] = None,
        workspace_root: Optional[Path] = None,
        max_rounds: int = 16,
        require_reasoning_content: bool = False,
        include_todo: bool = True,
        include_task: bool = True,
        include_write_edit: bool = True,
        include_task_board: bool = True,
        include_skill_loading: bool = True,
        include_background_tasks: bool = True,
        include_team_ops: bool = True,
        include_compaction: bool = True,
        compaction_threshold_tokens: int = 50000,
        keep_recent_tool_messages: int = 3,
        teammate_max_rounds: int = 24,
        teammate_max_tool_calls: int = 80,
        teammate_max_runtime_sec: int = 600,
        teammate_idle_timeout_sec: int = 60,
        teammate_poll_interval_sec: int = 5,
        teammate_readonly_role_keywords: Optional[List[str]] = None,
    ):
        self.model = model
        self.max_rounds = max_rounds
        self.require_reasoning_content = require_reasoning_content
        self.include_todo = include_todo
        self.include_task = include_task
        self.include_write_edit = include_write_edit
        self.include_task_board = include_task_board
        self.include_skill_loading = include_skill_loading
        self.include_background_tasks = include_background_tasks
        self.include_team_ops = include_team_ops
        self.include_compaction = include_compaction
        self.teammate_max_rounds = max(1, int(teammate_max_rounds))
        self.teammate_max_tool_calls = max(1, int(teammate_max_tool_calls))
        self.teammate_max_runtime_sec = max(10, int(teammate_max_runtime_sec))
        self.teammate_idle_timeout_sec = max(5, int(teammate_idle_timeout_sec))
        self.teammate_poll_interval_sec = max(1, int(teammate_poll_interval_sec))
        self.teammate_readonly_role_keywords = tuple(
            item.lower()
            for item in (
                teammate_readonly_role_keywords
                or [
                    "readonly",
                    "read-only",
                    "review",
                    "reviewer",
                    "qa",
                    "research",
                    "auditor",
                    "observer",
                ]
            )
        )

        if tool_executor is None:
            self.tool_executor = AgentToolExecutor(
                workspace_root=workspace_root,
                include_write_edit=include_write_edit,
                include_todo=include_todo,
                include_task=include_task,
                include_task_board=include_task_board,
                include_skill_loading=include_skill_loading,
                include_background_tasks=include_background_tasks,
                include_team_ops=include_team_ops,
            )
        else:
            self.tool_executor = tool_executor

        self.tool_schemas = build_tool_schemas(
            include_write_edit=include_write_edit,
            include_todo=include_todo,
            include_task=include_task,
            include_task_board=include_task_board,
            include_skill_loading=include_skill_loading,
            include_background_tasks=include_background_tasks,
            include_team_ops=include_team_ops,
        )

        if include_task and getattr(self.tool_executor, "subagent_runner", None) is None:
            self.tool_executor.set_subagent_runner(self._run_subagent)
        if include_team_ops and getattr(self.tool_executor, "team_manager", None) is not None:
            self.tool_executor.set_teammate_runner(self._run_teammate_worker)

        transcript_dir = Path(getattr(self.tool_executor, "workspace_root", Path.cwd())) / ".anuris_transcripts"
        self.compactor = ContextCompactor(
            model=self.model,
            transcript_dir=transcript_dir,
            keep_recent_tool_messages=keep_recent_tool_messages,
            threshold_tokens=compaction_threshold_tokens,
        )

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
            if self.include_background_tasks:
                notifications = self.tool_executor.drain_background_notifications()
                if notifications:
                    lines = [
                        f"[bg:{item['task_id']}] {item['status']}: {item['result']}"
                        for item in notifications
                    ]
                    text = "\n".join(lines)
                    api_messages.append(
                        {
                            "role": "user",
                            "content": f"<background-results>\n{text}\n</background-results>",
                        }
                    )
                    api_messages.append(
                        {
                            "role": "assistant",
                            "content": "Noted background task updates.",
                        }
                    )
                    if progress_callback:
                        progress_callback(f"[agent] received {len(notifications)} background update(s)")

            if self.include_compaction:
                self.compactor.micro_compact(api_messages)
                if self.compactor.should_auto_compact(api_messages):
                    api_messages = self.compactor.auto_compact(api_messages)
                    if progress_callback:
                        progress_callback("[agent] context auto-compacted")

            if progress_callback:
                progress_callback(f"[agent] round {round_index}...")
            response = self.model.create_completion(
                messages=api_messages,
                stream=False,
                tools=self.tool_schemas,
                tool_choice="auto",
            )
            payload = self._extract_assistant_payload(response)
            tool_calls = payload["tool_calls"]

            assistant_message = {
                "role": "assistant",
                "content": payload["content"],
            }
            if self.require_reasoning_content:
                assistant_message["reasoning_content"] = payload.get("reasoning_content")
            if tool_calls:
                assistant_message["tool_calls"] = [self._tool_call_to_dict(tool_call) for tool_call in tool_calls]
            api_messages.append(assistant_message)

            if not tool_calls:
                return AgentRunResult(
                    final_text=payload["content"],
                    rounds=round_index,
                    tool_events=tool_events,
                )

            for tool_call in tool_calls:
                args = self._parse_args(tool_call.get("arguments"))
                tool_output = self.tool_executor.execute(tool_call["name"], args)
                event = f"{tool_call['name']} -> {tool_output[:200]}"
                tool_events.append(event)
                if progress_callback:
                    progress_callback(f"[tool] {event}")
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
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

    def get_skill_snapshot(self) -> str:
        """Expose currently discovered skills for CLI commands."""
        return self.tool_executor.get_skill_snapshot()

    def get_background_snapshot(self, task_id: Optional[str] = None) -> str:
        """Expose current background task status for CLI commands."""
        return self.tool_executor.get_background_snapshot(task_id)

    def get_team_snapshot(self) -> str:
        """Expose teammate roster for CLI commands."""
        return self.tool_executor.get_team_snapshot()

    def get_inbox_snapshot(self, name: Optional[str] = None) -> str:
        """Expose inbox content for lead or teammate."""
        return self.tool_executor.get_inbox_snapshot(name)

    def get_plan_snapshot(self) -> str:
        """Expose plan approval tracker."""
        return self.tool_executor.get_plan_snapshot()

    def get_shutdown_snapshot(self) -> str:
        """Expose shutdown request tracker."""
        return self.tool_executor.get_shutdown_snapshot()

    def should_auto_compact(self, messages: List[Dict[str, Any]]) -> bool:
        """Check whether history should be compacted before the next run."""
        if not self.include_compaction:
            return False
        return self.compactor.should_auto_compact(messages)

    def compact_messages(self, messages: List[Dict[str, Any]], focus: Optional[str] = None) -> List[Dict[str, Any]]:
        """Manually compact a message list and return the new conversation skeleton."""
        return self.compactor.auto_compact(messages, focus=focus)

    def _run_subagent(self, prompt: str, agent_type: str = "Explore") -> str:
        """Run a fresh-context subagent and return only its final summary."""
        allow_write_edit = agent_type != "Explore"
        sub_executor = AgentToolExecutor(
            workspace_root=self.tool_executor.workspace_root,
            include_write_edit=allow_write_edit,
            include_todo=False,
            include_task=False,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
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
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=False,
            include_compaction=False,
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
    def _parse_args(raw_args: Any) -> Dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if not raw_args:
            return {}
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _tool_call_to_dict(tool_call: Any) -> Dict[str, Any]:
        if isinstance(tool_call, dict):
            return {
                "id": tool_call.get("id", "tool_call"),
                "type": "function",
                "function": {
                    "name": tool_call.get("name", ""),
                    "arguments": tool_call.get("arguments", "{}"),
                },
            }
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
        if self.include_skill_loading:
            instruction_lines.append(
                "Use load_skill for specialized repo workflows when needed."
            )
            instruction_lines.append("Skills available:")
            instruction_lines.append(self.tool_executor.get_skill_descriptions())
        if self.include_background_tasks:
            instruction_lines.append(
                "Use background_run for long-running commands and check_background to monitor progress."
            )
        if self.include_team_ops:
            instruction_lines.append(
                "For larger work, use spawn_teammate and coordinate via send_message/read_inbox/broadcast."
            )
            instruction_lines.append(
                "Use shutdown_request/shutdown_status and plan_review/plan_list to manage teammate governance."
            )
        instruction = "\n".join(instruction_lines)
        return [{"role": "system", "content": instruction}] + messages

    def _extract_assistant_payload(self, response: Any) -> Dict[str, Any]:
        """
        Normalize provider responses into one internal shape.
        Supports OpenAI-style `choices[].message` and Anthropic-style `content[]`.
        """
        message = self._extract_openai_message(response)
        if message is not None:
            return {
                "content": self._content_to_text(getattr(message, "content", "")),
                "reasoning_content": getattr(message, "reasoning_content", None),
                "tool_calls": self._normalize_openai_tool_calls(getattr(message, "tool_calls", None) or []),
            }

        payload = self._as_dict(response)
        if isinstance(payload, dict):
            if "choices" in payload:
                try:
                    choice_message = payload["choices"][0]["message"]
                    return {
                        "content": self._content_to_text(choice_message.get("content", "")),
                        "reasoning_content": choice_message.get("reasoning_content"),
                        "tool_calls": self._normalize_openai_tool_calls(choice_message.get("tool_calls") or []),
                    }
                except Exception:
                    pass

            content_blocks = payload.get("content")
            if isinstance(content_blocks, list):
                text_parts: List[str] = []
                reasoning_parts: List[str] = []
                tool_calls: List[Dict[str, str]] = []

                for index, block in enumerate(content_blocks):
                    block_type = self._item_get(block, "type", "")
                    if block_type == "text":
                        text = self._item_get(block, "text", "")
                        if text:
                            text_parts.append(str(text))
                    elif block_type in {"thinking", "redacted_thinking"}:
                        thought = self._item_get(block, "thinking", "") or self._item_get(block, "text", "")
                        if thought:
                            reasoning_parts.append(str(thought))
                    elif block_type == "tool_use":
                        name = str(self._item_get(block, "name", "")).strip()
                        if not name:
                            continue
                        raw_input = self._item_get(block, "input", {})
                        arguments = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
                        tool_calls.append(
                            {
                                "id": str(self._item_get(block, "id", f"tool_use_{index}")),
                                "name": name,
                                "arguments": arguments or "{}",
                            }
                        )

                return {
                    "content": "".join(text_parts),
                    "reasoning_content": "".join(reasoning_parts) or None,
                    "tool_calls": tool_calls,
                }

            output_text = payload.get("output_text")
            if isinstance(output_text, str):
                return {"content": output_text, "reasoning_content": None, "tool_calls": []}

        return {"content": "", "reasoning_content": None, "tool_calls": []}

    @staticmethod
    def _extract_openai_message(response: Any) -> Optional[Any]:
        choices = getattr(response, "choices", None)
        if choices:
            first = choices[0]
            return getattr(first, "message", None)
        return None

    def _normalize_openai_tool_calls(self, tool_calls: Any) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for index, tool_call in enumerate(tool_calls or []):
            if isinstance(tool_call, dict):
                function_payload = tool_call.get("function") or {}
                name = function_payload.get("name") or tool_call.get("name")
                args = function_payload.get("arguments") or tool_call.get("arguments") or "{}"
                call_id = tool_call.get("id", f"tool_call_{index}")
            else:
                function_payload = getattr(tool_call, "function", None)
                name = getattr(function_payload, "name", None)
                args = getattr(function_payload, "arguments", "{}") if function_payload else "{}"
                call_id = getattr(tool_call, "id", f"tool_call_{index}")
            if not name:
                continue
            normalized.append({"id": str(call_id), "name": str(name), "arguments": args or "{}"})
        return normalized

    @staticmethod
    def _item_get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                item_type = self._item_get(item, "type", "")
                if item_type in {"text", "output_text"}:
                    text = self._item_get(item, "text", "")
                    if text:
                        chunks.append(str(text))
            return "".join(chunks)
        return str(content)

    @staticmethod
    def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(value, "__dict__"):
            raw = dict(value.__dict__)
            if isinstance(raw, dict):
                return raw
        return None

    def _run_teammate_worker(self, name: str, role: str, prompt: str) -> None:
        """Run a persistent teammate loop with inbox polling and optional auto-claim."""
        team_manager = getattr(self.tool_executor, "team_manager", None)
        if not team_manager:
            return

        readonly_mode = self._is_readonly_role(role)
        worker_executor = AgentToolExecutor(
            workspace_root=self.tool_executor.workspace_root,
            include_write_edit=True,
            include_todo=False,
            include_task=False,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=False,
        )
        teammate_tools = self._build_teammate_tools(readonly_mode)
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"You are teammate '{name}' with role '{role}'. "
                    "Collaborate with lead via send_message. "
                    "Use plan_submit before major changes. "
                    "When work is done, call idle."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        started_at = time.monotonic()
        total_rounds = 0
        total_tool_calls = 0
        poll_interval_sec = self.teammate_poll_interval_sec
        idle_timeout_sec = self.teammate_idle_timeout_sec

        while True:
            stop_reason = self._teammate_budget_reason(
                started_at=started_at,
                total_rounds=total_rounds,
                total_tool_calls=total_tool_calls,
            )
            if stop_reason:
                self._notify_teammate_stop(team_manager, name, stop_reason)
                team_manager.set_member_status(name, "shutdown")
                return

            team_manager.set_member_status(name, "working")
            idle_requested = False

            for _ in range(max(1, min(self.max_rounds, self.teammate_max_rounds))):
                stop_reason = self._teammate_budget_reason(
                    started_at=started_at,
                    total_rounds=total_rounds,
                    total_tool_calls=total_tool_calls,
                )
                if stop_reason:
                    self._notify_teammate_stop(team_manager, name, stop_reason)
                    team_manager.set_member_status(name, "shutdown")
                    return

                inbox = team_manager.read_inbox(name)
                for msg in inbox:
                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(msg, ensure_ascii=False),
                        }
                    )
                response = self.model.create_completion(
                    messages=messages,
                    stream=False,
                    tools=teammate_tools,
                    tool_choice="auto",
                )
                total_rounds += 1
                payload = self._extract_assistant_payload(response)
                tool_calls = payload["tool_calls"]
                messages.append(
                    {
                        "role": "assistant",
                        "content": payload["content"],
                        **(
                            {"tool_calls": [self._tool_call_to_dict(tc) for tc in tool_calls]}
                            if tool_calls
                            else {}
                        ),
                    }
                )
                if not tool_calls:
                    idle_requested = True
                    break

                for tool_call in tool_calls:
                    stop_reason = self._teammate_budget_reason(
                        started_at=started_at,
                        total_rounds=total_rounds,
                        total_tool_calls=total_tool_calls,
                    )
                    if stop_reason:
                        self._notify_teammate_stop(team_manager, name, stop_reason)
                        team_manager.set_member_status(name, "shutdown")
                        return

                    args = self._parse_args(tool_call.get("arguments"))
                    try:
                        output = self._execute_teammate_tool(
                            worker_executor=worker_executor,
                            teammate=name,
                            role=role,
                            tool_name=tool_call["name"],
                            args=args,
                        )
                    except Exception as exc:
                        output = f"Error: {exc}"
                    total_tool_calls += 1
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": str(output),
                        }
                    )
                    if tool_call["name"] == "idle":
                        idle_requested = True

                if idle_requested:
                    break

            team_manager.set_member_status(name, "idle")

            resume = False
            for _ in range(max(1, idle_timeout_sec // poll_interval_sec)):
                stop_reason = self._teammate_budget_reason(
                    started_at=started_at,
                    total_rounds=total_rounds,
                    total_tool_calls=total_tool_calls,
                )
                if stop_reason:
                    self._notify_teammate_stop(team_manager, name, stop_reason)
                    team_manager.set_member_status(name, "shutdown")
                    return

                time.sleep(poll_interval_sec)
                inbox = team_manager.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        messages.append(
                            {
                                "role": "user",
                                "content": json.dumps(msg, ensure_ascii=False),
                            }
                        )
                    resume = True
                    break

                if self.tool_executor.task_manager:
                    claimed = self.tool_executor.task_manager.claim_next_unblocked(name)
                    if claimed:
                        if len(messages) <= 3:
                            messages.insert(
                                0,
                                {
                                    "role": "user",
                                    "content": (
                                        f"<identity>You are '{name}', role '{role}'. "
                                        "Resume from compacted context.</identity>"
                                    ),
                                },
                            )
                            messages.insert(
                                1,
                                {"role": "assistant", "content": f"I am {name}. Continuing work."},
                            )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"<auto-claimed>Task #{claimed['id']}: {claimed.get('subject', '')}\n"
                                    f"{claimed.get('description', '')}</auto-claimed>"
                                ),
                            }
                        )
                        resume = True
                        break

            if not resume:
                team_manager.set_member_status(name, "shutdown")
                return

    def _execute_teammate_tool(
        self,
        worker_executor: AgentToolExecutor,
        teammate: str,
        role: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> str:
        team_manager = getattr(self.tool_executor, "team_manager", None)
        if not team_manager:
            return "Error: Team manager unavailable"

        readonly_mode = self._is_readonly_role(role)
        if readonly_mode and tool_name in {"write_file", "edit_file"}:
            return f"Error: Role '{role}' is read-only; {tool_name} is blocked"
        if readonly_mode and tool_name == "bash":
            command = str(args.get("command", ""))
            if not self._is_readonly_bash_command(command):
                return f"Error: Role '{role}' is read-only; bash command blocked"

        if tool_name in {"bash", "read_file", "write_file", "edit_file"}:
            return worker_executor.execute(tool_name, args)
        if tool_name == "send_message":
            return team_manager.send_message(teammate, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "read_inbox":
            return team_manager.read_inbox_text(teammate)
        if tool_name == "shutdown_response":
            return team_manager.record_shutdown_response(
                sender=teammate,
                request_id=args["request_id"],
                approve=bool(args["approve"]),
                reason=args.get("reason", ""),
            )
        if tool_name == "plan_submit":
            return team_manager.submit_plan(teammate, args["plan"])
        if tool_name == "claim_task":
            if not self.tool_executor.task_manager:
                return "Error: Task manager unavailable"
            return self.tool_executor.task_manager.claim_task(args["task_id"], teammate)
        if tool_name == "idle":
            return "Entering idle phase."
        return f"Error: Unknown teammate tool '{tool_name}'"

    def _notify_teammate_stop(self, team_manager: Any, teammate: str, reason: str) -> None:
        team_manager.send_message(teammate, "lead", f"[auto-stop] {reason}")

    def _teammate_budget_reason(self, started_at: float, total_rounds: int, total_tool_calls: int) -> Optional[str]:
        elapsed = time.monotonic() - started_at
        if elapsed >= self.teammate_max_runtime_sec:
            return f"{elapsed:.1f}s runtime exceeded limit {self.teammate_max_runtime_sec}s"
        if total_rounds >= self.teammate_max_rounds:
            return f"round budget exceeded ({total_rounds}/{self.teammate_max_rounds})"
        if total_tool_calls >= self.teammate_max_tool_calls:
            return f"tool-call budget exceeded ({total_tool_calls}/{self.teammate_max_tool_calls})"
        return None

    def _is_readonly_role(self, role: str) -> bool:
        lowered = role.strip().lower()
        if not lowered:
            return False
        return any(keyword in lowered for keyword in self.teammate_readonly_role_keywords)

    @staticmethod
    def _is_readonly_bash_command(command: str) -> bool:
        raw = command.strip()
        if not raw:
            return False
        disallowed_fragments = [
            ";",
            "&&",
            "||",
            "|",
            ">",
            "<",
            "$(",
            "`",
            "\n",
        ]
        if any(fragment in raw for fragment in disallowed_fragments):
            return False
        try:
            parts = shlex.split(raw)
        except ValueError:
            return False
        if not parts:
            return False

        cmd = parts[0]
        if cmd in {"pwd", "ls", "cat", "head", "tail", "wc", "rg", "find"}:
            return True
        if cmd == "sed":
            return "-i" not in parts
        if cmd == "git":
            return len(parts) > 1 and parts[1] in {"status", "diff", "log", "show", "branch", "rev-parse"}
        return False

    @staticmethod
    def _build_teammate_tools(readonly_mode: bool = False) -> List[Dict[str, Any]]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": (
                        "Run a shell command in workspace."
                        if not readonly_mode
                        else "Run a read-only shell command in workspace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a message to lead or another teammate.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "content": {"type": "string"},
                            "msg_type": {
                                "type": "string",
                                "enum": ["message", "broadcast"],
                            },
                        },
                        "required": ["to", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_inbox",
                    "description": "Read and drain your inbox.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shutdown_response",
                    "description": "Respond to a shutdown request.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["request_id", "approve"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plan_submit",
                    "description": "Submit plan text for lead approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {"plan": {"type": "string"}},
                        "required": ["plan"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "claim_task",
                    "description": "Claim one task id from task board.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "integer"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "idle",
                    "description": "Signal no immediate work and enter idle polling.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        if not readonly_mode:
            tools.extend(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "description": "Write file contents.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "edit_file",
                            "description": "Edit one text segment in a file.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                },
                                "required": ["path", "old_text", "new_text"],
                            },
                        },
                    },
                ]
            )
        return tools
