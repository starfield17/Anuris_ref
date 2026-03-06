from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console

from .agent import AgentLoopRunner
from .attachments import AttachmentManager
from .commands import CommandDispatcher
from .config import Config
from .history import ChatHistory
from .model import ChatModel
from .prompts import prompt_manager
from .streaming import StreamRenderer


EventCallback = Callable[[Dict[str, Any]], None]


@dataclass
class SessionResponse:
    """Structured result of one session request."""

    request_id: str
    request_kind: str
    agent_mode: bool
    final_text: str = ""
    reasoning_text: str = ""
    interrupted: bool = False
    command_handled: bool = False
    output_text: str = ""
    round_count: int = 0
    tool_events: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_kind": self.request_kind,
            "agent_mode": self.agent_mode,
            "final_text": self.final_text,
            "reasoning_text": self.reasoning_text,
            "interrupted": self.interrupted,
            "command_handled": self.command_handled,
            "output_text": self.output_text,
            "round_count": self.round_count,
            "tool_events": list(self.tool_events),
        }


class HeadlessUI:
    """Minimal UI adapter used by non-interactive sessions."""

    def __init__(self):
        self._fragments: List[str] = []

    def clear_output(self) -> None:
        self._fragments.clear()

    def consume_output(self) -> str:
        output = "".join(self._fragments).strip()
        self.clear_output()
        return output

    def display_message(self, content: Any, style: str = None, end: str = "\n", flush: bool = False) -> None:
        del style, flush
        self._fragments.append(self._render(content))
        self._fragments.append(end)

    def display_separator(self) -> None:
        self._fragments.append("\n----------------------------------------\n")

    def display_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        if not attachments:
            return
        lines = ["Attachments:"]
        for item in attachments:
            lines.append(f"- [{item['index']}] {item['name']} ({item['type']}, {item['size']})")
        self._fragments.append("\n".join(lines) + "\n")

    def display_welcome(self, model: str) -> None:
        self.display_message(f"Anuris_API_CLI ({model})")

    @staticmethod
    def _render(content: Any) -> str:
        if isinstance(content, str):
            return content
        buffer = StringIO()
        console = Console(file=buffer, force_terminal=False, color_system=None, width=120)
        console.print(content)
        return buffer.getvalue().rstrip("\n")


class ChatSession:
    """Reusable chat session that works with both TTY and headless entrypoints."""

    def __init__(
        self,
        config: Config,
        ui: Optional[Any] = None,
        workspace_root: Optional[Path] = None,
        model: Optional[ChatModel] = None,
        event_callback: Optional[EventCallback] = None,
        session_id: Optional[str] = None,
    ):
        self.config = config
        self.ui = ui or HeadlessUI()
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.event_callback = event_callback
        self.session_id = session_id or self._build_default_session_id()
        self.request_counter = 0

        resolved_system_prompt = prompt_manager.resolve_prompt_source(config.system_prompt)
        self.history = ChatHistory(system_prompt=resolved_system_prompt)
        self.model = model or ChatModel(config)
        self.attachment_manager = AttachmentManager()
        self.agent_mode = True
        self.agent_runner = AgentLoopRunner(
            self.model,
            workspace_root=self.workspace_root,
            require_reasoning_content=self._provider_requires_reasoning_content(),
        )
        self.command_dispatcher = CommandDispatcher(
            self.history,
            self.attachment_manager,
            self.ui,
            extra_handlers={
                "agent": self._handle_agent_command,
                "todos": self._handle_todos_command,
                "tasks": self._handle_tasks_command,
                "skills": self._handle_skills_command,
                "compact": self._handle_compact_command,
                "background": self._handle_background_command,
                "bg": self._handle_background_command,
                "team": self._handle_team_command,
                "inbox": self._handle_inbox_command,
                "plans": self._handle_plans_command,
                "shutdowns": self._handle_shutdowns_command,
            },
        )
        self.stream_renderer = StreamRenderer(self.ui)

    @property
    def is_headless(self) -> bool:
        return isinstance(self.ui, HeadlessUI)

    def handle_input(
        self,
        user_input: str,
        request_kind: str = "message",
        attachment_paths: Optional[List[str]] = None,
    ) -> SessionResponse:
        """Process one user request and return a structured result."""
        request_id = self._next_request_id()
        self._clear_ui_output()
        self._emit_event(
            "request_started",
            request_id=request_id,
            request_kind=request_kind,
            agent_mode=self.agent_mode,
        )

        try:
            added_attachments = self._attach_paths(attachment_paths or [])
            self._emit_event(
                "user_message",
                request_id=request_id,
                request_kind=request_kind,
                content=user_input,
                attachment_paths=[attachment.path for attachment in added_attachments],
            )

            if user_input.startswith("/"):
                response = self._handle_command_input(request_id, request_kind, user_input)
            elif self.agent_mode:
                response = self._handle_agent_request(request_id, request_kind, user_input)
            else:
                response = self._handle_stream_request(request_id, request_kind, user_input)

            self._emit_event(
                "request_finished",
                request_id=request_id,
                request_kind=request_kind,
                interrupted=response.interrupted,
                command_handled=response.command_handled,
                final_text=response.final_text,
                round_count=response.round_count,
            )
            return response
        except Exception as exc:
            self._emit_event(
                "request_failed",
                request_id=request_id,
                request_kind=request_kind,
                error=str(exc),
            )
            raise
        finally:
            if self.is_headless:
                self._clear_ui_output()

    def _handle_command_input(self, request_id: str, request_kind: str, user_input: str) -> SessionResponse:
        cmd_parts = user_input[1:].split(maxsplit=1)
        cmd_name = cmd_parts[0]
        cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

        if not self.command_dispatcher.execute(cmd_name, cmd_args):
            raise ValueError(f"Unknown command: {cmd_name}")

        output_text = self._consume_ui_output()
        if output_text:
            self._emit_event(
                "assistant_message",
                request_id=request_id,
                request_kind=request_kind,
                content=output_text,
                source="command",
            )
        return SessionResponse(
            request_id=request_id,
            request_kind=request_kind,
            agent_mode=self.agent_mode,
            final_text=output_text,
            output_text=output_text,
            command_handled=True,
        )

    def _handle_stream_request(self, request_id: str, request_kind: str, user_input: str) -> SessionResponse:
        messages = self.history.messages + [{"role": "user", "content": user_input}]
        api_attachments = self.attachment_manager.prepare_for_api() if self.attachment_manager.attachments else None
        current_attachments = self.attachment_manager.attachments.copy()
        self.attachment_manager.clear_attachments()

        response_stream = self.model.get_response(messages, api_attachments)
        stream_result = self.stream_renderer.process(response_stream)

        if stream_result.full_response:
            self.history.add_message("user", user_input, attachments=current_attachments)
            self.history.add_message(
                "assistant",
                stream_result.full_response,
                stream_result.reasoning_content,
            )

        if stream_result.reasoning_content:
            self._emit_event(
                "assistant_reasoning",
                request_id=request_id,
                request_kind=request_kind,
                content=stream_result.reasoning_content,
            )
        if stream_result.full_response:
            self._emit_event(
                "assistant_message",
                request_id=request_id,
                request_kind=request_kind,
                content=stream_result.full_response,
            )
        elif not stream_result.interrupted:
            raise RuntimeError("No content in response chunks")

        if not self.is_headless and stream_result.full_response:
            self.ui.display_message("")

        return SessionResponse(
            request_id=request_id,
            request_kind=request_kind,
            agent_mode=self.agent_mode,
            final_text=stream_result.full_response,
            reasoning_text=stream_result.reasoning_content,
            interrupted=stream_result.interrupted,
        )

    def _handle_agent_request(self, request_id: str, request_kind: str, user_input: str) -> SessionResponse:
        if self.agent_runner.should_auto_compact(self.history.messages):
            self.history.messages = self.agent_runner.compact_messages(self.history.messages)
            self._emit_event(
                "agent_round_started",
                request_id=request_id,
                request_kind=request_kind,
                round=0,
                note="context compacted before run",
            )
            if not self.is_headless:
                self.ui.display_message("[agent] context compacted before run", style="dim")

        messages = self.history.messages + [{"role": "user", "content": user_input}]
        api_attachments = self.attachment_manager.prepare_for_api() if self.attachment_manager.attachments else None
        current_attachments = self.attachment_manager.attachments.copy()
        self.attachment_manager.clear_attachments()

        if not self.is_headless:
            self.ui.display_message("[agent] processing request...", style="dim")

        result = self.agent_runner.run(
            messages,
            api_attachments,
            progress_callback=(lambda event: self.ui.display_message(event, style="dim")) if not self.is_headless else None,
            event_callback=lambda event: self._emit_agent_event(request_id, request_kind, event),
        )

        if not result.final_text:
            raise RuntimeError("No content in agent response")

        if not self.is_headless:
            self.ui.display_message("\nAnuris: ", style="bold blue", end="")
            self.ui.display_message(result.final_text)

        self.history.add_message("user", user_input, attachments=current_attachments)
        self.history.add_message("assistant", result.final_text, result.reasoning_text or None)

        return SessionResponse(
            request_id=request_id,
            request_kind=request_kind,
            agent_mode=self.agent_mode,
            final_text=result.final_text,
            reasoning_text=result.reasoning_text,
            round_count=result.rounds,
            tool_events=result.tool_events,
        )

    def _attach_paths(self, attachment_paths: List[str]) -> List[Any]:
        added = []
        for file_path in attachment_paths:
            success, message = self.attachment_manager.add_attachment(file_path)
            if not success:
                self.attachment_manager.clear_attachments()
                raise ValueError(message)
            added.append(self.attachment_manager.attachments[-1])
        return added

    def _emit_agent_event(self, request_id: str, request_kind: str, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("request_id", request_id)
        payload.setdefault("request_kind", request_kind)
        self._emit_event(payload.pop("type", "agent_event"), **payload)

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if not self.event_callback:
            return
        event = {
            "type": event_type,
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        event.update(payload)
        self.event_callback(event)

    def _clear_ui_output(self) -> None:
        if self.is_headless:
            self.ui.clear_output()

    def _consume_ui_output(self) -> str:
        if not self.is_headless:
            return ""
        return self.ui.consume_output()

    def _next_request_id(self) -> str:
        self.request_counter += 1
        return f"req_{self.request_counter}"

    def _build_default_session_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"session_{timestamp}"

    def _handle_agent_command(self, args: str) -> None:
        action = args.strip().lower() if args else "status"
        if action in ("status", ""):
            status = "ON" if self.agent_mode else "OFF"
            self.ui.display_message(f"Agent mode: {status}", style="cyan")
            return
        if action == "on":
            self.agent_mode = True
            self.ui.display_message("Agent mode enabled", style="green")
            return
        if action == "off":
            self.agent_mode = False
            self.ui.display_message("Agent mode disabled", style="yellow")
            return
        self.ui.display_message("Usage: /agent [on|off|status]", style="yellow")

    def _handle_todos_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_todo_snapshot(), style="cyan")

    def _handle_tasks_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_task_snapshot(), style="cyan")

    def _handle_skills_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_skill_snapshot(), style="cyan")

    def _handle_compact_command(self, args: str) -> None:
        focus = args.strip() if args else None
        self.history.messages = self.agent_runner.compact_messages(self.history.messages, focus=focus)
        self.ui.display_message("Conversation compacted for continuity", style="green")

    def _handle_background_command(self, args: str) -> None:
        task_id = args.strip() if args and args.strip() else None
        self.ui.display_message(self.agent_runner.get_background_snapshot(task_id), style="cyan")

    def _handle_team_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_team_snapshot(), style="cyan")

    def _handle_inbox_command(self, args: str) -> None:
        target = args.strip() if args and args.strip() else None
        self.ui.display_message(self.agent_runner.get_inbox_snapshot(target), style="cyan")

    def _handle_plans_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_plan_snapshot(), style="cyan")

    def _handle_shutdowns_command(self, args: str) -> None:
        del args
        self.ui.display_message(self.agent_runner.get_shutdown_snapshot(), style="cyan")

    def _provider_requires_reasoning_content(self) -> bool:
        base_url = (self.config.base_url or "").lower()
        model_name = (self.config.model or "").lower()
        if "openrouter" in base_url or "openai.com" in base_url:
            return False
        return "deepseek" in base_url or "deepseek" in model_name
