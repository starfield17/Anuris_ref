import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from anuris.live_tui_controller import LiveRenderController
from anuris.live_tui import LiveTurnState
from anuris.ui import ChatUI


def _fake_session(output_style: str = "rich", theme: str = "claude", statusline_enabled: bool = True, statusline_format: str = "model mode perm sandbox cwd session usage team fast effort vim"):
    settings_manager = SimpleNamespace(
        runtime=SimpleNamespace(
            output_style=output_style,
            theme=theme,
            vim_mode=True,
            statusline_enabled=statusline_enabled,
            statusline_format=statusline_format,
            sandbox_mode="workspace-write",
            fast_mode=False,
            effort_level="auto",
            keybindings_path="",
        ),
        statusline_tokens=lambda: [token for token in statusline_format.split() if token],
    )
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
        team_runtime=SimpleNamespace(summary_counts=lambda: {"members": 2, "lead_inbox": 1, "plans_pending": 1}),
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
        self.assertIn("session", rendered)
        self.assertIn("Refactor Session", rendered)
        self.assertIn("sandbox workspace-write", rendered)
        self.assertIn("team 2", rendered)

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

    def test_statusline_can_be_disabled(self):
        ui = ChatUI()
        buffer = io.StringIO()
        ui.console = Console(file=buffer, force_terminal=False, color_system=None, width=120, record=True)
        ui.bind_session(_fake_session(statusline_enabled=False))
        ui.display_status_line()
        rendered = ui.console.export_text()
        self.assertEqual(rendered.strip(), "")

    def test_live_turn_state_tracks_progress_and_completion(self):
        state = LiveTurnState(prompt="Inspect the sample file.")

        state.apply_event({"type": "request_started", "request_id": "req1"})
        state.apply_event({"type": "assistant_delta", "content": "Draft "})
        state.apply_event({"type": "assistant_delta", "content": "reply"})
        state.apply_event({"type": "tool_called", "tool_name": "read_file", "tool_call_id": "tool1", "round": 1})
        state.apply_event({"type": "tool_result", "tool_name": "read_file", "summary": "read_file completed", "is_error": False})
        state.apply_event({"type": "progress_update", "summary": "Turn budget extended", "stage": "budget_extended", "status": "running"})
        state.apply_event({"type": "heartbeat", "status": "running", "last_activity_at": "2026-04-02T00:00:00Z"})
        state.apply_event({"type": "runtime_notice", "channel": "context", "tone": "info", "display_message": "Context compacted"})
        state.complete_from_response({"final_text": "Done.", "reasoning_text": "Plan first.", "round_count": 2})

        self.assertEqual(state.request_id, "req1")
        self.assertEqual(state.final_text, "Done.")
        self.assertEqual(state.reasoning_text, "Plan first.")
        self.assertEqual(state.round_count, 2)
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.heartbeat_count, 1)
        self.assertFalse(state.active_tools)
        self.assertEqual(state.status_summary(), "Turn budget extended")
        self.assertTrue(any(item.detail == "Context compacted" for item in state.recent_activity))

    def test_live_turn_finish_renders_final_output(self):
        ui = self._build_ui(output_style="plain")

        ui.begin_live_turn("Inspect the sample file.")
        ui.handle_runtime_event({"type": "progress_update", "summary": "Running read_file", "stage": "tool_running", "status": "running"})
        ui.handle_runtime_event({"type": "assistant_delta", "content": "Draft answer"})
        ui.finish_live_turn({"final_text": "Done.", "reasoning_text": "Plan first.", "round_count": 1})

        rendered = ui.console.export_text()
        self.assertIn("Streaming response started", rendered)
        self.assertIn("Running read_file", rendered)
        self.assertIn("Plan first.", rendered)
        self.assertIn("Done.", rendered)

    def test_live_render_controller_throttles_frequent_deltas(self):
        ticks = iter([0.0, 0.0, 0.05, 0.18, 0.20])
        controller = LiveRenderController(time_fn=lambda: next(ticks), min_refresh_interval_sec=0.1)

        controller.start()
        self.assertFalse(controller.should_render("assistant_delta"))
        self.assertFalse(controller.should_render("assistant_delta"))
        self.assertTrue(controller.should_render("assistant_delta"))
        self.assertFalse(controller.flush())

    def test_begin_live_turn_uses_non_transient_live_surface(self):
        ui = self._build_ui(output_style="rich")

        created = {}

        class FakeLive:
            def __init__(self, renderable, console, refresh_per_second, transient):
                created["renderable"] = renderable
                created["refresh_per_second"] = refresh_per_second
                created["transient"] = transient

            def __enter__(self):
                created["entered"] = True
                return self

            def __exit__(self, exc_type, exc, tb):
                created["exited"] = True

            def update(self, renderable, refresh=False):
                created["updated"] = (renderable, refresh)

        with patch("anuris.ui.Live", FakeLive):
            ui.begin_live_turn("Inspect the sample file.")

        self.assertTrue(created.get("entered"))
        self.assertFalse(created["transient"])
