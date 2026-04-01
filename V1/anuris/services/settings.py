from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

SUPPORTED_THEMES = ("claude", "dark", "midnight", "default")
SUPPORTED_EFFORT_LEVELS = ("auto", "low", "medium", "high", "max")
SUPPORTED_SANDBOX_MODES = ("workspace-write", "read-only", "off")
DEFAULT_STATUSLINE_FORMAT = "model mode perm sandbox cwd session usage team fast effort vim"


@dataclass
class RuntimeSettings:
    output_style: str = "rich"
    theme: str = "claude"
    vim_mode: bool = False
    effort_level: str = "auto"
    fast_mode: bool = False
    statusline_enabled: bool = True
    statusline_format: str = DEFAULT_STATUSLINE_FORMAT
    sandbox_mode: str = "workspace-write"
    excluded_commands: list[str] = None
    keybindings_path: str = ""

    def __post_init__(self) -> None:
        if self.excluded_commands is None:
            self.excluded_commands = []


class SettingsManager:
    """In-memory runtime settings for the interactive session."""

    def __init__(self, runtime: Optional[RuntimeSettings] = None, config_ref: Any = None, config_manager: Any = None):
        self.runtime = runtime or RuntimeSettings()
        self.config_ref = config_ref
        self.config_manager = config_manager

    @classmethod
    def from_config(cls, config: Any, config_manager: Any = None) -> "SettingsManager":
        output_style = str(getattr(config, "output_style", "rich") or "rich").lower()
        if output_style not in {"plain", "rich"}:
            output_style = "rich"
        theme = str(getattr(config, "theme", "claude") or "claude").lower()
        if theme not in SUPPORTED_THEMES:
            theme = "claude"
        effort_level = str(getattr(config, "effort_level", "") or "auto").lower()
        if effort_level not in SUPPORTED_EFFORT_LEVELS:
            effort_level = "auto"
        sandbox_mode = str(getattr(config, "sandbox_mode", "workspace-write") or "workspace-write").lower()
        if sandbox_mode not in SUPPORTED_SANDBOX_MODES:
            sandbox_mode = "workspace-write"
        statusline_format = str(getattr(config, "statusline_format", DEFAULT_STATUSLINE_FORMAT) or DEFAULT_STATUSLINE_FORMAT).strip()
        runtime = RuntimeSettings(
            output_style=output_style,
            theme=theme,
            vim_mode=bool(getattr(config, "vim_mode", False)),
            effort_level=effort_level,
            fast_mode=bool(getattr(config, "fast_mode", False)),
            statusline_enabled=bool(getattr(config, "statusline_enabled", True)),
            statusline_format=statusline_format,
            sandbox_mode=sandbox_mode,
            excluded_commands=list(getattr(config, "excluded_commands", []) or []),
            keybindings_path=str(getattr(config, "keybindings_path", "") or ""),
        )
        return cls(runtime=runtime, config_ref=config, config_manager=config_manager)

    def set_output_style(self, style: str) -> str:
        normalized = style.strip().lower()
        if normalized not in {"plain", "rich"}:
            raise ValueError("output style must be 'plain' or 'rich'")
        self.runtime.output_style = normalized
        self._persist(output_style=normalized)
        return normalized

    def set_theme(self, theme: str) -> str:
        normalized = theme.strip().lower()
        if not normalized:
            raise ValueError("theme is required")
        if normalized not in SUPPORTED_THEMES:
            raise ValueError(f"theme must be one of: {', '.join(SUPPORTED_THEMES)}")
        self.runtime.theme = normalized
        self._persist(theme=normalized)
        return normalized

    def toggle_theme(self) -> str:
        self.runtime.theme = "dark" if self.runtime.theme == "claude" else "claude"
        self._persist(theme=self.runtime.theme)
        return self.runtime.theme

    def available_themes(self) -> tuple[str, ...]:
        return SUPPORTED_THEMES

    def set_vim_mode(self, enabled: bool) -> bool:
        self.runtime.vim_mode = bool(enabled)
        self._persist(vim_mode=self.runtime.vim_mode)
        return self.runtime.vim_mode

    def set_effort_level(self, level: str) -> str:
        normalized = level.strip().lower() or "auto"
        if normalized == "unset":
            normalized = "auto"
        if normalized not in SUPPORTED_EFFORT_LEVELS:
            raise ValueError(f"effort level must be one of: {', '.join(SUPPORTED_EFFORT_LEVELS)}")
        self.runtime.effort_level = normalized
        self._persist(effort_level="" if normalized == "auto" else normalized)
        return normalized

    def set_fast_mode(self, enabled: bool) -> bool:
        self.runtime.fast_mode = bool(enabled)
        self._persist(fast_mode=self.runtime.fast_mode)
        return self.runtime.fast_mode

    def toggle_fast_mode(self) -> bool:
        return self.set_fast_mode(not self.runtime.fast_mode)

    def set_statusline_enabled(self, enabled: bool) -> bool:
        self.runtime.statusline_enabled = bool(enabled)
        self._persist(statusline_enabled=self.runtime.statusline_enabled)
        return self.runtime.statusline_enabled

    def set_statusline_format(self, value: str) -> str:
        normalized = " ".join(part for part in value.replace(",", " ").split() if part).strip()
        if not normalized:
            normalized = DEFAULT_STATUSLINE_FORMAT
        self.runtime.statusline_format = normalized
        self._persist(statusline_format=normalized)
        return normalized

    def statusline_tokens(self) -> list[str]:
        raw = self.runtime.statusline_format or DEFAULT_STATUSLINE_FORMAT
        return [token for token in raw.replace(",", " ").split() if token]

    def set_sandbox_mode(self, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in SUPPORTED_SANDBOX_MODES:
            raise ValueError(f"sandbox mode must be one of: {', '.join(SUPPORTED_SANDBOX_MODES)}")
        self.runtime.sandbox_mode = normalized
        self._persist(sandbox_mode=normalized)
        return normalized

    def add_excluded_command(self, pattern: str) -> list[str]:
        normalized = pattern.strip()
        if not normalized:
            raise ValueError("excluded command pattern is required")
        if normalized not in self.runtime.excluded_commands:
            self.runtime.excluded_commands.append(normalized)
        self._persist(excluded_commands=list(self.runtime.excluded_commands))
        return list(self.runtime.excluded_commands)

    def remove_excluded_command(self, pattern: str) -> list[str]:
        normalized = pattern.strip()
        self.runtime.excluded_commands = [item for item in self.runtime.excluded_commands if item != normalized]
        self._persist(excluded_commands=list(self.runtime.excluded_commands))
        return list(self.runtime.excluded_commands)

    def set_keybindings_path(self, path: str) -> str:
        normalized = path.strip()
        self.runtime.keybindings_path = normalized
        self._persist(keybindings_path=normalized)
        return normalized

    def render(self) -> str:
        return "\n".join(
            [
                f"output_style: {self.runtime.output_style}",
                f"theme: {self.runtime.theme}",
                f"vim_mode: {self.runtime.vim_mode}",
                f"effort_level: {self.runtime.effort_level}",
                f"fast_mode: {self.runtime.fast_mode}",
                f"statusline_enabled: {self.runtime.statusline_enabled}",
                f"statusline_format: {self.runtime.statusline_format}",
                f"sandbox_mode: {self.runtime.sandbox_mode}",
                f"excluded_commands: {self.runtime.excluded_commands}",
                f"keybindings_path: {self.runtime.keybindings_path or '(default)'}",
            ]
        )

    def _persist(self, **kwargs: Any) -> None:
        if self.config_ref is not None:
            for key, value in kwargs.items():
                if hasattr(self.config_ref, key):
                    setattr(self.config_ref, key, value)
        if self.config_manager is not None:
            self.config_manager.save_config(**kwargs)
