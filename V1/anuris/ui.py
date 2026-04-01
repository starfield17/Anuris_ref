from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import toml
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
    "dark": ThemePalette(
        border="rgb(84,84,84)",
        accent="rgb(232,232,232)",
        accent_soft="rgb(125,183,255)",
        muted="grey70",
        success="green3",
        warning="yellow3",
        danger="red3",
        assistant="rgb(220,229,255)",
        reasoning="grey84",
        tool="rgb(116,214,255)",
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
        self.rebuild_prompt_session()

    def rebuild_prompt_session(self) -> None:
        self.session = self._create_prompt_session()

    def _create_prompt_session(self) -> PromptSession:
        key_bindings = KeyBindings()
        undo_stack = []
        redo_stack = []
        binding_map = self._load_keybindings_map()

        @key_bindings.add(binding_map["submit"], eager=True)
        def _(event):
            event.current_buffer.validate_and_handle()

        @key_bindings.add(binding_map["submit_alt"])
        def _(event):
            if event.current_buffer.text.strip():
                event.current_buffer.validate_and_handle()

        @key_bindings.add(binding_map["paste"])
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

        @key_bindings.add(binding_map["undo"], eager=True)
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

        @key_bindings.add(binding_map["redo"], eager=True)
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

    def _load_keybindings_map(self) -> dict[str, str]:
        defaults = {
            "submit": "enter",
            "submit_alt": "c-d",
            "paste": "c-v",
            "undo": "c-z",
            "redo": "c-y",
        }
        settings = self._settings()
        runtime = getattr(settings, "runtime", None) if settings else None
        path = str(getattr(runtime, "keybindings_path", "") or "").strip()
        if not path:
            return defaults
        resolved = Path(path).expanduser()
        if not resolved.exists():
            return defaults
        try:
            if resolved.suffix.lower() == ".json":
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            else:
                payload = toml.loads(resolved.read_text(encoding="utf-8"))
        except Exception:
            return defaults
        prompt_config = payload.get("prompt", {}) if isinstance(payload, dict) else {}
        if not isinstance(prompt_config, dict):
            return defaults
        merged = dict(defaults)
        for key in defaults:
            value = prompt_config.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip().lower()
        return merged

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
        if runtime and not getattr(runtime, "statusline_enabled", True):
            return []

        tokens = getattr(settings, "statusline_tokens", lambda: ["model", "mode", "perm", "cwd", "session", "usage", "vim"])()
        team_runtime = getattr(self._session_ref, "team_runtime", None)
        team_summary = team_runtime.summary_counts() if team_runtime and hasattr(team_runtime, "summary_counts") else {}

        token_map = {
            "model": (f"model {self._session_ref.config.model}", palette.accent),
            "mode": (f"{'agent' if self._session_ref.agent_mode else 'chat'} mode", palette.accent_soft),
            "perm": (f"perm {getattr(permission_manager, 'mode', 'default')}", palette.tool),
            "sandbox": (f"sandbox {getattr(runtime, 'sandbox_mode', 'workspace-write')}", palette.tool),
            "cwd": (f"cwd {cwd}", palette.muted),
            "session": (f"session {self._session_title()}", palette.assistant),
            "usage": (
                f"q {getattr(usage_tracker, 'query_count', 0)} · tools {getattr(usage_tracker, 'tool_call_count', 0)}",
                palette.muted,
            ),
            "team": (
                f"team {team_summary.get('members', 0)} · inbox {team_summary.get('lead_inbox', 0)} · plans {team_summary.get('plans_pending', 0)}",
                palette.assistant,
            ),
            "fast": (f"fast {'on' if getattr(runtime, 'fast_mode', False) else 'off'}", palette.warning),
            "effort": (f"effort {getattr(runtime, 'effort_level', 'auto')}", palette.accent_soft),
            "vim": ("vim", palette.warning),
        }

        segments: list[tuple[str, str]] = []
        for token in tokens:
            if token == "vim" and not (runtime and getattr(runtime, "vim_mode", False)):
                continue
            segment = token_map.get(token)
            if segment:
                segments.append(segment)
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

    def select_option(self, title: str, options: List[str], default_index: int = 0) -> Optional[str]:
        if not options:
            return None
        if self._is_plain_output():
            return None
        palette = self._palette()
        self.console.print(
            Panel.fit(
                "\n".join(f"{index + 1}. {option}" for index, option in enumerate(options)),
                title=Text(title, style=palette.accent_soft),
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )
        default_choice = str(default_index + 1 if 0 <= default_index < len(options) else 1)
        try:
            raw = self.session.prompt("Select option number (blank to cancel): ", default="")
        except (EOFError, KeyboardInterrupt):
            return None
        value = raw.strip() or default_choice
        if not raw.strip():
            return None
        try:
            selected_index = int(value) - 1
        except ValueError:
            return None
        if 0 <= selected_index < len(options):
            return options[selected_index]
        return None

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

    def display_notices(self, notices: List[Dict[str, Any]]) -> None:
        if not notices:
            return
        if self._is_plain_output():
            for notice in notices:
                label = f"[{notice.get('channel', 'runtime')}/{notice.get('tone', 'info')}]"
                self.display_message(f"{label} {notice.get('display_message', notice.get('message', ''))}")
            return

        palette = self._palette()
        table = Table(box=box.SIMPLE_HEAVY, border_style=palette.border, pad_edge=False)
        table.add_column("Channel", style=palette.accent_soft, width=14)
        table.add_column("Tone", style=palette.tool, width=10)
        table.add_column("Notice", style=palette.assistant)
        table.add_column("Count", style=palette.muted, width=6, justify="right")
        for notice in notices:
            table.add_row(
                str(notice.get("channel", "runtime")),
                str(notice.get("tone", "info")),
                str(notice.get("display_message", notice.get("message", ""))),
                str(notice.get("count", 1)),
            )
        self.console.print(
            Panel(
                table,
                title="status notices",
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )

    def display_session_preview(self, messages: List[Dict[str, Any]], title: str = "session preview") -> None:
        if self._is_plain_output():
            for item in messages:
                self.display_message(f"{item.get('label', 'message')}: {item.get('preview', '')}")
            return
        palette = self._palette()
        lines = []
        for item in messages:
            lines.append(f"{item.get('label', 'message')}: {item.get('preview', '')}")
        self.console.print(
            Panel.fit(
                "\n".join(lines) or "(empty)",
                title=title,
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )

    def display_runtime_dashboard(self, sections: List[Dict[str, Any]], title: str = "runtime dashboard") -> None:
        if self._is_plain_output():
            for section in sections:
                self.display_message(str(section.get("title", "section")))
                for line in section.get("lines", []):
                    self.display_message(f"- {line}")
                self.display_message("")
            return
        palette = self._palette()
        table = Table(box=box.SIMPLE_HEAVY, border_style=palette.border, pad_edge=False)
        table.add_column("Section", style=palette.accent_soft, width=16)
        table.add_column("Details", style=palette.assistant)
        for section in sections:
            lines = section.get("lines", [])
            table.add_row(str(section.get("title", "section")), "\n".join(str(line) for line in lines) or "(empty)")
        self.console.print(
            Panel(
                table,
                title=title,
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )

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
            Text("• /help  /status  /doctor  /usage  /stats", style=palette.assistant),
            Text("• /context  /summary  /memory  /add-dir  /files", style=palette.assistant),
            Text("• /review  /plan  /commit  /rename  /export  /copy", style=palette.assistant),
            Text("• /permissions  /worktree  /mcp  /plugin  /session", style=palette.assistant),
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
