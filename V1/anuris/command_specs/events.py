from __future__ import annotations

import shlex


def register_event_commands(dispatcher) -> None:
    dispatcher._register("hooks", "Manage local hook commands for runtime events.", "/hooks [list|add|remove|run]", lambda args: _handle_hooks(dispatcher, args))


def _handle_hooks(dispatcher, args: str) -> None:
    parts = shlex.split(args)
    action = parts[0] if parts else "list"
    manager = dispatcher.session.services.hook_manager
    if action == "list":
        dispatcher.ui.display_message(manager.render(), style="cyan")
        return
    if action == "add":
        if len(parts) < 3:
            dispatcher.ui.display_message("Usage: /hooks add <event> <command>", style="yellow")
            return
        entry = manager.add(parts[1], " ".join(parts[2:]))
        dispatcher.ui.display_message(f"Added hook for {entry['event']}", style="green")
        return
    if action == "remove":
        if len(parts) < 2:
            dispatcher.ui.display_message("Usage: /hooks remove <index>", style="yellow")
            return
        entry = manager.remove(int(parts[1]))
        dispatcher.ui.display_message(f"Removed hook for {entry['event']}", style="green")
        return
    if action == "run":
        if len(parts) < 2:
            dispatcher.ui.display_message("Usage: /hooks run <event>", style="yellow")
            return
        results = manager.run(parts[1], {"manual": True})
        if not results:
            dispatcher.ui.display_message("No hooks matched.", style="yellow")
            return
        rendered = []
        for item in results:
            rendered.append(f"- {item['command']} -> {item['returncode']}")
            if item["stdout"]:
                rendered.append(f"  stdout: {item['stdout']}")
            if item["stderr"]:
                rendered.append(f"  stderr: {item['stderr']}")
        dispatcher.ui.display_message("\n".join(rendered), style="cyan")
        return
    dispatcher.ui.display_message("Usage: /hooks [list|add|remove|run]", style="yellow")
