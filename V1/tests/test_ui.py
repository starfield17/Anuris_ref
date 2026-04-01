import io
import unittest
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from anuris.ui import ChatUI


def _fake_session(output_style: str = "rich", theme: str = "claude"):
    settings_manager = SimpleNamespace(runtime=SimpleNamespace(output_style=output_style, theme=theme, vim_mode=True))
    return SimpleNamespace(
        config=SimpleNamespace(model="demo-model"),
        agent_mode=True,
        workspace_root=Path("/tmp/demo-workspace"),
        session_id="sess1",
        session_store=SimpleNamespace(title="Refactor Session"),
        services=SimpleNamespace(
            settings_manager=settings_manager,
            permission_manager=SimpleNamespace(mode="acceptEdits"),
            usage_tracker=SimpleNamespace(query_count=2, tool_call_count=3),
        ),
    )


class ChatUITests(unittest.TestCase):
    def _build_ui(self, output_style: str = "rich", theme: str = "claude"):
        ui = ChatUI()
        buffer = io.StringIO()
        ui.console = Console(file=buffer, force_terminal=False, color_system=None, width=120, record=True)
        ui.bind_session(_fake_session(output_style=output_style, theme=theme))
        return ui

    def test_welcome_and_statusline_render(self):
        ui = self._build_ui()

        ui.display_welcome("demo-model")
        ui.display_status_line()

        rendered = ui.console.export_text()
        self.assertIn("Claude Code-inspired Python runtime", rendered)
        self.assertIn("model demo-model", rendered)
        self.assertIn("session Refactor Session", rendered)

    def test_message_cards_render_in_rich_and_plain_modes(self):
        rich_ui = self._build_ui(output_style="rich")
        rich_ui.display_assistant_message("Implemented the UI refresh.")
        rich_ui.display_reasoning("Compare the palette and activity rendering.")
        rich_ui.display_activity_event("tool", "read_file", tone="warning")
        rich_rendered = rich_ui.console.export_text()
        self.assertIn("Implemented the UI refresh.", rich_rendered)
        self.assertIn("Compare the palette", rich_rendered)
        self.assertIn("read_file", rich_rendered)

        plain_ui = self._build_ui(output_style="plain")
        plain_ui.display_welcome("demo-model")
        plain_ui.display_assistant_message("Plain assistant output.")
        plain_rendered = plain_ui.console.export_text()
        self.assertIn("Anuris (demo-model)", plain_rendered)
        self.assertIn("Plain assistant output.", plain_rendered)

    def test_dark_theme_renders_same_ui_surface(self):
        ui = self._build_ui(theme="dark")
        ui.display_welcome("demo-model")
        ui.display_status_line()
        ui.display_assistant_message("Dark theme assistant output.")
        rendered = ui.console.export_text()
        self.assertIn("interactive session", rendered)
        self.assertIn("model demo-model", rendered)
        self.assertIn("Dark theme assistant output.", rendered)
