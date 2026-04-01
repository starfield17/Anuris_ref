from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rich.panel import Panel

from .command_specs import (
    register_analysis_commands,
    register_diagnostic_commands,
    register_event_commands,
    register_inspection_commands,
    register_session_ops_commands,
    register_workspace_commands,
    register_workflow_commands,
)
from .services.settings import DEFAULT_STATUSLINE_FORMAT, SUPPORTED_EFFORT_LEVELS, SUPPORTED_SANDBOX_MODES


@dataclass
class CommandSpec:
    name: str
    description: str
    usage: str
    handler: Callable[[str], None]
    aliases: tuple[str, ...] = ()
    group: str = "General"


class CommandDispatcher:
    """Registry-backed slash command layer inspired by Claude Code."""

    def __init__(self, session: Any, extra_handlers: Optional[Dict[str, Callable[[str], None]]] = None):
        self.session = session
        self.ui = session.ui
        self.commands: Dict[str, CommandSpec] = {}

        self._register("help", "Show available commands.", "/help", self._handle_help)
        self._register("clear", "Clear session, context, memory, or attachments.", "/clear [all|session|context|attachments|memory]", self._handle_clear)
        self._register("save", "Save the current session transcript as JSON.", "/save [filename]", self._handle_save)
        self._register("load", "Load a saved JSON session.", "/load [filename]", self._handle_load)
        self._register("attach", "Queue local files as message attachments.", "/attach <glob...>", self._handle_attach)
        self._register("detach", "Remove one or all pending attachments.", "/detach [index]", self._handle_detach)
        self._register("files", "List attachments and current working set.", "/files", self._handle_files)
        self._register("agent", "Enable, disable, or inspect tool mode.", "/agent [on|off|status]", self._handle_agent)
        self._register("compact", "Compact older context into a summary boundary.", "/compact [focus]", self._handle_compact)
        self._register("todos", "Render the in-memory todo board.", "/todos", self._handle_todos)
        self._register("tasks", "Render the persistent task board.", "/tasks [board|pending|blocked|resume]", self._handle_tasks)
        self._register("skills", "Render discovered skill files.", "/skills", self._handle_skills)
        self._register("status", "Show session, git, context, and runtime status.", "/status", self._handle_status)
        self._register("model", "Show, pick, or update the active model.", "/model [name|pick]", self._handle_model)
        self._register("config", "Show the current runtime config.", "/config", self._handle_config)
        self._register(
            "agents",
            "Inspect or control teammates, inbox, and governance flows.",
            "/agents [status|list|ui|ps|claim-next|spawn|inbox|send|broadcast|shutdown|plans|approve|reject]",
            self._handle_agents,
        )
        self._register("permissions", "Show or update the active permission mode.", "/permissions [mode]", self._handle_permissions)
        self._register("session", "Inspect, preview, list, or pick saved sessions.", "/session [show|list|preview|pick]", self._handle_session)
        self._register("resume", "Resume a stored session by id or latest.", "/resume [session_id]", self._handle_resume)
        self._register("rewind", "Rewind one or more recent conversation turns.", "/rewind [turns]", self._handle_rewind)
        self._register("mcp", "Inspect or modify local MCP resources.", "/mcp <servers|list|add-resource|read>", self._handle_mcp)
        self._register("plugin", "Inspect discovered local plugins.", "/plugin [list|reload]", self._handle_plugin)
        self._register("reload-plugins", "Reload plugin discovery and skill paths.", "/reload-plugins", self._handle_reload_plugins)
        self._register("worktree", "Inspect or switch worktrees.", "/worktree <list|enter|exit>", self._handle_worktree)
        self._register("branch", "Show the current git branch.", "/branch", self._handle_branch)
        self._register("env", "Show environment and runtime details.", "/env", self._handle_env)
        self._register("output-style", "Show, pick, or set the output style.", "/output-style [plain|rich|pick]", self._handle_output_style)
        self._register("theme", "Show, pick, toggle, or set the theme.", "/theme [name|pick|toggle|switch]", self._handle_theme)
        self._register("vim", "Enable or disable vim mode flag.", "/vim [on|off|status]", self._handle_vim)
        self._register("effort", "Show or set the runtime effort level.", "/effort [auto|low|medium|high|max]", self._handle_effort)
        self._register("fast", "Show or toggle fast mode.", "/fast [on|off|toggle|status]", self._handle_fast)
        self._register(
            "statusline",
            "Show, enable, disable, or format the interactive status line.",
            "/statusline [on|off|format <tokens...>|setup [format...]]",
            self._handle_statusline,
        )
        self._register(
            "keybindings",
            "Show, create, reload, or set custom prompt keybindings.",
            "/keybindings [show|template [path]|path <path>|reload|reset]",
            self._handle_keybindings,
        )
        self._register(
            "sandbox-toggle",
            "Manage local sandbox mode and excluded bash patterns.",
            "/sandbox-toggle [workspace-write|read-only|off|exclude <pattern>|include <pattern>|list]",
            self._handle_sandbox_toggle,
            aliases=("sandbox",),
        )
        self._register("thinkback", "List, inspect, or show saved debug traces.", "/thinkback [list|latest|show <id>]", self._handle_thinkback)
        self._register("thinkback-play", "Replay a saved debug trace into a fresh session.", "/thinkback-play <id>", self._handle_thinkback_play)
        self._register("notices", "Inspect or clear queued runtime notices.", "/notices [list|recent|clear]", self._handle_notices)
        self._register("search", "Search sessions, traces, and exports.", "/search <query>", self._handle_search)
        self._register("history-search", "Search saved session transcripts.", "/history-search <query>", self._handle_history_search)
        self._register("trace-search", "Search saved thinkback/debug transcripts.", "/trace-search <query>", self._handle_trace_search)
        self._register("quickopen", "Quick-open a session or trace by id/title.", "/quickopen <query>", self._handle_quickopen)
        self._register("thinking", "Show or toggle provider reasoning mode.", "/thinking [on|off|toggle|status]", self._handle_thinking)
        self._register("picker", "Open a small runtime picker for common settings.", "/picker [model|theme|output|thinking|effort|fast]", self._handle_picker)
        register_analysis_commands(self)
        register_diagnostic_commands(self)
        register_event_commands(self)
        register_inspection_commands(self)
        register_session_ops_commands(self)
        register_workspace_commands(self)
        register_workflow_commands(self)

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
        group: str = "",
    ) -> None:
        spec = CommandSpec(
            name=name,
            description=description,
            usage=usage,
            handler=handler,
            aliases=aliases,
            group=group or self._infer_group(name),
        )
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
        query = args.strip().lower()
        grouped: Dict[str, List[CommandSpec]] = {}
        seen: set[str] = set()
        for key in sorted(self.commands):
            spec = self.commands[key]
            if spec.name in seen:
                continue
            seen.add(spec.name)
            haystack = f"{spec.name} {spec.description} {spec.usage} {spec.group}".lower()
            if query and query not in haystack:
                continue
            grouped.setdefault(spec.group, []).append(spec)

        if not grouped:
            self.ui.display_message(f"No commands matched: {query}", style="yellow")
            return

        lines = ["[bold cyan]Anuris Command Palette[/bold cyan]", ""]
        ordered_groups = ["Core", "Context", "Session", "Diagnostics", "Git", "Runtime", "Tools", "Automation"]
        seen_groups: set[str] = set()
        for group in ordered_groups + sorted(grouped):
            if group in seen_groups:
                continue
            seen_groups.add(group)
            specs = grouped.get(group)
            if not specs:
                continue
            lines.append(f"[bold magenta]{group}[/bold magenta]")
            for spec in specs:
                alias_text = f" (aliases: {', '.join(spec.aliases)})" if spec.aliases else ""
                lines.append(f"[green]{spec.usage}[/green]{alias_text}")
                lines.append(f"    {spec.description}")
            lines.append("")
        self.ui.display_message(Panel.fit("\n".join(lines).rstrip(), border_style="blue"))

    def _handle_clear(self, args: str) -> None:
        action = (args.strip().lower() or "all")
        if action == "all":
            self.session.session_store.reset()
            self.session.attachment_manager.clear_attachments()
            self.session.services.context_files.clear_all()
            self.ui.display_message("Session, attachments, and context cleared.", style="yellow")
            return
        if action == "session":
            self.session.session_store.reset()
            self.ui.display_message("Session history cleared.", style="yellow")
            return
        if action == "attachments":
            self.session.attachment_manager.clear_attachments()
            self.ui.display_message("Pending attachments cleared.", style="yellow")
            return
        if action == "context":
            self.session.services.context_files.clear_all()
            self.ui.display_message("Context files and added directories cleared.", style="yellow")
            return
        if action == "memory":
            self.session.services.memory_manager.clear()
            self.ui.display_message("Workspace memory cleared.", style="yellow")
            return
        self.ui.display_message("Usage: /clear [all|session|context|attachments|memory]", style="yellow")

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
        action = (args.strip().lower() or "board")
        manager = self.session.services.task_manager
        if action in {"board", "show"}:
            board = manager.render_board()
            resumable = manager.resumable_task("lead")
            lines = [board]
            if resumable:
                lines.extend(
                    [
                        "",
                        "Resume candidate:",
                        f"- #{resumable['id']} {resumable.get('subject', '')} status={resumable.get('status', '')} owner={resumable.get('owner', '') or 'unowned'}",
                    ]
                )
            if hasattr(self.session, "team_runtime"):
                lines.extend(["", self.session.team_runtime.render_governance()])
            self.ui.display_message("\n".join(lines), style="cyan")
            return
        if action == "pending":
            pending = manager.list_by_status("pending")
            self.ui.display_message(
                "\n".join(f"- #{item['id']} {item.get('subject', '')}" for item in pending) if pending else "No pending tasks.",
                style="cyan",
            )
            return
        if action == "blocked":
            blocked = [item for item in manager.list_records() if item.get("blockedBy") and item.get("status") != "completed"]
            self.ui.display_message(
                "\n".join(f"- #{item['id']} {item.get('subject', '')} blockedBy={item.get('blockedBy')}" for item in blocked) if blocked else "No blocked tasks.",
                style="cyan",
            )
            return
        if action.startswith("resume"):
            resumable = manager.resumable_task()
            if not resumable:
                self.ui.display_message("No resumable tasks.", style="yellow")
                return
            self.ui.display_message(json.dumps(resumable, ensure_ascii=False, indent=2), style="green")
            return
        self.ui.display_message("Usage: /tasks [board|pending|blocked|resume]", style="yellow")

    def _handle_skills(self, args: str) -> None:
        del args
        self.ui.display_message(self.session.services.skill_loader.render_catalog(), style="cyan")

    def _handle_status(self, args: str) -> None:
        del args
        diagnostics = self.session.services.diagnostics or None
        diagnostic_snapshot = diagnostics.snapshot() if diagnostics else {}
        context_snapshot = self.session.services.context_visualizer.analyze()
        notice_summary = self.session.services.notification_center.summary_counts()
        task_summary = self.session.services.task_manager.summary_counts()
        team_summary = self.session.team_runtime.summary_counts() if hasattr(self.session, "team_runtime") else {}
        active_tools = ", ".join(sorted(self.session.tool_registry.by_name))
        git_summary = self._git_summary()
        sections = [
            {
                "title": "Session",
                "lines": [
                    f"session={self.session.session_store.title or self.session.session_id}",
                    f"model={self.session.config.model}",
                    f"workspace={self.session.workspace_root}",
                    f"git={git_summary}",
                ],
            },
            {
                "title": "Runtime",
                "lines": [
                    f"agent_mode={'on' if self.session.agent_mode else 'off'}",
                    f"permission={self.session.services.permission_manager.mode}",
                    f"sandbox={self.session.services.settings_manager.runtime.sandbox_mode}",
                    f"theme={self.session.services.settings_manager.runtime.theme}",
                    f"output={self.session.services.settings_manager.runtime.output_style}",
                    f"effort={self.session.services.settings_manager.runtime.effort_level}",
                    f"fast={self.session.services.settings_manager.runtime.fast_mode}",
                ],
            },
            {
                "title": "Context",
                "lines": [
                    f"approx_chars={context_snapshot['approx_chars']}",
                    f"compact_boundaries={context_snapshot['compact_count']}",
                    f"conversation_items={len(context_snapshot['groups']['conversation'])}",
                    f"file_reads={len(context_snapshot['groups']['file_reads'])}",
                    f"attachments={len(context_snapshot['groups']['attachments'])}",
                ],
            },
            {
                "title": "Tasks",
                "lines": [
                    f"total={task_summary['total']}",
                    f"in_progress={task_summary['in_progress']}",
                    f"pending={task_summary['pending']}",
                    f"blocked={task_summary['blocked']}",
                    f"team_members={team_summary.get('members', 0)} inbox={team_summary.get('lead_inbox', 0)} plans={team_summary.get('plans_pending', 0)}",
                ],
            },
            {
                "title": "Diagnostics",
                "lines": [
                    f"warnings={len(diagnostic_snapshot.get('warnings', []))}",
                    f"queued_notices={notice_summary.get('queued', 0)}",
                    f"background_tasks={diagnostic_snapshot.get('background_tasks', 0)}",
                    f"hooks={diagnostic_snapshot.get('hooks', 0)} plugins={diagnostic_snapshot.get('plugins', 0)} mcp={diagnostic_snapshot.get('mcp_resources', 0)}",
                ],
            },
        ]
        if hasattr(self.ui, "display_runtime_dashboard"):
            self.ui.display_runtime_dashboard(sections, title="status")
        else:
            lines = []
            for section in sections:
                lines.append(f"{section['title']}:")
                lines.extend(f"- {line}" for line in section["lines"])
                lines.append("")
            lines.append(f"Tools: {active_tools}")
            self.ui.display_message(Panel.fit("\n".join(lines).strip(), border_style="cyan"))
            return
        self.ui.display_message(f"Tools: {active_tools}", style="cyan")

    def _handle_model(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(f"Current model: {self.session.config.model}", style="cyan")
            return
        if requested == "pick":
            choice = self._choose_option("model", self._model_options(), default=self.session.config.model)
            if not choice:
                self.ui.display_message("Available models: " + ", ".join(self._model_options()), style="cyan")
                return
            requested = choice
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
        raw = args.strip()
        if not raw or raw == "status":
            lines = [self.session.team_runtime.render_dashboard(), "", self.session.team_runtime.describe(), "", "Subagent note: model-facing `task` delegates bounded work via a fresh subagent context."]
            self.ui.display_message(Panel.fit("\n".join(lines), border_style="cyan"))
            return

        parts = shlex.split(raw)
        action = parts[0].lower()

        if action == "list":
            self.ui.display_message(self.session.team_runtime.list_members(), style="cyan")
            return

        if action in {"ui", "dashboard"}:
            self.ui.display_message(self.session.team_runtime.render_dashboard(), style="cyan")
            return

        if action in {"ps", "processes"}:
            self.ui.display_message(self.session.team_runtime.render_processes(), style="cyan")
            return

        if action == "claim-next":
            owner = parts[1] if len(parts) > 1 else "lead"
            self.ui.display_message(self.session.team_runtime.claim_next(owner), style="green")
            return

        if action == "spawn":
            head, prompt = self._split_prompt_tail(raw[len("spawn") :].strip())
            spawn_parts = shlex.split(head)
            if len(spawn_parts) < 1 or not prompt:
                self.ui.display_message("Usage: /agents spawn <name> [role] -- <prompt>", style="yellow")
                return
            name = spawn_parts[0]
            role = " ".join(spawn_parts[1:]).strip() or "teammate"
            self.ui.display_message(self.session.team_runtime.spawn(name, role, prompt), style="green")
            return

        if action == "inbox":
            name = parts[1] if len(parts) > 1 else "lead"
            self.ui.display_message(self.session.team_runtime.read_inbox(name), style="cyan")
            return

        if action == "send":
            if len(parts) < 3:
                self.ui.display_message("Usage: /agents send <to> <message...>", style="yellow")
                return
            to = parts[1]
            content = self._tail_after_tokens(raw, 2)
            self.ui.display_message(self.session.team_runtime.send_message(to, content), style="green")
            return

        if action == "broadcast":
            content = self._tail_after_tokens(raw, 1)
            if not content:
                self.ui.display_message("Usage: /agents broadcast <message...>", style="yellow")
                return
            self.ui.display_message(self.session.team_runtime.broadcast(content), style="green")
            return

        if action == "shutdown":
            mode = parts[1].lower() if len(parts) > 1 else "list"
            if mode == "request":
                if len(parts) < 3:
                    self.ui.display_message("Usage: /agents shutdown request <teammate>", style="yellow")
                    return
                self.ui.display_message(self.session.team_runtime.request_shutdown(parts[2]), style="green")
                return
            if mode == "status":
                if len(parts) < 3:
                    self.ui.display_message(self.session.team_runtime.list_shutdown_requests(), style="cyan")
                    return
                self.ui.display_message(self.session.team_runtime.shutdown_status(parts[2]), style="cyan")
                return
            if mode == "list":
                self.ui.display_message(self.session.team_runtime.list_shutdown_requests(), style="cyan")
                return
            self.ui.display_message("Usage: /agents shutdown <request|status|list> ...", style="yellow")
            return

        if action in {"plans", "plan"}:
            self.ui.display_message(self.session.team_runtime.list_plan_requests(), style="cyan")
            return

        if action in {"approve", "reject"}:
            if len(parts) < 2:
                self.ui.display_message(f"Usage: /agents {action} <request_id> [feedback...]", style="yellow")
                return
            request_id = parts[1]
            feedback = self._tail_after_tokens(raw, 2)
            self.ui.display_message(
                self.session.team_runtime.review_plan(
                    request_id,
                    approve=action == "approve",
                    feedback=feedback,
                ),
                style="green",
            )
            return

        self.ui.display_message(
            "Usage: /agents [status|list|ui|ps|claim-next|spawn|inbox|send|broadcast|shutdown|plans|approve|reject]",
            style="yellow",
        )

    def _handle_permissions(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            self.ui.display_message(self.session.services.permission_manager.render(), style="cyan")
            return
        mode = self.session.services.permission_manager.set_mode(requested)
        self.ui.display_message(f"Permission mode set to {mode}", style="green")

    def _handle_session(self, args: str) -> None:
        parts = shlex.split(args)
        action = (parts[0].lower() if parts else "show")
        if action == "show":
            details = [
                self.session.session_store.describe(),
                "",
                self.session.session_store.summary_report(limit=5),
            ]
            self.ui.display_message(Panel.fit("\n".join(details), border_style="cyan"))
            return
        if action == "list":
            self.ui.display_message(self.session.services.session_catalog.render(), style="cyan")
            return
        if action == "preview":
            session_id = parts[1] if len(parts) > 1 else self.session.services.session_catalog.latest_session_id()
            preview = self.session.services.session_catalog.preview(session_id)
            snapshot = json.loads(self.session.services.session_catalog.snapshot_path(session_id).read_text(encoding="utf-8"))
            messages = snapshot.get("messages", [])[-8:]
            payload = []
            for index, item in enumerate(messages, start=max(1, len(snapshot.get("messages", [])) - len(messages) + 1)):
                role = str(item.get("role", "unknown"))
                kind = str(item.get("kind", "message"))
                content = item.get("content", "")
                if isinstance(content, list):
                    preview_text = " ".join(str(block.get("text", block)) for block in content if isinstance(block, dict))
                else:
                    preview_text = str(content)
                payload.append({"label": f"{index}:{role}:{kind}", "preview": preview_text.replace("\n", " ")[:180]})
            if hasattr(self.ui, "display_session_preview"):
                self.ui.display_session_preview(payload, title=f"session {session_id}")
                self.ui.display_message(preview, style="cyan")
            else:
                self.ui.display_message(preview, style="cyan")
            return
        if action == "pick":
            sessions = self.session.services.session_catalog.list_sessions()
            options = [f"{item['session_id']} [{item['title'] or 'untitled'}]" for item in sessions]
            choice = self._choose_option("session", options)
            if not choice:
                self.ui.display_message("Available sessions:\n" + self.session.services.session_catalog.render(), style="cyan")
                return
            session_id = choice.split(" ", 1)[0]
            snapshot = self.session.services.session_catalog.snapshot_path(session_id)
            self.session.session_store.load_snapshot_path(snapshot)
            self.ui.display_message(f"Resumed session {session_id}", style="green")
            return
        self.ui.display_message("Usage: /session [show|list|preview|pick]", style="yellow")

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
        if requested == "pick":
            choice = self._choose_option("output style", ["rich", "plain"], default=self.session.services.settings_manager.runtime.output_style)
            if not choice:
                self.ui.display_message("Available output styles: rich, plain", style="cyan")
                return
            requested = choice
        style = self.session.services.settings_manager.set_output_style(requested)
        self.ui.display_message(f"Output style set to {style}", style="green")

    def _handle_theme(self, args: str) -> None:
        requested = args.strip()
        if not requested:
            available = ", ".join(self.session.services.settings_manager.available_themes())
            current = self.session.services.settings_manager.runtime.theme
            self.ui.display_message(f"theme: {current} (available: {available})", style="cyan")
            return
        if requested.lower() == "pick":
            choice = self._choose_option(
                "theme",
                list(self.session.services.settings_manager.available_themes()),
                default=self.session.services.settings_manager.runtime.theme,
            )
            if not choice:
                available = ", ".join(self.session.services.settings_manager.available_themes())
                self.ui.display_message(f"Available themes: {available}", style="cyan")
                return
            requested = choice
        if requested.lower() in {"toggle", "switch"}:
            theme = self.session.services.settings_manager.toggle_theme()
            self.ui.display_message(f"Theme switched to {theme}", style="green")
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

    def _handle_effort(self, args: str) -> None:
        requested = (args.strip().lower() or "status")
        current = self.session.services.settings_manager.runtime.effort_level
        if requested in {"status", "current"}:
            effective = current or "auto"
            description = {
                "auto": "model default balance",
                "low": "quick and lightweight",
                "medium": "balanced depth",
                "high": "more thorough reasoning",
                "max": "deepest local reasoning budget",
            }.get(effective, "custom")
            self.ui.display_message(f"Current effort level: {effective} ({description})", style="cyan")
            return
        try:
            level = self.session.services.settings_manager.set_effort_level(requested)
        except ValueError:
            self.ui.display_message(
                f"Invalid effort level: {requested}. Valid options are: {', '.join(SUPPORTED_EFFORT_LEVELS)}",
                style="red",
            )
            return
        suffix = {
            "auto": "model default balance",
            "low": "quick and lightweight",
            "medium": "balanced depth",
            "high": "more thorough reasoning",
            "max": "deepest local reasoning budget",
        }.get(level, "custom")
        self.ui.display_message(f"Set effort level to {level}: {suffix}", style="green")

    def _handle_fast(self, args: str) -> None:
        action = (args.strip().lower() or "status")
        settings = self.session.services.settings_manager
        if action == "status":
            self.ui.display_message(f"fast_mode: {settings.runtime.fast_mode}", style="cyan")
            return
        if action == "toggle":
            state = settings.toggle_fast_mode()
            self.ui.display_message(f"Fast mode {'ON' if state else 'OFF'}", style="green")
            return
        if action in {"on", "off"}:
            state = settings.set_fast_mode(action == "on")
            self.ui.display_message(f"Fast mode {'ON' if state else 'OFF'}", style="green")
            return
        self.ui.display_message("Usage: /fast [on|off|toggle|status]", style="yellow")

    def _handle_statusline(self, args: str) -> None:
        parts = shlex.split(args)
        settings = self.session.services.settings_manager
        if not parts:
            self.ui.display_message(
                "\n".join(
                    [
                        f"statusline_enabled: {settings.runtime.statusline_enabled}",
                        f"statusline_format: {settings.runtime.statusline_format}",
                        "available_tokens: model mode perm sandbox cwd session usage team fast effort vim",
                    ]
                ),
                style="cyan",
            )
            return
        action = parts[0].lower()
        if action in {"on", "off"}:
            enabled = settings.set_statusline_enabled(action == "on")
            self.ui.display_message(f"Status line {'enabled' if enabled else 'disabled'}", style="green")
            return
        if action == "format":
            value = self._tail_after_tokens(args, 1) or DEFAULT_STATUSLINE_FORMAT
            fmt = settings.set_statusline_format(value)
            self.ui.display_message(f"Updated statusline format: {fmt}", style="green")
            return
        if action == "setup":
            settings.set_statusline_enabled(True)
            value = self._tail_after_tokens(args, 1)
            if value:
                settings.set_statusline_format(value)
            self.ui.display_message(
                f"Status line setup complete. format={settings.runtime.statusline_format}",
                style="green",
            )
            return
        self.ui.display_message("Usage: /statusline [on|off|format <tokens...>|setup [format...]]", style="yellow")

    def _handle_keybindings(self, args: str) -> None:
        parts = shlex.split(args)
        action = parts[0].lower() if parts else "show"
        settings = self.session.services.settings_manager
        if action == "show":
            path = settings.runtime.keybindings_path or "~/.anuris_keybindings.toml (not yet configured)"
            self.ui.display_message(
                "\n".join(
                    [
                        f"keybindings_path: {path}",
                        "supported_actions: submit, submit_alt, paste, undo, redo",
                    ]
                ),
                style="cyan",
            )
            return
        if action == "template":
            requested = parts[1] if len(parts) > 1 else (settings.runtime.keybindings_path or str(Path.home() / ".anuris_keybindings.toml"))
            path = Path(requested).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(self._default_keybindings_template(), encoding="utf-8")
            settings.set_keybindings_path(str(path))
            if hasattr(self.ui, "rebuild_prompt_session"):
                self.ui.rebuild_prompt_session()
            self.ui.display_message(f"Keybindings template ready at {path}", style="green")
            return
        if action == "path":
            if len(parts) < 2:
                self.ui.display_message("Usage: /keybindings path <path>", style="yellow")
                return
            path = Path(parts[1]).expanduser().resolve()
            settings.set_keybindings_path(str(path))
            if hasattr(self.ui, "rebuild_prompt_session"):
                self.ui.rebuild_prompt_session()
            self.ui.display_message(f"Keybindings path set to {path}", style="green")
            return
        if action == "reload":
            if hasattr(self.ui, "rebuild_prompt_session"):
                self.ui.rebuild_prompt_session()
            self.ui.display_message("Reloaded prompt keybindings.", style="green")
            return
        if action == "reset":
            settings.set_keybindings_path("")
            if hasattr(self.ui, "rebuild_prompt_session"):
                self.ui.rebuild_prompt_session()
            self.ui.display_message("Reset prompt keybindings to defaults.", style="green")
            return
        self.ui.display_message("Usage: /keybindings [show|template [path]|path <path>|reload|reset]", style="yellow")

    def _handle_sandbox_toggle(self, args: str) -> None:
        parts = shlex.split(args)
        settings = self.session.services.settings_manager
        if not parts or parts[0].lower() in {"status", "list"}:
            patterns = settings.runtime.excluded_commands or []
            rendered = "\n".join(
                [
                    f"sandbox_mode: {settings.runtime.sandbox_mode}",
                    f"excluded_commands: {patterns if patterns else '(none)'}",
                    "note: this is an Anuris-local tool policy layer, not an OS sandbox.",
                ]
            )
            self.ui.display_message(rendered, style="cyan")
            return
        action = parts[0].lower()
        if action in SUPPORTED_SANDBOX_MODES:
            mode = settings.set_sandbox_mode(action)
            self.ui.display_message(f"Sandbox mode set to {mode}", style="green")
            return
        if action == "exclude":
            pattern = self._tail_after_tokens(args, 1)
            if not pattern:
                self.ui.display_message("Usage: /sandbox-toggle exclude <pattern>", style="yellow")
                return
            settings.add_excluded_command(pattern)
            self.ui.display_message(f'Added "{pattern}" to excluded commands', style="green")
            return
        if action == "include":
            pattern = self._tail_after_tokens(args, 1)
            if not pattern:
                self.ui.display_message("Usage: /sandbox-toggle include <pattern>", style="yellow")
                return
            settings.remove_excluded_command(pattern)
            self.ui.display_message(f'Removed "{pattern}" from excluded commands', style="green")
            return
        self.ui.display_message(
            "Usage: /sandbox-toggle [workspace-write|read-only|off|exclude <pattern>|include <pattern>|list]",
            style="yellow",
        )

    def _handle_thinkback(self, args: str) -> None:
        parts = shlex.split(args)
        action = parts[0].lower() if parts else "list"
        sessions = self._debug_trace_sessions()
        if action == "list":
            if not sessions:
                self.ui.display_message("No thinkback sessions found.", style="yellow")
                return
            lines = ["Thinkback sessions:"]
            for item in sessions[:20]:
                lines.append(
                    f"- {item['session_id']}: status={item['status']} requests={item['request_count']} updated={item['updated_at']} workspace={item['workspace_root']}"
                )
            self.ui.display_message("\n".join(lines), style="cyan")
            return
        if action == "latest":
            if not sessions:
                self.ui.display_message("No thinkback sessions found.", style="yellow")
                return
            transcript = Path(sessions[0]["transcript_path"]).read_text(encoding="utf-8")
            self.ui.display_message(transcript, style="cyan")
            return
        if action == "show":
            if len(parts) < 2:
                self.ui.display_message("Usage: /thinkback show <id>", style="yellow")
                return
            session = next((item for item in sessions if item["session_id"] == parts[1]), None)
            if not session:
                self.ui.display_message(f"Unknown thinkback session: {parts[1]}", style="red")
                return
            self.ui.display_message(Path(session["transcript_path"]).read_text(encoding="utf-8"), style="cyan")
            return
        self.ui.display_message("Usage: /thinkback [list|latest|show <id>]", style="yellow")

    def _handle_thinkback_play(self, args: str) -> None:
        from .debug_server import DebugSessionManager, DebugTraceRunner

        session_id = args.strip()
        if not session_id:
            self.ui.display_message("Usage: /thinkback-play <id>", style="yellow")
            return
        sessions = self._debug_trace_sessions()
        source = next((item for item in sessions if item["session_id"] == session_id), None)
        if not source:
            self.ui.display_message(f"Unknown thinkback session: {session_id}", style="red")
            return
        events_path = Path(source["events_path"])
        steps = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("type") == "user_input_received" and payload.get("content"):
                steps.append({"kind": "input", "content": str(payload["content"])})
        if not steps:
            self.ui.display_message(f"No replayable inputs found in {session_id}", style="yellow")
            return
        replay_id = f"{session_id}_replay"
        debug_dir = self._debug_runs_dir()
        manager = DebugSessionManager(
            base_config=self.session.config,
            workspace_root=self.session.workspace_root,
            debug_dir=debug_dir,
            model_factory=lambda _config: self.session.model,
        )
        runner = DebugTraceRunner(manager)
        result = runner.run_trace(
            {
                "session": {
                    "session_id": replay_id,
                    "session_name": f"replay {session_id}",
                    "agent_mode": self.session.agent_mode,
                },
                "steps": steps,
                "markdown_path": str((debug_dir / f"{replay_id}.md").resolve()),
            }
        )
        self.ui.display_message(
            f"Replayed {session_id} -> {result['session_id']}\nmarkdown_path: {result['markdown_path']}\nevents_path: {result['events_path']}",
            style="green",
        )

    def _handle_notices(self, args: str) -> None:
        action = (args.strip().lower() or "list")
        center = self.session.services.notification_center
        if action in {"list", "show"}:
            self.ui.display_message(center.preview(), style="cyan")
            return
        if action == "recent":
            recent = center.recent()
            if not recent:
                self.ui.display_message("No recent notices.", style="yellow")
                return
            self.ui.display_message(
                "\n".join(
                    f"- [{item['channel']}/{item['tone']}] {item.get('display_message', item['message'])}"
                    for item in recent
                ),
                style="cyan",
            )
            return
        if action == "clear":
            removed = center.clear()
            self.ui.display_message(f"Cleared {removed} queued notice(s).", style="green")
            return
        self.ui.display_message("Usage: /notices [list|recent|clear]", style="yellow")

    def _handle_search(self, args: str) -> None:
        query = args.strip()
        if not query:
            self.ui.display_message("Usage: /search <query>", style="yellow")
            return
        results = self.session.services.search_service.search_all(query)
        self.ui.display_message(self._render_search_results(results), style="cyan")

    def _handle_history_search(self, args: str) -> None:
        query = args.strip()
        if not query:
            self.ui.display_message("Usage: /history-search <query>", style="yellow")
            return
        results = self.session.services.search_service.search_sessions(query)
        self.ui.display_message(self._render_search_results(results), style="cyan")

    def _handle_trace_search(self, args: str) -> None:
        query = args.strip()
        if not query:
            self.ui.display_message("Usage: /trace-search <query>", style="yellow")
            return
        results = self.session.services.search_service.search_traces(query)
        self.ui.display_message(self._render_search_results(results), style="cyan")

    def _handle_quickopen(self, args: str) -> None:
        query = args.strip()
        if not query:
            self.ui.display_message("Usage: /quickopen <query>", style="yellow")
            return
        matches = self.session.services.search_service.quickopen(query)
        if not matches:
            self.ui.display_message(f"No quick-open matches for: {query}", style="yellow")
            return
        first = matches[0]
        if first.kind == "session":
            snapshot = self.session.services.session_catalog.snapshot_path(first.source_id)
            self.session.session_store.load_snapshot_path(snapshot)
            self.ui.display_message(f"Quick-open resumed session {first.source_id}", style="green")
            return
        if first.kind == "trace":
            self.ui.display_message(Path(first.path).read_text(encoding="utf-8"), style="cyan")
            return
        self.ui.display_message(self._render_search_results(matches), style="cyan")

    def _handle_thinking(self, args: str) -> None:
        action = (args.strip().lower() or "status")
        if action == "status":
            self.ui.display_message(f"reasoning: {self.session.config.reasoning}", style="cyan")
            return
        if action == "toggle":
            self.session.config.reasoning = not bool(self.session.config.reasoning)
        elif action in {"on", "off"}:
            self.session.config.reasoning = action == "on"
        else:
            self.ui.display_message("Usage: /thinking [on|off|toggle|status]", style="yellow")
            return
        if self.session.config_manager is not None:
            self.session.config_manager.save_config(reasoning=self.session.config.reasoning)
        if hasattr(self.session.model, "config"):
            self.session.model.config.reasoning = self.session.config.reasoning
        self.ui.display_message(f"reasoning: {self.session.config.reasoning}", style="green")

    def _handle_picker(self, args: str) -> None:
        action = (args.strip().lower() or "theme")
        if action == "model":
            choice = self._choose_option("model", self._model_options(), default=self.session.config.model)
            if choice:
                self._handle_model(choice)
            return
        if action == "theme":
            self._handle_theme("pick")
            return
        if action == "output":
            self._handle_output_style("pick")
            return
        if action == "thinking":
            choice = self._choose_option("thinking", ["on", "off"], default="on" if self.session.config.reasoning else "off")
            if choice:
                self._handle_thinking(choice)
            return
        if action == "effort":
            choice = self._choose_option("effort", list(SUPPORTED_EFFORT_LEVELS), default=self.session.services.settings_manager.runtime.effort_level)
            if choice:
                self._handle_effort(choice)
            return
        if action == "fast":
            choice = self._choose_option("fast", ["on", "off"], default="on" if self.session.services.settings_manager.runtime.fast_mode else "off")
            if choice:
                self._handle_fast(choice)
            return
        self.ui.display_message("Usage: /picker [model|theme|output|thinking|effort|fast]", style="yellow")

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

    @staticmethod
    def _default_keybindings_template() -> str:
        return """[prompt]
submit = "enter"
submit_alt = "c-d"
paste = "c-v"
undo = "c-z"
redo = "c-y"
"""

    def _choose_option(self, title: str, options: List[str], default: str = "") -> str:
        if hasattr(self.ui, "select_option"):
            try:
                default_index = options.index(default) if default in options else 0
            except ValueError:
                default_index = 0
            selected = self.ui.select_option(title, options, default_index=default_index)
            return selected or ""
        return ""

    @staticmethod
    def _split_prompt_tail(raw: str) -> tuple[str, str]:
        if " -- " in raw:
            head, prompt = raw.split(" -- ", 1)
            return head.strip(), prompt.strip()
        parts = shlex.split(raw)
        if len(parts) < 2:
            return raw.strip(), ""
        return " ".join(parts[:2]).strip(), " ".join(parts[2:]).strip()

    @staticmethod
    def _tail_after_tokens(raw: str, count: int) -> str:
        parts = shlex.split(raw)
        if len(parts) <= count:
            return ""
        return " ".join(parts[count:]).strip()

    def _model_options(self) -> List[str]:
        options = [
            self.session.config.model,
            "gpt-4.1",
            "gpt-4o",
            "claude-sonnet-4-5",
            "deepseek-chat",
        ]
        seen: set[str] = set()
        result: List[str] = []
        for item in options:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @staticmethod
    def _debug_runs_dir() -> Path:
        return (Path.home() / ".anuris_debug_runs").expanduser().resolve()

    def _debug_trace_sessions(self) -> List[dict]:
        sessions_dir = self._debug_runs_dir() / "sessions"
        if not sessions_dir.exists():
            return []
        items: List[dict] = []
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            session_path = session_dir / "session.json"
            transcript_path = session_dir / "transcript.md"
            events_path = session_dir / "events.jsonl"
            if not session_path.exists():
                continue
            payload = json.loads(session_path.read_text(encoding="utf-8"))
            payload["transcript_path"] = str(transcript_path)
            payload["events_path"] = str(events_path)
            items.append(payload)
        return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)

    @staticmethod
    def _render_search_results(results: List[Any]) -> str:
        if not results:
            return "No results."
        return "\n".join(item.render() if hasattr(item, "render") else str(item) for item in results)

    def _git_summary(self) -> str:
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.session.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.session.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.splitlines()
        except Exception:
            return "not a git repository"
        return f"{branch} ({len(dirty)} change(s))"

    @staticmethod
    def _infer_group(name: str) -> str:
        groups = {
            "help": "Core",
            "clear": "Core",
            "save": "Session",
            "load": "Session",
            "attach": "Context",
            "detach": "Context",
            "files": "Context",
            "add-dir": "Context",
            "agent": "Runtime",
            "compact": "Session",
            "todos": "Automation",
            "tasks": "Automation",
            "skills": "Automation",
            "notices": "Automation",
            "status": "Runtime",
            "model": "Runtime",
            "config": "Runtime",
            "agents": "Automation",
            "permissions": "Runtime",
            "session": "Session",
            "resume": "Session",
            "rewind": "Session",
            "search": "Session",
            "history-search": "Session",
            "trace-search": "Session",
            "quickopen": "Session",
            "mcp": "Tools",
            "plugin": "Tools",
            "reload-plugins": "Tools",
            "worktree": "Tools",
            "branch": "Git",
            "env": "Runtime",
            "output-style": "Runtime",
            "theme": "Runtime",
            "vim": "Runtime",
            "effort": "Runtime",
            "fast": "Runtime",
            "thinking": "Runtime",
            "picker": "Runtime",
            "statusline": "Runtime",
            "keybindings": "Runtime",
            "sandbox-toggle": "Runtime",
            "thinkback": "Diagnostics",
            "thinkback-play": "Diagnostics",
            "cost": "Diagnostics",
            "usage": "Diagnostics",
            "stats": "Diagnostics",
            "doctor": "Diagnostics",
            "diff": "Git",
            "review": "Git",
            "plan": "Git",
            "hooks": "Automation",
            "summary": "Session",
            "context": "Context",
            "memory": "Context",
            "rename": "Session",
            "export": "Session",
            "copy": "Session",
            "commit": "Git",
        }
        return groups.get(name, "General")
