from __future__ import annotations

import os
import shutil
import subprocess
import sys


def register_diagnostic_commands(dispatcher) -> None:
    dispatcher._register("usage", "Show local session usage and elapsed time.", "/usage", lambda args: _handle_usage(dispatcher, args))
    dispatcher._register("stats", "Show local runtime, session, and repository stats.", "/stats", lambda args: _handle_stats(dispatcher, args))
    dispatcher._register("doctor", "Run local environment and workspace health checks.", "/doctor", lambda args: _handle_doctor(dispatcher, args))


def _handle_usage(dispatcher, args: str) -> None:
    del args
    dispatcher.ui.display_message(dispatcher.session.services.usage_tracker.render(), style="cyan")


def _handle_stats(dispatcher, args: str) -> None:
    del args
    session = dispatcher.session
    usage = session.services.usage_tracker.snapshot()
    context = session.services.context_files.snapshot()
    task_summary = session.services.task_manager.summary_counts()
    team_summary = session.team_runtime.summary_counts() if hasattr(session, "team_runtime") else {}
    lines = [
        "Runtime stats:",
        f"- session_id: {session.session_id}",
        f"- title: {session.session_store.title or '(untitled)'}",
        f"- messages: {len(session.session_store.messages)}",
        f"- queries: {usage['queries']}",
        f"- tool_calls: {usage['tool_calls']}",
        f"- elapsed_seconds: {usage['elapsed_seconds']}",
        f"- pending_attachments: {len(session.attachment_manager.attachments)}",
        f"- context_files: {context['files']}",
        f"- added_dirs: {context['added_dirs']}",
        f"- tasks: {task_summary['total']} (pending={task_summary['pending']}, in_progress={task_summary['in_progress']}, completed={task_summary['completed']}, blocked={task_summary['blocked']})",
        f"- todos: {len(session.services.todo_manager.items)}",
        f"- skills: {len(session.services.skill_loader.skills)}",
        f"- plugins: {len(session.services.plugin_manager.plugins)}",
        f"- mcp_resources: {len(session.services.mcp_manager.list_resources())}",
        f"- saved_sessions: {len(session.services.session_catalog.list_sessions())}",
        f"- runtime_notices: {session.services.notification_center.count() if session.services.notification_center else 0}",
        f"- team_members: {team_summary.get('members', 0)}",
        f"- team_inbox: {team_summary.get('lead_inbox', 0)}",
        f"- team_plans_pending: {team_summary.get('plans_pending', 0)}",
    ]
    dispatcher.ui.display_message("\n".join(lines), style="cyan")


def _handle_doctor(dispatcher, args: str) -> None:
    del args
    session = dispatcher.session
    lines = ["Doctor report:"]

    _append_check(lines, "python", True, sys.version.split()[0])
    _append_check(lines, "workspace_exists", session.workspace_root.exists(), str(session.workspace_root))
    _append_check(lines, "workspace_writable", os.access(session.workspace_root, os.W_OK), str(session.workspace_root))
    _append_check(lines, "git_installed", shutil.which("git") is not None, shutil.which("git") or "missing")
    _append_check(lines, "api_key_configured", bool(session.config.api_key), "configured" if session.config.api_key else "missing")
    _append_check(lines, "model_configured", bool(session.config.model), session.config.model or "missing")
    _append_check(lines, "base_url_configured", bool(session.config.base_url), session.config.base_url or "missing")
    _append_check(lines, "theme", True, session.services.settings_manager.runtime.theme)
    _append_check(lines, "output_style", True, session.services.settings_manager.runtime.output_style)
    _append_check(lines, "permission_mode", True, session.services.permission_manager.mode)
    _append_check(lines, "plugins", True, str(len(session.services.plugin_manager.plugins)))
    _append_check(lines, "mcp_resources", True, str(len(session.services.mcp_manager.list_resources())))
    _append_check(lines, "context_memory", True, session.services.memory_manager.memory_path.as_posix())

    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=session.workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=session.workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        _append_check(lines, "git_repository", True, branch or "(detached)")
        _append_check(lines, "git_dirty", True, f"{len(status.splitlines())} change(s)")
    except Exception as exc:
        _append_check(lines, "git_repository", False, str(exc))

    dispatcher.ui.display_message("\n".join(lines), style="cyan")


def _append_check(lines: list[str], label: str, ok: bool, detail: str) -> None:
    marker = "OK" if ok else "WARN"
    lines.append(f"- {label}: {marker} ({detail})")
