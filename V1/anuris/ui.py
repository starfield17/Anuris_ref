from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True)
class ThemePalette:
    border: str
    accent: str
    accent_soft: str
    muted: str
    success: str
    warning: str
    danger: str
    assistant: str
    reasoning: str
    tool: str


_THEMES: dict[str, ThemePalette] = {
    "default": ThemePalette(
        border="grey37",
        accent="bright_white",
        accent_soft="rgb(212,175,55)",
        muted="grey62",
        success="green3",
        warning="dark_orange3",
        danger="red3",
        assistant="rgb(255,248,235)",
        reasoning="grey82",
        tool="cyan",
    ),
    "claude": ThemePalette(
        border="grey42",
        accent="rgb(255,250,242)",
        accent_soft="rgb(230,179,64)",
        muted="grey66",
        success="green3",
        warning="dark_orange3",
        danger="red3",
        assistant="rgb(255,246,228)",
        reasoning="grey82",
        tool="bright_cyan",
    ),
    "midnight": ThemePalette(
        border="rgb(60,76,110)",
        accent="rgb(225,236,255)",
        accent_soft="rgb(124,156,255)",
        muted="grey70",
        success="green3",
        warning="yellow3",
        danger="red3",
        assistant="rgb(235,243,255)",
        reasoning="grey85",
        tool="rgb(110,200,255)",
    ),
}


class ChatUI:
    """Interactive terminal UI with a Claude Code-inspired presentation layer."""

    def __init__(self):
        self.console = Console(highlight=False)
        self._session_ref: Any = None
        self.session = self._create_prompt_session()

    def bind_session(self, session: Any) -> None:
        self._session_ref = session

    def _create_prompt_session(self) -> PromptSession:
        key_bindings = KeyBindings()
        undo_stack = []
        redo_stack = []

        @key_bindings.add(Keys.Enter, eager=True)
        def _(event):
            event.current_buffer.validate_and_handle()

        @key_bindings.add(Keys.ControlD)
        def _(event):
            if event.current_buffer.text.strip():
                event.current_buffer.validate_and_handle()

        @key_bindings.add(Keys.ControlV)
        def _(event):
            try:
                import pyperclip

                text = pyperclip.paste()
                undo_stack.append(event.current_buffer.text)
                event.current_buffer.insert_text(text)
            except ImportError:
                self.display_message("pyperclip not installed.", style="red")
            except Exception as exc:
                self.display_message(f"Failed to paste: {str(exc)}", style="red")

        @key_bindings.add("c-z", eager=True)
        def _(event):
            if not undo_stack:
                current_text = event.current_buffer.text
                if current_text.strip():
                    undo_stack.append("")
                    redo_stack.append(current_text)
                    event.current_buffer.text = ""
            else:
                current_text = event.current_buffer.text
                last_state = undo_stack.pop()
                redo_stack.append(current_text)
                event.current_buffer.text = last_state

        @key_bindings.add("c-y", eager=True)
        def _(event):
            if redo_stack:
                current_text = event.current_buffer.text
                next_state = redo_stack.pop()
                undo_stack.append(current_text)
                event.current_buffer.text = next_state

        return PromptSession(
            history=FileHistory(os.path.expanduser("~/.chat_history")),
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=key_bindings,
        )

    def _settings(self) -> Any:
        if self._session_ref and hasattr(self._session_ref, "services"):
            return getattr(self._session_ref.services, "settings_manager", None)
        return None

    def _is_plain_output(self) -> bool:
        settings = self._settings()
        if not settings:
            return False
        return getattr(settings.runtime, "output_style", "rich") == "plain"

    def _palette(self) -> ThemePalette:
        settings = self._settings()
        theme_name = getattr(getattr(settings, "runtime", None), "theme", "default") if settings else "default"
        return _THEMES.get(str(theme_name).lower(), _THEMES["default"])

    def _session_title(self) -> str:
        if not self._session_ref:
            return "untitled"
        title = getattr(self._session_ref.session_store, "title", None)
        return title or getattr(self._session_ref, "session_id", "untitled")

    def _render_status_segments(self) -> list[tuple[str, str]]:
        if not self._session_ref:
            return []

        palette = self._palette()
        usage_tracker = getattr(self._session_ref.services, "usage_tracker", None)
        permission_manager = getattr(self._session_ref.services, "permission_manager", None)
        settings = self._settings()
        runtime = getattr(settings, "runtime", None) if settings else None
        cwd = Path(getattr(self._session_ref, "workspace_root", Path.cwd())).name or str(
            getattr(self._session_ref, "workspace_root", Path.cwd())
        )

        segments = [
            (f"model {self._session_ref.config.model}", palette.accent),
            (f"{'agent' if self._session_ref.agent_mode else 'chat'} mode", palette.accent_soft),
            (f"perm {getattr(permission_manager, 'mode', 'default')}", palette.tool),
            (f"cwd {cwd}", palette.muted),
            (f"session {self._session_title()}", palette.assistant),
        ]
        if usage_tracker:
            segments.append((f"q {usage_tracker.query_count} · tools {usage_tracker.tool_call_count}", palette.muted))
        if runtime and getattr(runtime, "vim_mode", False):
            segments.append(("vim", palette.warning))
        return segments

    def display_status_line(self) -> None:
        if self._is_plain_output():
            return
        segments = self._render_status_segments()
        if not segments:
            return
        palette = self._palette()
        line = Text()
        for index, (text, style) in enumerate(segments):
            if index:
                line.append("  •  ", style=palette.border)
            line.append(text, style=style)
        self.console.print(line)

    def display_separator(self) -> None:
        if self._is_plain_output():
            self.console.print("-" * 72)
            return
        self.console.rule(style=self._palette().border, characters="─")

    def display_prompt(self) -> str:
        try:
            self.display_status_line()
            prompt_label = "❯ "
            text = self.session.prompt(prompt_label, multiline=True, wrap_lines=True)
            return text.strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def display_message(self, content: Any, style: str = None, end: str = "\n", flush: bool = False) -> None:
        if flush:
            print(content, end=end, flush=True)
            return
        if self._is_plain_output():
            self.console.print(content, end=end)
            return
        self.console.print(content, style=style, end=end)

    def display_assistant_message(self, content: str) -> None:
        if not content.strip():
            return
        if self._is_plain_output():
            self.display_message(f"Anuris: {content}", style="bold")
            return
        palette = self._palette()
        title = Text("assistant", style=palette.accent_soft)
        body = Text(content, style=palette.assistant)
        subtitle = Text(self._session_title(), style=palette.muted)
        self.console.print(
            Panel.fit(
                body,
                title=title,
                subtitle=subtitle,
                title_align="left",
                subtitle_align="right",
                border_style=palette.border,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def display_reasoning(self, content: str) -> None:
        if not content or not content.strip():
            return
        if self._is_plain_output():
            self.display_message("[thinking]", style="yellow")
            self.display_message(content)
            return
        palette = self._palette()
        self.console.print(
            Panel.fit(
                Text(content, style=palette.reasoning),
                title=Text("thinking", style=palette.accent_soft),
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def display_activity_event(self, label: str, detail: str = "", tone: str = "info") -> None:
        tones = {
            "info": self._palette().muted,
            "success": self._palette().success,
            "warning": self._palette().warning,
            "danger": self._palette().danger,
        }
        style = tones.get(tone, self._palette().muted)
        message = Text()
        message.append("● ", style=style)
        message.append(label, style=style)
        if detail:
            message.append("  ")
            message.append(detail, style=self._palette().muted)
        self.console.print(message)

    def display_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        if not attachments:
            return

        table = Table(box=box.SIMPLE_HEAVY, border_style=self._palette().border, pad_edge=False)
        table.add_column("#", style=self._palette().muted, width=3)
        table.add_column("File", style=self._palette().assistant)
        table.add_column("Type", style=self._palette().tool)
        table.add_column("Size", style=self._palette().accent_soft)

        for attachment in attachments:
            table.add_row(
                str(attachment["index"]),
                attachment["name"],
                attachment["type"],
                attachment["size"],
            )

        self.console.print(Panel(table, title="context attachments", title_align="left", border_style=self._palette().border))

    def display_welcome(self, model: str) -> None:
        if self._is_plain_output():
            self.display_message(f"Anuris ({model})")
            self.display_message("Use /help to inspect commands.")
            return

        palette = self._palette()
        lines = [
            Text("Anuris", style=f"bold {palette.accent}"),
            Text("Claude Code-inspired Python runtime", style=palette.muted),
            Text(""),
            Text("Quick start", style=f"bold {palette.accent_soft}"),
            Text("• /help  /status  /context  /summary  /memory", style=palette.assistant),
            Text("• /review  /plan  /rename  /export  /copy", style=palette.assistant),
            Text("• /permissions  /worktree  /mcp  /plugin", style=palette.assistant),
            Text(""),
            Text("Shortcuts", style=f"bold {palette.accent_soft}"),
            Text("• Enter send  • Ctrl+D send  • Ctrl+V paste  • Ctrl+Z undo  • Ctrl+Y redo", style=palette.muted),
            Text(""),
            Text(f"Model: {model}", style=palette.tool),
            Text(f"Workspace: {getattr(self._session_ref, 'workspace_root', Path.cwd())}", style=palette.muted),
        ]
        content = Text("\n").join(lines)
        self.console.print(
            Panel.fit(
                content,
                title=Text("interactive session", style=palette.accent_soft),
                subtitle=Text("type /help to open the command palette", style=palette.muted),
                title_align="left",
                subtitle_align="right",
                border_style=palette.border,
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
