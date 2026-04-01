from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console

from .attachments import AttachmentManager
from .commands import CommandDispatcher
from .config import Config
from .engine import PermissionContext, QueryEngine, SessionServices, SessionStore
from .model import ChatModel
from .prompts import prompt_manager
from .services import (
    ContextFileTracker,
    HookManager,
    MCPManager,
    PermissionManager,
    PluginManager,
    SessionCatalog,
    SettingsManager,
    UsageTracker,
    WorktreeManager,
)
from .tools import ToolRegistry, build_default_tools
from .agent.skills import SkillLoader
from .agent.tasks import PersistentTaskManager
from .agent.todo import TodoManager

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
        self.display_message(f"Anuris ({model})")

    def display_reasoning(self, content: str) -> None:
        if content.strip():
            self.display_message(content)

    @staticmethod
    def _render(content: Any) -> str:
        if isinstance(content, str):
            return content
        buffer = StringIO()
        console = Console(file=buffer, force_terminal=False, color_system=None, width=120)
        console.print(content)
        return buffer.getvalue().rstrip("\n")


class ChatSession:
    """Unified session wrapper around the new QueryEngine architecture."""

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
        self.initial_workspace_root = (workspace_root or Path.cwd()).resolve()
        self.workspace_root = self.initial_workspace_root
        self.model = model or ChatModel(config)
        self.event_callback = event_callback
        self.session_id = session_id or self._build_default_session_id()
        self.request_counter = 0
        self.agent_mode = True
        self.attachment_manager = AttachmentManager()

        system_prompt = prompt_manager.resolve_prompt_source(config.system_prompt)
        self.session_store = SessionStore(system_prompt, self.workspace_root, self.session_id)
        self.history = self.session_store
        self.services = self._build_services(self.workspace_root)
        self.tool_registry = ToolRegistry(build_default_tools())
        self.engine = QueryEngine(
            model=self.model,
            session_store=self.session_store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace_root,
            config=self.config,
            event_callback=self._on_engine_event,
            ui=self.ui,
            switch_workspace=self.switch_workspace,
            reset_workspace=self.reset_workspace,
        )
        self.command_dispatcher = CommandDispatcher(self)

    @property
    def is_headless(self) -> bool:
        return isinstance(self.ui, HeadlessUI)

    def handle_input(
        self,
        user_input: str,
        request_kind: str = "message",
        attachment_paths: Optional[List[str]] = None,
    ) -> SessionResponse:
        request_id = self._next_request_id()
        self._clear_ui_output()
        self._emit_event("request_started", request_id=request_id, request_kind=request_kind, agent_mode=self.agent_mode)
        try:
            added_attachments = self._attach_paths(attachment_paths or [])
            self._emit_event(
                "user_input_received",
                request_id=request_id,
                request_kind=request_kind,
                attachment_paths=[attachment.path for attachment in added_attachments],
            )

            if user_input.startswith("/"):
                response = self._handle_command_input(request_id, request_kind, user_input)
            else:
                response = self._handle_query_input(request_id, request_kind, user_input)

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
            self._emit_event("request_failed", request_id=request_id, request_kind=request_kind, error=str(exc))
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

    def _handle_query_input(self, request_id: str, request_kind: str, user_input: str) -> SessionResponse:
        attachments = self.attachment_manager.prepare_for_api() if self.attachment_manager.attachments else []
        attachment_meta = [attachment.to_dict() for attachment in self.attachment_manager.attachments]
        for attachment in self.attachment_manager.attachments:
            self.services.context_files.record(attachment.path)
        self.attachment_manager.clear_attachments()
        self.services.usage_tracker.record_query(user_input)

        allowed_tool_names = None if self.agent_mode else set()
        permission_context = self.services.permission_manager.create_context(
            agent_mode=self.agent_mode,
            explicit_allowed_tools=allowed_tool_names,
        )
        result = self.engine.submit(
            user_input,
            attachments=attachments,
            permission_context=permission_context,
            allowed_tool_names=allowed_tool_names,
            metadata={"attachments": attachment_meta, "request_id": request_id, "request_kind": request_kind},
        )

        if result.reasoning_text:
            self.ui.display_reasoning(result.reasoning_text)
        if result.final_text:
            self.ui.display_message(f"Anuris: {result.final_text}", style="bold blue")
        self.services.usage_tracker.record_response(result.final_text, result.reasoning_text)

        output_text = self._consume_ui_output()
        return SessionResponse(
            request_id=request_id,
            request_kind=request_kind,
            agent_mode=self.agent_mode,
            final_text=result.final_text,
            reasoning_text=result.reasoning_text,
            output_text=output_text,
            round_count=result.rounds,
            tool_events=result.tool_events,
        )

    def _on_engine_event(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type", "")
        if event_type == "tool_called":
            self.services.usage_tracker.record_tool_call()
        if event_type in {"tool_called", "tool_result", "compact_boundary", "request_finished"}:
            hook_results = self.services.hook_manager.run(event_type, event)
            if not self.is_headless:
                for result in hook_results:
                    if result["returncode"] != "0":
                        message = result["stderr"] or result["stdout"] or "(no output)"
                        self.ui.display_message(f"[hook:{event_type}] {message}", style="red")
        if not self.is_headless:
            if event_type == "agent_round_started":
                self.ui.display_message(f"[agent] round {event.get('round')}", style="cyan")
            elif event_type == "tool_called":
                self.ui.display_message(f"[tool] {event.get('tool_name')}", style="yellow")
            elif event_type == "tool_result":
                preview = str(event.get("content", ""))[:240]
                style = "red" if event.get("is_error") else "green"
                self.ui.display_message(f"[tool-result] {preview}", style=style)
            elif event_type == "compact_boundary":
                self.ui.display_message("[agent] context compacted", style="magenta")
        self._emit_event(event_type, **{key: value for key, value in event.items() if key != "type"})

    def _attach_paths(self, attachment_paths: List[str]) -> List[Any]:
        added = []
        for path in attachment_paths:
            success, _ = self.attachment_manager.add_attachment(path)
            if success:
                added.append(self.attachment_manager.attachments[-1])
        return added

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if not self.event_callback:
            return
        self.event_callback({"type": event_type, **payload})

    def _clear_ui_output(self) -> None:
        if hasattr(self.ui, "clear_output"):
            self.ui.clear_output()

    def _consume_ui_output(self) -> str:
        if hasattr(self.ui, "consume_output"):
            return self.ui.consume_output()
        return ""

    def switch_workspace(self, raw_path: str) -> str:
        target = self.services.worktree_manager.resolve_target(raw_path)
        self.workspace_root = target
        self._refresh_workspace_services()
        return f"Switched workspace to {self.workspace_root}"

    def reset_workspace(self) -> str:
        self.workspace_root = self.initial_workspace_root
        self._refresh_workspace_services()
        return f"Returned to primary workspace {self.workspace_root}"

    def _refresh_workspace_services(self) -> None:
        previous_permission_mode = self.services.permission_manager.mode
        previous_settings = self.services.settings_manager.runtime
        self.services = self._build_services(self.workspace_root)
        self.services.permission_manager.set_mode(previous_permission_mode)
        self.services.settings_manager.runtime = previous_settings
        self.session_store.retarget_workspace(self.workspace_root)
        self.engine.services = self.services
        self.engine.workspace_root = self.workspace_root
        self.tool_registry = ToolRegistry(build_default_tools())
        self.engine.tool_registry = self.tool_registry

    def _build_services(self, workspace_root: Path) -> SessionServices:
        plugin_manager = PluginManager(workspace_root)
        skill_dirs = [
            workspace_root / ".anuris_skills",
            workspace_root / "skills",
            *plugin_manager.skill_dirs(),
        ]
        return SessionServices(
            todo_manager=TodoManager(),
            task_manager=PersistentTaskManager(workspace_root / ".anuris" / "tasks"),
            skill_loader=SkillLoader(workspace_root, skills_dirs=skill_dirs),
            permission_manager=PermissionManager(),
            session_catalog=SessionCatalog(workspace_root),
            worktree_manager=WorktreeManager(workspace_root),
            plugin_manager=plugin_manager,
            mcp_manager=MCPManager(workspace_root),
            settings_manager=SettingsManager(),
            hook_manager=HookManager(workspace_root),
            context_files=ContextFileTracker(workspace_root),
            usage_tracker=UsageTracker(),
        )

    def run_prompt_command(self, label: str, prompt: str) -> str:
        self.services.usage_tracker.record_query(prompt)
        permission_context = self.services.permission_manager.create_context(
            agent_mode=self.agent_mode,
            explicit_allowed_tools=None if self.agent_mode else set(),
        )
        result = self.engine.submit(
            prompt,
            permission_context=permission_context,
            allowed_tool_names=None if self.agent_mode else set(),
            metadata={"command_prompt": label},
        )
        self.services.usage_tracker.record_response(result.final_text, result.reasoning_text)
        if result.reasoning_text:
            self.ui.display_reasoning(result.reasoning_text)
        if result.final_text:
            self.ui.display_message(f"Anuris: {result.final_text}", style="bold blue")
        return result.final_text

    def _next_request_id(self) -> str:
        self.request_counter += 1
        return f"{self.session_id}_{self.request_counter:04d}"

    @staticmethod
    def _build_default_session_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
