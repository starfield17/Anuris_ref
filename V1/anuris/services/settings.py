from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

SUPPORTED_THEMES = ("claude", "dark", "midnight", "default")


@dataclass
class RuntimeSettings:
    output_style: str = "rich"
    theme: str = "claude"
    vim_mode: bool = False


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
        runtime = RuntimeSettings(
            output_style=output_style,
            theme=theme,
            vim_mode=bool(getattr(config, "vim_mode", False)),
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

    def render(self) -> str:
        return "\n".join(
            [
                f"output_style: {self.runtime.output_style}",
                f"theme: {self.runtime.theme}",
                f"vim_mode: {self.runtime.vim_mode}",
            ]
        )

    def _persist(self, **kwargs: Any) -> None:
        if self.config_ref is not None:
            for key, value in kwargs.items():
                if hasattr(self.config_ref, key):
                    setattr(self.config_ref, key, value)
        if self.config_manager is not None:
            self.config_manager.save_config(**kwargs)
