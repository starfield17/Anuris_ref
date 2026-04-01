from __future__ import annotations

import shlex


def register_workspace_commands(dispatcher) -> None:
    dispatcher._register(
        "add-dir",
        "Manage added directories in the current working set.",
        "/add-dir [list|clear|remove <path>|<path>...]",
        lambda args: _handle_add_dir(dispatcher, args),
    )


def _handle_add_dir(dispatcher, args: str) -> None:
    parts = shlex.split(args)
    tracker = dispatcher.session.services.context_files
    action = parts[0] if parts else "list"
    if action == "list":
        dispatcher.ui.display_message(tracker.render_dirs(), style="cyan")
        return
    if action == "clear":
        tracker.clear_dirs()
        dispatcher.ui.display_message("Added directories cleared.", style="yellow")
        return
    if action == "remove":
        if len(parts) < 2:
            dispatcher.ui.display_message("Usage: /add-dir remove <path>", style="yellow")
            return
        removed = tracker.remove_dir(parts[1])
        message = "Removed added directory." if removed else "Directory was not in the working set."
        dispatcher.ui.display_message(message, style="yellow" if removed else "red")
        return

    added = []
    for raw_path in parts:
        path = tracker.add_dir(raw_path)
        added.append(str(path))
    if not added:
        dispatcher.ui.display_message("Usage: /add-dir [list|clear|remove <path>|<path>...]", style="yellow")
        return
    dispatcher.ui.display_message("Added directories:\n" + "\n".join(f"- {item}" for item in added), style="green")
