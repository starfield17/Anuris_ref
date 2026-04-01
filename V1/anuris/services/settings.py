from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeSettings:
    output_style: str = "rich"
    theme: str = "claude"
    vim_mode: bool = False


class SettingsManager:
    """In-memory runtime settings for the interactive session."""

    def __init__(self):
        self.runtime = RuntimeSettings()

    def set_output_style(self, style: str) -> str:
        normalized = style.strip().lower()
        if normalized not in {"plain", "rich"}:
            raise ValueError("output style must be 'plain' or 'rich'")
        self.runtime.output_style = normalized
        return normalized

    def set_theme(self, theme: str) -> str:
        normalized = theme.strip().lower()
        if not normalized:
            raise ValueError("theme is required")
        self.runtime.theme = normalized
        return normalized

    def set_vim_mode(self, enabled: bool) -> bool:
        self.runtime.vim_mode = bool(enabled)
        return self.runtime.vim_mode

    def render(self) -> str:
        return "\n".join(
            [
                f"output_style: {self.runtime.output_style}",
                f"theme: {self.runtime.theme}",
                f"vim_mode: {self.runtime.vim_mode}",
            ]
        )
