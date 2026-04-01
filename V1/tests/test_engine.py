import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anuris.config import Config
from anuris.engine import PermissionContext, QueryEngine, SessionServices, SessionStore
from anuris.agent.skills import SkillLoader
from anuris.agent.tasks import PersistentTaskManager
from anuris.agent.todo import TodoManager
from anuris.services import (
    ContextBudgetService,
    ContextFileTracker,
    HookManager,
    MCPManager,
    NotificationCenter,
    PermissionManager,
    PluginManager,
    RuntimeWatcher,
    SessionCatalog,
    SettingsManager,
    UsageTracker,
    WorktreeManager,
)
from anuris.tools import ToolRegistry, build_default_tools


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
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
        return self.responses.pop(0)


def tool_call(name, arguments, tool_id="call_1"):
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


class QueryEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        self.services = SessionServices(
            todo_manager=TodoManager(),
            task_manager=PersistentTaskManager(self.workspace / ".anuris" / "tasks"),
            skill_loader=SkillLoader(self.workspace),
            permission_manager=PermissionManager(),
            session_catalog=SessionCatalog(self.workspace),
            worktree_manager=WorktreeManager(self.workspace),
            plugin_manager=PluginManager(self.workspace),
            mcp_manager=MCPManager(self.workspace),
            settings_manager=SettingsManager(),
            hook_manager=HookManager(self.workspace),
            context_files=ContextFileTracker(self.workspace),
            usage_tracker=UsageTracker(),
            notification_center=NotificationCenter(),
            runtime_watcher=RuntimeWatcher(PersistentTaskManager(self.workspace / ".anuris" / "tasks")),
        )
        self.tool_registry = ToolRegistry(build_default_tools())
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_query_engine_executes_tool_then_returns_answer(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}')]}}]},
                {"choices": [{"message": {"content": "The file contains alpha and beta."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng1")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Inspect the sample file.")
        self.assertEqual(result.final_text, "The file contains alpha and beta.")
        self.assertTrue(any("read_file" in item for item in result.tool_events))
        self.assertTrue((self.workspace / ".anuris" / "sessions" / "eng1" / "transcript.md").exists())

    def test_query_engine_feeds_tool_errors_back_to_model(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"bad.txt","content":"x"}')]}}]},
                {"choices": [{"message": {"content": "Write was rejected, switching to analysis only."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng2")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit(
            "Attempt a write in readonly mode.",
            permission_context=PermissionContext(mode="readonly", allowed_tools={"read_file"}),
            allowed_tool_names={"read_file"},
        )
        self.assertIn("analysis only", result.final_text)
        self.assertTrue(any(message.role == "tool" and "Error:" in str(message.content) for message in store.messages))

    def test_session_store_compaction_creates_boundary(self):
        store = SessionStore("system", self.workspace, "eng3")
        for index in range(12):
            store.add_user_message(f"user {index}")
            store.add_assistant_message(f"assistant {index}")
        summary = store.compact_history("keep only the current direction", keep_last=4)
        self.assertIn("Conversation compacted", summary)
        self.assertEqual(store.messages[1].kind, "compact_boundary")

    def test_query_engine_supports_mcp_resource_tools(self):
        mcp_file = self.workspace / "resource.txt"
        mcp_file.write_text("resource body", encoding="utf-8")
        self.services.mcp_manager.add_resource("docs", str(mcp_file))

        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("list_mcp_resources", '{}', "call_list")]}}]},
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_mcp_resource", '{"name":"docs"}', "call_read")]}}]},
                {"choices": [{"message": {"content": "Read the local MCP resource."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng4")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Inspect the MCP catalog.")
        self.assertEqual(result.final_text, "Read the local MCP resource.")
        self.assertTrue(any(message.role == "tool" and "docs" in str(message.content) for message in store.messages))

    def test_query_engine_tool_search_finds_matching_tools(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("tool_search", '{"query":"mcp"}')]}}]},
                {"choices": [{"message": {"content": "Use the MCP tools."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng5")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Find the MCP-related tools.")
        self.assertEqual(result.final_text, "Use the MCP tools.")
        self.assertTrue(any(message.role == "tool" and "list_mcp_resources" in str(message.content) for message in store.messages))

    def test_query_engine_injects_runtime_notices_and_prefetched_skills(self):
        skills_dir = self.workspace / "skills"
        skills_dir.mkdir()
        (skills_dir / "pytest.md").write_text("---\ndescription: Run Python tests safely\n---\nUse pytest.", encoding="utf-8")
        self.services.skill_loader.refresh()
        self.services.notification_center.enqueue("Task #1 completed", kind="task_completed")

        model = FakeModel([{"choices": [{"message": {"content": "Use pytest if needed."}}]}])
        store = SessionStore("system", self.workspace, "eng6")
        events = []
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
        )

        result = engine.submit("Please run pytest on the repo.")
        self.assertEqual(result.final_text, "Use pytest if needed.")
        first_call = model.calls[0]
        messages = first_call["messages"]
        self.assertTrue(any("Queued runtime notices" in str(message.get("content", "")) for message in messages))
        self.assertTrue(any("Relevant available skills" in str(message.get("content", "")) for message in messages))
        self.assertTrue(any(event.get("type") == "skill_prefetch" for event in events))

    def test_query_engine_returns_structured_permission_denial_for_blocked_tool(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"blocked.txt","content":"x"}')]}}]},
                {"choices": [{"message": {"content": "Falling back after the permission rejection."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng7")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit(
            "Try writing a file.",
            permission_context=PermissionContext(mode="readonly", allowed_tools={"write_file"}),
            allowed_tool_names={"write_file"},
        )
        self.assertIn("permission rejection", result.final_text.lower())
        tool_messages = [message for message in store.messages if message.role == "tool"]
        self.assertEqual(len(tool_messages), 1)
        denial = tool_messages[0].metadata.get("permission_denial", {})
        self.assertEqual(denial.get("reason_code"), "readonly_requires_write")
        self.assertEqual(denial.get("tool_name"), "write_file")

    def test_query_engine_auto_compacts_using_context_budget(self):
        model = FakeModel([{"choices": [{"message": {"content": "Done after compaction."}}]}])
        store = SessionStore("system", self.workspace, "eng8")
        for index in range(5):
            store.add_user_message(f"user {index} " + ("alpha " * 120))
            store.add_assistant_message(f"assistant {index} " + ("beta " * 120))

        session_like = SimpleNamespace(
            session_store=store,
            workspace_root=self.workspace,
            services=self.services,
        )
        self.services.context_budget = ContextBudgetService(session_like, soft_limit=1000, hard_limit=1400)
        events = []
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
            auto_compact_chars=999999,
        )

        result = engine.submit("Short follow-up.")
        self.assertEqual(result.final_text, "Done after compaction.")
        self.assertEqual(store.messages[1].kind, "compact_boundary")
        compact_event = next(event for event in events if event.get("type") == "compact_boundary")
        self.assertTrue(compact_event.get("budget", {}).get("should_compact"))
        self.assertTrue(compact_event.get("compact_reason"))

    def test_notification_center_drain_for_model_prioritizes_and_preserves_noninjectable_items(self):
        center = NotificationCenter()
        center.enqueue("low", priority=20)
        center.enqueue("high", priority=90)
        center.enqueue("ui only", priority=80, metadata={"inject_to_model": False})

        drained = center.drain_for_model(limit=1)
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["message"], "high")
        preview = center.preview()
        self.assertIn("ui only", preview)
        self.assertIn("low", preview)
