from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from rich.panel import Panel


@dataclass
class CommandSpec:
    name: str
    description: str
    usage: str
    handler: Callable[[str], None]
    aliases: tuple[str, ...] = ()


class CommandDispatcher:
    """Registry-backed slash command layer inspired by Claude Code."""

    def __init__(self, session: Any, extra_handlers: Optional[Dict[str, Callable[[str], None]]] = None):
        self.session = session
        self.ui = session.ui
        self.commands: Dict[str, CommandSpec] = {}

        self._register("help", "Show available commands.", "/help", self._handle_help)
        self._register("clear", "Clear conversation state and pending attachments.", "/clear", self._handle_clear)
        self._register("save", "Save the current session transcript as JSON.", "/save [filename]", self._handle_save)
        self._register("load", "Load a saved JSON session.", "/load [filename]", self._handle_load)
        self._register("attach", "Queue local files as message attachments.", "/attach <glob...>", self._handle_attach)
        self._register("detach", "Remove one or all pending attachments.", "/detach [index]", self._handle_detach)
        self._register("files", "List pending attachments.", "/files", self._handle_files)
        self._register("agent", "Enable, disable, or inspect tool mode.", "/agent [on|off|status]", self._handle_agent)
        self._register("compact", "Compact older context into a summary boundary.", "/compact [focus]", self._handle_compact)
        self._register("todos", "Render the in-memory todo board.", "/todos", self._handle_todos)
        self._register("tasks", "Render the persistent task board.", "/tasks", self._handle_tasks)
        self._register("skills", "Render discovered skill files.", "/skills", self._handle_skills)
        self._register("status", "Show session status and active capabilities.", "/status", self._handle_status)
        self._register("model", "Show or update the active model name.", "/model [name]", self._handle_model)
        self._register("config", "Show the current runtime config.", "/config", self._handle_config)
        self._register("agents", "Show subagent capability status.", "/agents", self._handle_agents)

        if extra_handlers:
            for name, handler in extra_handlers.items():
                self._register(name, "Custom command.", f"/{name}", handler)

    def _register(
        self,
        name: str,
        description: str,
        usage: str,
        handler: Callable[[str], None],
        aliases: tuple[str, ...] = (),
    ) -> None:
        spec = CommandSpec(name=name, description=description, usage=usage, handler=handler, aliases=aliases)
        self.commands[name] = spec
        for alias in aliases:
            self.commands[alias] = spec

    def execute(self, command_name: str, command_args: str) -> bool:
        spec = self.commands.get(command_name)
        if not spec:
            return False
        spec.handler(command_args)
        return True

    def _handle_help(self, args: str) -> None:
        del args
        lines = ["[bold cyan]Anuris Command Palette[/bold cyan]", ""]
        seen: set[str] = set()
        for key in sorted(self.commands):
            spec = self.commands[key]
            if spec.name in seen:
                continue
            seen.add(spec.name)
            alias_text = f" (aliases: {', '.join(spec.aliases)})" if spec.aliases else ""
            lines.append(f"[green]{spec.usage}[/green]{alias_text}")
            lines.append(f"    {spec.description}")
        self.ui.display_message(Panel.fit("\n".join(lines), border_style="blue"))

    def _handle_clear(self, args: str) -> None:
        del args
        self.session.session_store.reset()
        self.session.attachment_manager.clear_attachments()
        self.ui.display_message("Session state cleared.", style="yellow")

    def _handle_save(self, args: str) -> None:
        filename = args.strip() or "anuris_session.json"
        path = self.session.session_store.save(filename)
        self.ui.display_message(f"Saved session to {path}", style="green")

    def _handle_load(self, args: str) -> None:
        filename = args.strip() or "anuris_session.json"
        try:
            path = self.session.session_store.load(filename)
        except FileNotFoundError:
            self.ui.display_message(f"File not found: {filename}", style="red")
            return
        self.ui.display_message(f"Loaded session from {path}", style="green")

    def _handle_attach(self, args: str) -> None:
        patterns = [item for item in args.split() if item]
        if not patterns:
            self.ui.display_message("Usage: /attach <glob...>", style="yellow")
            return
        for pattern in patterns:
            expanded = glob.glob(os.path.expanduser(pattern))
            if not expanded:
                self.ui.display_message(f"No files matched: {pattern}", style="red")
                continue
            for path in expanded:
                success, message = self.session.attachment_manager.add_attachment(path)
                self.ui.display_message(message, style="green" if success else "red")

    def _handle_detach(self, args: str) -> None:
        raw = args.strip()
        if not raw:
            self.session.attachment_manager.clear_attachments()
            self.ui.display_message("Cleared all pending attachments.", style="yellow")
            return
        try:
            index = int(raw)
        except ValueError:
            self.ui.display_message("Attachment index must be an integer.", style="red")
            return
        success, message = self.session.attachment_manager.remove_attachment(index)
        self.ui.display_message(message, style="green" if success else "red")

    def _handle_files(self, args: str) -> None:
        del args
        attachments = self.session.attachment_manager.list_attachments()
        if attachments:
            self.ui.display_attachments(attachments)
        else:
            self.ui.display_message("No pending attachments.", style="yellow")

    def _handle_agent(self, args: str) -> None:
        action = (args.strip().lower() or "status")
        if action == "status":
            state = "on" if self.session.agent_mode else "off"
            self.ui.display_message(f"Agent tool mode is {state}.", style="cyan")
            return
        if action in {"on", "off"}:
            self.session.agent_mode = action == "on"
            state = "enabled" if self.session.agent_mode else "disabled"
            self.ui.display_message(f"Agent tool mode {state}.", style="green")
            return
        self.ui.display_message("Usage: /agent [on|off|status]", style="yellow")

    def _handle_compact(self, args: str) -> None:
        summary = self.session.session_store.compact_history(args.strip())
        self.ui.display_message(summary, style="cyan")

    def _handle_todos(self, args: str) -> None:
        del args
        self.ui.display_message(self.session.services.todo_manager.render(), style="cyan")

    def _handle_tasks(self, args: str) -> None:
        del args
        self.ui.display_message(self.session.services.task_manager.list_all(), style="cyan")

    def _handle_skills(self, args: str) -> None:
        del args
        self.ui.display_message(self.session.services.skill_loader.render_catalog(), style="cyan")

    def _handle_status(self, args: str) -> None:
        del args
        active_tools = ", ".join(sorted(self.session.tool_registry.by_name))
        lines = [
            f"Model: {self.session.config.model}",
            f"Base URL: {self.session.config.base_url}",
            f"Workspace: {self.session.workspace_root}",
            f"Tool mode: {'on' if self.session.agent_mode else 'off'}",
            f"Tools: {active_tools}",
        ]
        self.ui.display_message(Panel.fit("\n".join(lines), border_style="cyan"))

    def _handle_model(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(f"Current model: {self.session.config.model}", style="cyan")
            return
        self.session.config.model = requested
        if hasattr(self.session.model, "config"):
            self.session.model.config.model = requested
        self.ui.display_message(f"Updated model to {requested}", style="green")

    def _handle_config(self, args: str) -> None:
        del args
        payload = self.session.config.to_dict()
        safe_payload = {key: value for key, value in payload.items() if key != "api_key"}
        rendered = "\n".join(f"{key}: {value}" for key, value in safe_payload.items())
        self.ui.display_message(Panel.fit(rendered, border_style="magenta"))

    def _handle_agents(self, args: str) -> None:
        del args
        self.ui.display_message(
            "Subagent tool is available through the model-facing `task` tool. "
            "Readonly subagents expose bash/read/search/skill/task read APIs by default.",
            style="cyan",
        )
