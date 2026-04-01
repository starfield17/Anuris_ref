from __future__ import annotations

import shlex


def register_inspection_commands(dispatcher) -> None:
    dispatcher._register("summary", "Show a compact summary of the current session.", "/summary", lambda args: _handle_summary(dispatcher, args))
    dispatcher._register("context", "Show current context usage and file coverage.", "/context [viz|top|recent]", lambda args: _handle_context(dispatcher, args))
    dispatcher._register("memory", "Show or edit the local workspace memory file.", "/memory [show|append|clear]", lambda args: _handle_memory(dispatcher, args))


def _handle_summary(dispatcher, args: str) -> None:
    del args
    dispatcher.ui.display_message(dispatcher.session.session_store.summary_report(), style="cyan")


def _handle_context(dispatcher, args: str) -> None:
    parts = shlex.split(args)
    action = parts[0].lower() if parts else "report"
    visualizer = dispatcher.session.services.context_visualizer
    if action in {"viz", "show"}:
        dispatcher.ui.display_message(visualizer.render(), style="cyan")
        return
    if action == "top":
        dispatcher.ui.display_message(visualizer.render_top(), style="cyan")
        return
    if action == "recent":
        dispatcher.ui.display_message(visualizer.render_recent(), style="cyan")
        return
    parts = [
        "## Context",
        "",
        dispatcher.session.session_store.context_report(),
        "",
        "## Files In Context",
        "",
        dispatcher.session.services.context_files.render(),
        "",
        "## Usage",
        "",
        dispatcher.session.services.usage_tracker.render(),
    ]
    dispatcher.ui.display_message("\n".join(parts), style="cyan")


def _handle_memory(dispatcher, args: str) -> None:
    parts = shlex.split(args)
    action = parts[0] if parts else "show"
    manager = dispatcher.session.services.memory_manager
    if action == "show":
        dispatcher.ui.display_message(manager.read(), style="cyan")
        return
    if action == "append":
        if len(parts) < 2:
            dispatcher.ui.display_message("Usage: /memory append <text>", style="yellow")
            return
        added = manager.append(" ".join(parts[1:]))
        dispatcher.ui.display_message(f"Added memory: {added}", style="green")
        return
    if action == "clear":
        manager.clear()
        dispatcher.ui.display_message("Workspace memory cleared.", style="yellow")
        return
    dispatcher.ui.display_message("Usage: /memory [show|append|clear]", style="yellow")
