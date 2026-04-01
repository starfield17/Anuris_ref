from __future__ import annotations

import glob
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from rich.panel import Panel

from .command_specs import register_analysis_commands, register_event_commands, register_inspection_commands


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
        self._register("permissions", "Show or update the active permission mode.", "/permissions [mode]", self._handle_permissions)
        self._register("session", "Inspect current session or list saved sessions.", "/session [show|list]", self._handle_session)
        self._register("resume", "Resume a stored session by id or latest.", "/resume [session_id]", self._handle_resume)
        self._register("rewind", "Rewind one or more recent conversation turns.", "/rewind [turns]", self._handle_rewind)
        self._register("mcp", "Inspect or modify local MCP resources.", "/mcp <servers|list|add-resource|read>", self._handle_mcp)
        self._register("plugin", "Inspect discovered local plugins.", "/plugin [list|reload]", self._handle_plugin)
        self._register("reload-plugins", "Reload plugin discovery and skill paths.", "/reload-plugins", self._handle_reload_plugins)
        self._register("worktree", "Inspect or switch worktrees.", "/worktree <list|enter|exit>", self._handle_worktree)
        self._register("branch", "Show the current git branch.", "/branch", self._handle_branch)
        self._register("env", "Show environment and runtime details.", "/env", self._handle_env)
        self._register("output-style", "Show or set the output style.", "/output-style [plain|rich]", self._handle_output_style)
        self._register("theme", "Show or set the current theme name.", "/theme [name]", self._handle_theme)
        self._register("vim", "Enable or disable vim mode flag.", "/vim [on|off|status]", self._handle_vim)
        register_analysis_commands(self)
        register_event_commands(self)
        register_inspection_commands(self)

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
        context_files = self.session.services.context_files.render()
        if attachments:
            self.ui.display_attachments(attachments)
        else:
            self.ui.display_message("No pending attachments.", style="yellow")
        self.ui.display_message(context_files, style="cyan")

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
            f"Permission mode: {self.session.services.permission_manager.mode}",
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

    def _handle_permissions(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(self.session.services.permission_manager.render(), style="cyan")
            return
        mode = self.session.services.permission_manager.set_mode(requested)
        self.ui.display_message(f"Permission mode set to {mode}", style="green")

    def _handle_session(self, args: str) -> None:
        action = (args.strip().lower() or "show")
        if action == "show":
            self.ui.display_message(Panel.fit(self.session.session_store.describe(), border_style="cyan"))
            return
        if action == "list":
            self.ui.display_message(self.session.services.session_catalog.render(), style="cyan")
            return
        self.ui.display_message("Usage: /session [show|list]", style="yellow")

    def _handle_resume(self, args: str) -> None:
        session_id = args.strip() or self.session.services.session_catalog.latest_session_id()
        snapshot = self.session.services.session_catalog.snapshot_path(session_id)
        self.session.session_store.load_snapshot_path(snapshot)
        self.ui.display_message(f"Resumed session {session_id}", style="green")

    def _handle_rewind(self, args: str) -> None:
        turns = int(args.strip() or "1")
        removed = self.session.session_store.rewind_turns(turns)
        self.ui.display_message(f"Rewound {removed} message(s).", style="yellow")

    def _handle_mcp(self, args: str) -> None:
        parts = shlex.split(args)
        action = parts[0] if parts else "list"
        if action == "servers":
            self.ui.display_message(self.session.services.mcp_manager.render_servers(), style="cyan")
            return
        if action == "list":
            server = parts[1] if len(parts) > 1 else None
            self.ui.display_message(self.session.services.mcp_manager.render_resources(server), style="cyan")
            return
        if action == "add-resource":
            if len(parts) < 3:
                self.ui.display_message("Usage: /mcp add-resource <name> <path> [description]", style="yellow")
                return
            description = " ".join(parts[3:]) if len(parts) > 3 else ""
            resource = self.session.services.mcp_manager.add_resource(parts[1], parts[2], description=description)
            self.ui.display_message(f"Added MCP resource {resource['name']}", style="green")
            return
        if action == "read":
            if len(parts) < 2:
                self.ui.display_message("Usage: /mcp read <name>", style="yellow")
                return
            content = self.session.services.mcp_manager.read_resource(parts[1])
            self.ui.display_message(content, style="cyan")
            return
        self.ui.display_message("Usage: /mcp <servers|list|add-resource|read>", style="yellow")

    def _handle_plugin(self, args: str) -> None:
        action = (args.strip().lower() or "list")
        if action == "list":
            self.ui.display_message(self.session.services.plugin_manager.render(), style="cyan")
            return
        if action == "reload":
            self._reload_plugins()
            self.ui.display_message("Plugins reloaded.", style="green")
            return
        self.ui.display_message("Usage: /plugin [list|reload]", style="yellow")

    def _handle_reload_plugins(self, args: str) -> None:
        del args
        self._reload_plugins()
        self.ui.display_message("Plugins reloaded.", style="green")

    def _handle_worktree(self, args: str) -> None:
        parts = shlex.split(args)
        action = parts[0] if parts else "list"
        if action == "list":
            self.ui.display_message(self.session.services.worktree_manager.render(), style="cyan")
            return
        if action == "enter":
            if len(parts) < 2:
                self.ui.display_message("Usage: /worktree enter <path>", style="yellow")
                return
            self.ui.display_message(self.session.switch_workspace(parts[1]), style="green")
            return
        if action == "exit":
            self.ui.display_message(self.session.reset_workspace(), style="green")
            return
        self.ui.display_message("Usage: /worktree <list|enter|exit>", style="yellow")

    def _handle_branch(self, args: str) -> None:
        del args
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.session.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            branch = completed.stdout.strip()
        except Exception:
            branch = "(not a git repository)"
        self.ui.display_message(f"branch: {branch}", style="cyan")

    def _handle_env(self, args: str) -> None:
        del args
        lines = [
            f"workspace: {self.session.workspace_root}",
            f"home_workspace: {self.session.initial_workspace_root}",
            f"session_id: {self.session.session_id}",
            f"python: {os.environ.get('PYTHONPATH', '') or '(default)'}",
        ]
        self.ui.display_message(Panel.fit("\n".join(lines), border_style="blue"))

    def _handle_output_style(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(
                f"output_style: {self.session.services.settings_manager.runtime.output_style}",
                style="cyan",
            )
            return
        style = self.session.services.settings_manager.set_output_style(requested)
        self.ui.display_message(f"Output style set to {style}", style="green")

    def _handle_theme(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(f"theme: {self.session.services.settings_manager.runtime.theme}", style="cyan")
            return
        theme = self.session.services.settings_manager.set_theme(requested)
        self.ui.display_message(f"Theme set to {theme}", style="green")

    def _handle_vim(self, args: str) -> None:
        action = (args.strip().lower() or "status")
        if action == "status":
            state = self.session.services.settings_manager.runtime.vim_mode
            self.ui.display_message(f"vim_mode: {state}", style="cyan")
            return
        if action in {"on", "off"}:
            state = self.session.services.settings_manager.set_vim_mode(action == "on")
            self.ui.display_message(f"vim_mode: {state}", style="green")
            return
        self.ui.display_message("Usage: /vim [on|off|status]", style="yellow")

    def _reload_plugins(self) -> None:
        self.session.services.plugin_manager.reload(self.session.workspace_root)
        skill_dirs = [
            self.session.workspace_root / ".anuris_skills",
            self.session.workspace_root / "skills",
            *self.session.services.plugin_manager.skill_dirs(),
        ]
        self.session.services.skill_loader = type(self.session.services.skill_loader)(
            self.session.workspace_root,
            skills_dirs=skill_dirs,
        )
