from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from ..engine.messages import extract_text_content


def register_session_ops_commands(dispatcher) -> None:
    dispatcher._register("rename", "Rename the current session.", "/rename [name]", lambda args: _handle_rename(dispatcher, args))
    dispatcher._register("export", "Export the current conversation to a text file.", "/export [filename]", lambda args: _handle_export(dispatcher, args))
    dispatcher._register("copy", "Copy the latest assistant response or code block.", "/copy [full|code [index]|message [index]]", lambda args: _handle_copy(dispatcher, args))
    dispatcher._register("message", "Inspect, copy, or export a message by absolute index.", "/message [inspect|copy|export] <index>", lambda args: _handle_message(dispatcher, args))


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


def _handle_copy(dispatcher, args: str) -> None:
    parts = args.split()
    action = parts[0].lower() if parts else "message"

    if action == "full":
        content = dispatcher.session.session_store.export_text()
        message = _copy_with_fallback(content, "conversation.txt")
        dispatcher.ui.display_message(message, style="green")
        return

    if action in {"message", "code"}:
        index = _parse_index(parts[1] if len(parts) > 1 else "1")
        if index is None:
            dispatcher.ui.display_message("Copy index must be a positive integer.", style="yellow")
            return
        texts = _collect_recent_assistant_texts(dispatcher.session.session_store.messages)
        if len(texts) < index:
            dispatcher.ui.display_message("No assistant response available for that index.", style="yellow")
            return
        selected = texts[index - 1]
        if action == "message":
            message = _copy_with_fallback(selected, "response.md")
            dispatcher.ui.display_message(message, style="green")
            return
        code_blocks = _extract_code_blocks(selected)
        if not code_blocks:
            dispatcher.ui.display_message("No fenced code blocks found in the selected assistant response.", style="yellow")
            return
        block_text, extension = code_blocks[0]
        filename = f"copy{extension}"
        message = _copy_with_fallback(block_text, filename)
        dispatcher.ui.display_message(message, style="green")
        return

    dispatcher.ui.display_message("Usage: /copy [full|code [index]|message [index]]", style="yellow")


def _handle_message(dispatcher, args: str) -> None:
    parts = args.split()
    if len(parts) < 2:
        dispatcher.ui.display_message("Usage: /message [inspect|copy|export] <index>", style="yellow")
        return
    action = parts[0].lower()
    index = _parse_index(parts[1])
    if index is None:
        dispatcher.ui.display_message("Message index must be a positive integer.", style="yellow")
        return
    try:
        message = dispatcher.session.session_store.message_at(index)
    except IndexError:
        dispatcher.ui.display_message("Message index out of range.", style="yellow")
        return

    text = extract_text_content(message.content).strip() or "(empty)"
    if action == "inspect":
        lines = [
            f"index: {index}",
            f"role: {message.role}",
            f"kind: {message.kind}",
            f"name: {message.name or ''}",
            f"tool_call_id: {message.tool_call_id or ''}",
            "content:",
            text,
        ]
        if message.reasoning:
            lines.extend(["", "reasoning:", message.reasoning])
        dispatcher.ui.display_message("\n".join(lines), style="cyan")
        return
    if action == "copy":
        dispatcher.ui.display_message(_copy_with_fallback(text, f"message-{index}.md"), style="green")
        return
    if action == "export":
        filename = f"message-{index}.txt"
        path = (dispatcher.session.workspace_root / filename).resolve()
        path.write_text(text + "\n", encoding="utf-8")
        dispatcher.ui.display_message(f"Exported message {index} to {path}", style="green")
        return
    dispatcher.ui.display_message("Usage: /message [inspect|copy|export] <index>", style="yellow")


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


def _collect_recent_assistant_texts(messages) -> list[str]:
    texts: list[str] = []
    for message in reversed(messages):
        if message.role != "assistant" or message.kind != "message":
            continue
        text = extract_text_content(message.content).strip()
        if text:
            texts.append(text)
    return texts


def _extract_code_blocks(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", markdown, flags=re.DOTALL):
        language = re.sub(r"[^a-zA-Z0-9]", "", match.group(1).strip())
        extension = _language_extension(language)
        blocks.append((match.group(2).rstrip() + "\n", extension))
    return blocks


def _parse_index(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _copy_with_fallback(text: str, filename: str) -> str:
    target_dir = Path(tempfile.gettempdir()) / "anuris"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(text, encoding="utf-8")

    clipboard_ok = _set_clipboard(text)
    char_count = len(text)
    line_count = text.count("\n") + (0 if not text else 1)
    if clipboard_ok:
        return f"Copied to clipboard ({char_count} characters, {line_count} lines)\nAlso written to {path}"
    return f"Clipboard unavailable; wrote {char_count} characters ({line_count} lines) to {path}"


def _set_clipboard(text: str) -> bool:
    commands = [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                input=text,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            continue
        if completed.returncode == 0:
            return True
    return False


def _language_extension(language: str) -> str:
    if not language or language == "plaintext":
        return ".txt"
    mapping = {
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "tsx": ".tsx",
        "jsx": ".jsx",
        "shell": ".sh",
        "bash": ".sh",
        "sh": ".sh",
        "json": ".json",
        "yaml": ".yml",
        "yml": ".yml",
    }
    return mapping.get(language.lower(), f".{language.lower()}")
