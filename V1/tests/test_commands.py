import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from anuris.config import ConfigManager
from anuris.config import Config
from anuris.debug_server import DebugSessionManager
from anuris.session import ChatSession


class FakeModel:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return {"choices": [{"message": {"content": "unused"}}]}


def tool_call(name, arguments, tool_id="call_1"):
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


class TeamCommandModel:
    def __init__(self):
        self.calls = []

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        system_text = str(messages[0].get("content", "")) if messages else ""
        blob = "\n".join(str(message.get("content", "")) for message in messages if isinstance(message, dict))
        if "You are teammate" not in system_text:
            return {"choices": [{"message": {"content": "unused"}}]}

        if '"type": "shutdown_request"' in blob:
            request_id = "unknown"
            for message in reversed(messages):
                content = str(message.get("content", ""))
                if '"type": "shutdown_request"' not in content:
                    continue
                marker = '"request_id": "'
                if marker in content:
                    request_id = content.split(marker, 1)[1].split('"', 1)[0]
                    break
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                tool_call(
                                    "shutdown_response",
                                    json.dumps({"request_id": request_id, "approve": True, "reason": "done"}),
                                    "shutdown_1",
                                ),
                                tool_call("idle", "{}", "idle_shutdown"),
                            ],
                        }
                    }
                ]
            }

        if "please ack the lead" in blob and "acknowledged by teammate" not in blob:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                tool_call("read_inbox", "{}", "read_1"),
                                tool_call(
                                    "send_message",
                                    json.dumps({"to": "lead", "content": "acknowledged by teammate", "msg_type": "message"}),
                                    "send_1",
                                ),
                                tool_call("idle", "{}", "idle_ack"),
                            ],
                        }
                    }
                ]
            }

        if "Plan submitted" not in blob:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                tool_call("plan_submit", json.dumps({"plan": "1. inspect\n2. report"}), "plan_1"),
                                tool_call(
                                    "send_message",
                                    json.dumps({"to": "lead", "content": "initial report ready", "msg_type": "message"}),
                                    "send_0",
                                ),
                                tool_call("idle", "{}", "idle_0"),
                            ],
                        }
                    }
                ]
            }

        return {"choices": [{"message": {"content": "idle"}}]}


class CommandDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "note.txt").write_text("hello", encoding="utf-8")
        self.session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="cmdtest",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_attach_and_files_commands(self):
        response = self.session.handle_input(f"/attach {self.workspace / 'note.txt'}")
        self.assertIn("Added: note.txt", response.output_text)
        self.assertEqual(len(self.session.attachment_manager.attachments), 1)

        response = self.session.handle_input("/files")
        self.assertIn("note.txt", response.output_text)

    def test_save_and_load_commands_round_trip(self):
        self.session.session_store.add_user_message("hello")
        self.session.session_store.add_assistant_message("world")

        save_response = self.session.handle_input("/save snapshot.json")
        self.assertIn("snapshot.json", save_response.output_text)

        self.session.handle_input("/clear")
        self.assertEqual(len(self.session.session_store.messages), 1)

        load_response = self.session.handle_input("/load snapshot.json")
        self.assertIn("snapshot.json", load_response.output_text)
        self.assertEqual(len(self.session.session_store.messages), 3)

    def test_model_and_agent_commands_update_runtime(self):
        response = self.session.handle_input("/model alt-model")
        self.assertIn("alt-model", response.output_text)
        self.assertEqual(self.session.config.model, "alt-model")

        response = self.session.handle_input("/agent off")
        self.assertIn("disabled", response.output_text)
        self.assertFalse(self.session.agent_mode)

    def test_theme_command_lists_and_switches_themes(self):
        response = self.session.handle_input("/theme")
        self.assertIn("theme: claude", response.output_text)
        self.assertIn("dark", response.output_text)

        response = self.session.handle_input("/theme dark")
        self.assertIn("Theme set to dark", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.theme, "dark")

        response = self.session.handle_input("/theme toggle")
        self.assertIn("Theme switched to claude", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.theme, "claude")

    def test_runtime_control_commands_update_settings(self):
        response = self.session.handle_input("/effort high")
        self.assertIn("Set effort level to high", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.effort_level, "high")

        response = self.session.handle_input("/fast on")
        self.assertIn("Fast mode ON", response.output_text)
        self.assertTrue(self.session.services.settings_manager.runtime.fast_mode)

        response = self.session.handle_input("/statusline format model cwd session")
        self.assertIn("Updated statusline format", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.statusline_format, "model cwd session")

        response = self.session.handle_input("/sandbox-toggle read-only")
        self.assertIn("Sandbox mode set to read-only", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.sandbox_mode, "read-only")

        response = self.session.handle_input('/sandbox-toggle exclude "npm run test"')
        self.assertIn('Added "npm run test"', response.output_text)
        self.assertIn("npm run test", self.session.services.settings_manager.runtime.excluded_commands)

    def test_keybindings_template_command_persists_path(self):
        keybindings_path = self.workspace / "bindings.toml"
        response = self.session.handle_input(f"/keybindings template {keybindings_path}")
        self.assertIn("Keybindings template ready", response.output_text)
        self.assertTrue(keybindings_path.exists())
        self.assertIn("submit", keybindings_path.read_text(encoding="utf-8"))
        self.assertEqual(self.session.services.settings_manager.runtime.keybindings_path, str(keybindings_path.resolve()))

    def test_picker_commands_select_theme_model_output_style_and_session(self):
        self.session.ui.select_option = lambda title, options, default_index=0: "dark" if title == "theme" else options[-1]

        response = self.session.handle_input("/theme pick")
        self.assertIn("Theme set to dark", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.theme, "dark")

        response = self.session.handle_input("/model pick")
        self.assertIn("Updated model", response.output_text)
        self.assertEqual(self.session.config.model, "deepseek-chat")

        self.session.ui.select_option = lambda title, options, default_index=0: "plain"
        response = self.session.handle_input("/output-style pick")
        self.assertIn("Output style set to plain", response.output_text)
        self.assertEqual(self.session.services.settings_manager.runtime.output_style, "plain")

        other = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="pickme",
        )
        other.session_store.add_user_message("picked user")
        other.session_store.add_assistant_message("picked assistant")

        self.session.ui.select_option = lambda title, options, default_index=0: next(item for item in options if item.startswith("pickme "))
        response = self.session.handle_input("/session pick")
        self.assertIn("Resumed session pickme", response.output_text)
        self.assertTrue(any(message.content == "picked assistant" for message in self.session.session_store.messages))

    def test_permissions_and_rewind_commands(self):
        self.session.session_store.add_user_message("first")
        self.session.session_store.add_assistant_message("reply")
        self.session.session_store.add_user_message("second")
        self.session.session_store.add_assistant_message("reply2")

        response = self.session.handle_input("/permissions readonly")
        self.assertIn("readonly", response.output_text)
        self.assertEqual(self.session.services.permission_manager.mode, "readonly")

        response = self.session.handle_input("/rewind 1")
        self.assertIn("Rewound", response.output_text)
        self.assertEqual([message.content for message in self.session.session_store.messages if message.role == "user"], ["first"])

    def test_resume_command_loads_saved_session_snapshot(self):
        other = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="saved123",
        )
        other.session_store.add_user_message("restored user")
        other.session_store.add_assistant_message("restored assistant")

        self.session.handle_input("/clear")
        response = self.session.handle_input("/resume saved123")
        self.assertIn("saved123", response.output_text)
        self.assertTrue(any(message.content == "restored assistant" for message in self.session.session_store.messages))

    def test_mcp_plugin_and_worktree_commands(self):
        docs_path = self.workspace / "docs.txt"
        docs_path.write_text("mcp body", encoding="utf-8")
        plugin_dir = self.workspace / ".anuris" / "plugins" / "demo-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "1.0.0", "description": "demo"}),
            encoding="utf-8",
        )

        response = self.session.handle_input(f"/mcp add-resource docs {docs_path}")
        self.assertIn("Added MCP resource docs", response.output_text)

        response = self.session.handle_input("/mcp list")
        self.assertIn("docs", response.output_text)

        response = self.session.handle_input("/plugin reload")
        self.assertIn("reloaded", response.output_text.lower())

        response = self.session.handle_input("/plugin list")
        self.assertIn("demo-plugin", response.output_text)

        alt_workspace = self.workspace / "alt"
        alt_workspace.mkdir()
        response = self.session.handle_input(f"/worktree enter {alt_workspace}")
        self.assertIn(str(alt_workspace), response.output_text)
        self.assertEqual(self.session.workspace_root, alt_workspace.resolve())

        response = self.session.handle_input("/worktree exit")
        self.assertIn(str(self.workspace), response.output_text)
        self.assertEqual(self.session.workspace_root, self.workspace.resolve())

    def test_files_and_cost_commands_show_context(self):
        self.session.handle_input("Read note", attachment_paths=[str(self.workspace / "note.txt")])
        response = self.session.handle_input("/files")
        self.assertIn("note.txt", response.output_text)

        response = self.session.handle_input("/cost")
        self.assertIn("queries:", response.output_text)

        response = self.session.handle_input("/usage")
        self.assertIn("elapsed_seconds:", response.output_text)

    def test_doctor_stats_add_dir_and_clear_commands(self):
        docs_dir = self.workspace / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("guide", encoding="utf-8")

        response = self.session.handle_input(f"/add-dir {docs_dir}")
        self.assertIn("Added directories:", response.output_text)
        self.assertEqual(len(self.session.services.context_files.list_dirs()), 1)

        response = self.session.handle_input("/stats")
        self.assertIn("added_dirs: 1", response.output_text)

        response = self.session.handle_input("/doctor")
        self.assertIn("Doctor report:", response.output_text)
        self.assertIn("model_configured: OK", response.output_text)

        self.session.handle_input("/memory append keep this")
        response = self.session.handle_input("/clear context")
        self.assertIn("Context files and added directories cleared", response.output_text)
        self.assertEqual(self.session.services.context_files.snapshot()["added_dirs"], 0)

        response = self.session.handle_input("/clear memory")
        self.assertIn("Workspace memory cleared", response.output_text)

    def test_help_and_session_preview_commands(self):
        saved = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="preview1",
        )
        saved.session_store.add_user_message("preview user")
        saved.session_store.add_assistant_message("preview assistant")

        response = self.session.handle_input("/help context")
        self.assertIn("Context", response.output_text)
        self.assertIn("/add-dir", response.output_text)

        response = self.session.handle_input("/session preview preview1")
        self.assertIn("preview assistant", response.output_text)
        self.assertIn("session_id: preview1", response.output_text)

    def test_hooks_command_adds_and_runs_local_hook(self):
        response = self.session.handle_input('/hooks add tool_called "printf hook-fired"')
        self.assertIn("Added hook", response.output_text)

        response = self.session.handle_input("/hooks run tool_called")
        self.assertIn("hook-fired", response.output_text)

        response = self.session.handle_input("/hooks test tool_called")
        self.assertIn("printf hook-fired", response.output_text)

    def test_review_and_plan_commands_use_prompt_execution(self):
        review_session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(
                [
                    {"choices": [{"message": {"content": "Review findings go here."}}]},
                    {"choices": [{"message": {"content": "Implementation plan drafted."}}]},
                ]
            ),
            workspace_root=self.workspace,
            session_id="cmdreview",
        )

        review_response = review_session.handle_input("/review")
        self.assertIn("Review findings go here.", review_response.output_text)

        plan_response = review_session.handle_input("/plan add MCP support")
        self.assertIn("Implementation plan drafted.", plan_response.output_text)

    def test_summary_context_and_memory_commands(self):
        self.session.session_store.add_user_message("context user")
        self.session.session_store.add_assistant_message("context assistant")
        self.session.services.context_files.record(self.workspace / "note.txt")

        response = self.session.handle_input("/summary")
        self.assertIn("Session summary", response.output_text)
        self.assertIn("context assistant", response.output_text)

        response = self.session.handle_input("/context")
        self.assertIn("messages_total:", response.output_text)
        self.assertIn("Files In Context", response.output_text)
        self.assertIn("note.txt", response.output_text)

        response = self.session.handle_input("/memory append remember the build steps")
        self.assertIn("Added memory", response.output_text)

        response = self.session.handle_input("/memory show")
        self.assertIn("remember the build steps", response.output_text)

        response = self.session.handle_input("/memory clear")
        self.assertIn("cleared", response.output_text.lower())

    def test_status_notice_and_search_commands(self):
        searchable = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="searchable",
        )
        searchable.session_store.add_user_message("alpha session hit")
        searchable.session_store.add_assistant_message("search content")

        debug_root = self.workspace / ".debug_runs"
        trace_dir = debug_root / "sessions" / "trace1"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "session.json").write_text(
            json.dumps({"session_id": "trace1", "session_name": "alpha trace", "updated_at": "2026-04-01T00:00:00Z"}),
            encoding="utf-8",
        )
        (trace_dir / "transcript.md").write_text("# Trace\n\nalpha trace body\n", encoding="utf-8")
        (trace_dir / "events.jsonl").write_text("", encoding="utf-8")
        self.session.services.search_service.debug_root = debug_root
        (self.workspace / "alpha-export.txt").write_text("alpha export body", encoding="utf-8")

        center = self.session.services.notification_center
        center.enqueue("rate limit warning", tone="warning", channel="runtime", collapse_key="runtime:rate")
        center.enqueue("rate limit still active", tone="warning", channel="runtime", collapse_key="runtime:rate")

        notices = self.session.handle_input("/notices list")
        self.assertIn("latest: rate limit still active", notices.output_text)

        search_all = self.session.handle_input("/search alpha")
        self.assertIn("[session] searchable", search_all.output_text)
        self.assertIn("[trace] trace1", search_all.output_text)
        self.assertIn("[export] alpha-export", search_all.output_text)
        self.assertIn("[message] searchable", search_all.output_text)

        history = self.session.handle_input("/history-search alpha")
        self.assertIn("[session] searchable", history.output_text)
        self.assertIn("[message] searchable", history.output_text)

        trace = self.session.handle_input("/trace-search alpha")
        self.assertIn("[trace] trace1", trace.output_text)

        quickopen = self.session.handle_input("/quickopen searchable")
        self.assertIn("Quick-open resumed session searchable", quickopen.output_text)

        status = self.session.handle_input("/status")
        self.assertIn("Session", status.output_text)
        self.assertIn("Runtime", status.output_text)
        self.assertIn("Diagnostics", status.output_text)
        self.assertIn("Tools:", status.output_text)

    def test_context_diff_message_tasks_and_diagnostics_commands(self):
        self.session.session_store.add_user_message("hello")
        self.session.session_store.add_assistant_message("```python\nprint(1)\n```", reasoning="first pass")
        self.session.session_store.add_tool_result(
            "write_file",
            "call_1",
            "updated file",
            metadata={
                "path": "note.txt",
                "diff": "--- a/note.txt\n+++ b/note.txt\n@@\n-hello\n+hello world\n",
                "summary": "updated note.txt",
            },
        )

        self.session.services.task_manager.create("prepare patch")
        self.session.services.task_manager.create("review patch")
        self.session.services.task_manager.update(1, status="in_progress", owner="ghost")
        self.session.services.task_manager.update(2, add_blocked_by=[1])
        self.session.team_runtime.team_manager.submit_plan("alice", "1. inspect\n2. report")
        self.session.team_runtime.team_manager.request_shutdown("alice")

        context_viz = self.session.handle_input("/context viz")
        self.assertIn("Context visualization:", context_viz.output_text)
        self.assertIn("conversation:", context_viz.output_text)

        diff_recent = self.session.handle_input("/diff recent")
        self.assertIn("note.txt", diff_recent.output_text)
        self.assertIn("+++ b/note.txt", diff_recent.output_text)

        inspect_message = self.session.handle_input("/message inspect 3")
        self.assertIn("role: assistant", inspect_message.output_text)
        self.assertIn("reasoning:", inspect_message.output_text)

        export_message = self.session.handle_input("/message export 3")
        self.assertIn("Exported message 3", export_message.output_text)
        self.assertTrue((self.workspace / "message-3.txt").exists())

        tasks_board = self.session.handle_input("/tasks board")
        self.assertIn("Task board:", tasks_board.output_text)
        self.assertIn("Resume candidate:", tasks_board.output_text)
        self.assertIn("Governance:", tasks_board.output_text)
        self.assertIn("plans_pending:", tasks_board.output_text)

        diagnostics = self.session.handle_input("/diagnostics warnings")
        self.assertIn("owner missing from roster: ghost", diagnostics.output_text)

    def test_rename_and_export_commands(self):
        self.session.session_store.add_user_message("Refactor the runtime session layer for Claude parity")
        self.session.session_store.add_assistant_message("Starting with command and session metadata changes.")

        response = self.session.handle_input("/rename")
        self.assertIn("Session renamed to:", response.output_text)
        self.assertIn("Refactor the runtime session layer", self.session.session_store.title)

        response = self.session.handle_input("/session list")
        self.assertIn("Refactor the runtime session layer", response.output_text)

        response = self.session.handle_input("/export exported-session")
        self.assertIn("exported-session.txt", response.output_text)
        export_path = self.workspace / "exported-session.txt"
        self.assertTrue(export_path.exists())
        exported = export_path.read_text(encoding="utf-8")
        self.assertIn("Session:", exported)
        self.assertIn("Refactor the runtime session layer", exported)
        self.assertIn("Starting with command and session metadata changes.", exported)

    def test_copy_command_writes_fallback_artifacts(self):
        self.session.session_store.add_assistant_message("Plain answer for copy testing.")
        self.session.session_store.add_assistant_message("```python\nprint('hello')\n```")

        response = self.session.handle_input("/copy message 1")
        self.assertTrue("response.md" in response.output_text)
        response_path = Path(tempfile.gettempdir()) / "anuris" / "response.md"
        self.assertTrue(response_path.exists())
        self.assertIn("print('hello')", response_path.read_text(encoding="utf-8"))

        response = self.session.handle_input("/copy code 1")
        self.assertTrue("copy.py" in response.output_text)
        code_path = Path(tempfile.gettempdir()) / "anuris" / "copy.py"
        self.assertTrue(code_path.exists())
        self.assertEqual(code_path.read_text(encoding="utf-8"), "print('hello')\n")

    def test_commit_command_creates_git_commit(self):
        subprocess.run(["git", "init"], cwd=self.workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.workspace, check=True, capture_output=True, text=True)

        (self.workspace / "note.txt").write_text("updated", encoding="utf-8")
        response = self.session.handle_input("/commit test workspace snapshot")
        self.assertIn("test workspace snapshot", response.output_text)

        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("test workspace snapshot", log)

    def test_runtime_settings_persist_to_config_file(self):
        config_path = self.workspace / "anuris_config.toml"
        manager = ConfigManager(config_file=config_path)
        persistent = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="persistcfg",
            config_manager=manager,
        )

        persistent.handle_input("/theme dark")
        persistent.handle_input("/output-style plain")
        persistent.handle_input("/vim on")
        persistent.handle_input("/effort high")
        persistent.handle_input("/fast on")
        persistent.handle_input("/statusline off")
        persistent.handle_input("/sandbox-toggle read-only")
        persistent.handle_input('/sandbox-toggle exclude "npm run test"')
        persistent.handle_input(f"/keybindings path {self.workspace / 'bindings.toml'}")

        loaded = manager.load_config()
        self.assertEqual(loaded.theme, "dark")
        self.assertEqual(loaded.output_style, "plain")
        self.assertTrue(loaded.vim_mode)
        self.assertEqual(loaded.effort_level, "high")
        self.assertTrue(loaded.fast_mode)
        self.assertFalse(loaded.statusline_enabled)
        self.assertEqual(loaded.sandbox_mode, "read-only")
        self.assertEqual(loaded.excluded_commands, ["npm run test"])
        self.assertIn("bindings.toml", loaded.keybindings_path)

        restored = ChatSession(
            loaded,
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="persistcfg2",
            config_manager=manager,
        )
        self.assertEqual(restored.services.settings_manager.runtime.theme, "dark")
        self.assertEqual(restored.services.settings_manager.runtime.output_style, "plain")
        self.assertTrue(restored.services.settings_manager.runtime.vim_mode)
        self.assertEqual(restored.services.settings_manager.runtime.effort_level, "high")
        self.assertTrue(restored.services.settings_manager.runtime.fast_mode)
        self.assertFalse(restored.services.settings_manager.runtime.statusline_enabled)
        self.assertEqual(restored.services.settings_manager.runtime.sandbox_mode, "read-only")
        self.assertEqual(restored.services.settings_manager.runtime.excluded_commands, ["npm run test"])

    def test_agents_command_drives_team_inbox_and_governance(self):
        session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=TeamCommandModel(),
            workspace_root=self.workspace,
            session_id="teamcmd",
        )

        status = session.handle_input("/agents")
        self.assertIn("Team dashboard:", status.output_text)
        self.assertIn("Team runtime:", status.output_text)
        self.assertIn(".anuris_team", status.output_text)

        spawned = session.handle_input("/agents spawn alice reviewer -- Inspect the repo and report back")
        self.assertIn("Spawned 'alice'", spawned.output_text)

        deadline = time.time() + 2
        lead_snapshot = ""
        while time.time() < deadline:
            lead_snapshot = session.team_runtime.read_inbox("lead")
            if "initial report ready" in lead_snapshot:
                break
            time.sleep(0.05)
        self.assertIn("initial report ready", lead_snapshot)

        plans = session.handle_input("/agents plans")
        self.assertIn("from=alice [pending]", plans.output_text)
        request_id = next(iter(session.team_runtime.team_manager._plan_requests.keys()))

        approved = session.handle_input(f"/agents approve {request_id} looks-good")
        self.assertIn("approved", approved.output_text)

        sent = session.handle_input("/agents send alice please ack the lead")
        self.assertIn("Sent message to alice", sent.output_text)

        deadline = time.time() + 2
        lead_ack = ""
        while time.time() < deadline:
            lead_ack = session.team_runtime.read_inbox("lead")
            if "acknowledged by teammate" in lead_ack:
                break
            time.sleep(0.05)
        self.assertIn("acknowledged by teammate", lead_ack)

        shutdown = session.handle_input("/agents shutdown request alice")
        self.assertIn("Shutdown request", shutdown.output_text)
        shutdown_id = next(iter(session.team_runtime.team_manager._shutdown_requests.keys()))

        deadline = time.time() + 2
        shutdown_status = ""
        while time.time() < deadline:
            shutdown_status = session.team_runtime.shutdown_status(shutdown_id)
            if '"status": "approved"' in shutdown_status:
                break
            time.sleep(0.05)
        self.assertIn('"status": "approved"', shutdown_status)

    def test_agents_ps_and_claim_next_include_task_state(self):
        session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="teamps",
        )
        session.services.task_manager.create("review docs")
        ps = session.handle_input("/agents ps")
        self.assertIn("lead:", ps.output_text)

        claimed = session.handle_input("/agents claim-next alice")
        self.assertIn('"owner": "alice"', claimed.output_text)
        self.assertIn('"status": "in_progress"', claimed.output_text)

    def test_sandbox_excluded_command_blocks_bash_tool(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("bash", '{"command":"npm run test"}')]}}]},
                {"choices": [{"message": {"content": "Observed the local sandbox restriction."}}]},
            ]
        )
        session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=model,
            workspace_root=self.workspace,
            session_id="sandboxcmd",
        )
        session.handle_input('/sandbox-toggle exclude "npm run test"')
        response = session.handle_input("Run the test command.")
        self.assertIn("Observed the local sandbox restriction.", response.final_text)
        tool_messages = [str(message.content) for message in session.session_store.messages if message.role == "tool"]
        self.assertTrue(any("Command blocked by local sandbox exclude rules." in message for message in tool_messages))

    def test_thinkback_commands_list_show_and_replay(self):
        debug_dir = self.workspace / ".debug_runs"
        manager = DebugSessionManager(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            workspace_root=self.workspace,
            debug_dir=debug_dir,
            model_factory=lambda config: FakeModel([]),
        )
        manager.create_session({"session_id": "trace1"})
        manager.submit_message("trace1", {"message": "/theme dark"})

        session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel([]),
            workspace_root=self.workspace,
            session_id="thinkbackcmd",
        )
        with patch("anuris.commands.CommandDispatcher._debug_runs_dir", return_value=debug_dir):
            listed = session.handle_input("/thinkback list")
            self.assertIn("trace1", listed.output_text)

            shown = session.handle_input("/thinkback show trace1")
            self.assertIn("/theme dark", shown.output_text)
            self.assertIn("Theme set to dark", shown.output_text)

            replayed = session.handle_input("/thinkback-play trace1")
            self.assertIn("Replayed trace1", replayed.output_text)
