from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def register_session_ops_commands(dispatcher) -> None:
    dispatcher._register("rename", "Rename the current session.", "/rename [name]", lambda args: _handle_rename(dispatcher, args))
    dispatcher._register("export", "Export the current conversation to a text file.", "/export [filename]", lambda args: _handle_export(dispatcher, args))


def _handle_rename(dispatcher, args: str) -> None:
    requested = args.strip()
    if not requested:
        requested = dispatcher.session.session_store.suggested_title()
    if not requested:
        dispatcher.ui.display_message(
            "Could not generate a session title yet. Usage: /rename <name>",
            style="yellow",
        )
        return
    title = dispatcher.session.session_store.rename(requested)
    dispatcher.ui.display_message(f"Session renamed to: {title}", style="green")


def _handle_export(dispatcher, args: str) -> None:
    requested = args.strip()
    filename = requested or _default_export_filename(dispatcher.session.session_store)
    path = Path(filename).expanduser()
    if path.suffix.lower() != ".txt":
        path = path.with_suffix(".txt")
    if not path.is_absolute():
        path = (dispatcher.session.workspace_root / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dispatcher.session.session_store.export_text(), encoding="utf-8")
    dispatcher.ui.display_message(f"Conversation exported to: {path}", style="green")


def _default_export_filename(session_store) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    first_prompt = session_store.suggested_title()
    if first_prompt:
        sanitized = _sanitize_filename(first_prompt)
        if sanitized:
            return f"{timestamp}-{sanitized}.txt"
    return f"conversation-{timestamp}.txt"


def _sanitize_filename(text: str) -> str:
    sanitized = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    sanitized = re.sub(r"\s+", "-", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized
