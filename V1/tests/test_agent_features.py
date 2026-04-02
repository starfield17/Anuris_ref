import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from anuris.agent.autonomy import AutonomousTaskController
from anuris.agent.executor import AgentToolExecutor
from anuris.agent.loop import AgentLoopRunner
from anuris.agent.skills import SkillLoader
from anuris.agent.tasks import PersistentTaskManager
from anuris.agent.team import TeamManager
from anuris.agent.background import BackgroundManager
from anuris.agent.todo import TodoManager
from anuris.config import Config
from anuris.engine import QueryEngine, SessionServices, SessionStore
from anuris.runtime import RuntimeState
from anuris.services import (
    ContextFileTracker,
    HookManager,
    MCPManager,
    PermissionManager,
    PluginManager,
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
        if not self.responses:
            raise AssertionError("FakeModel ran out of responses")
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


def tool_names(call):
    schemas = call.get("tools") or []
    return [schema.get("function", {}).get("name", "") for schema in schemas]


def build_services(workspace: Path) -> SessionServices:
    return SessionServices(
        todo_manager=TodoManager(),
        task_manager=PersistentTaskManager(workspace / ".anuris" / "tasks"),
        skill_loader=SkillLoader(workspace),
        permission_manager=PermissionManager(),
        session_catalog=SessionCatalog(workspace),
        worktree_manager=WorktreeManager(workspace),
        plugin_manager=PluginManager(workspace),
        mcp_manager=MCPManager(workspace),
        settings_manager=SettingsManager(),
        hook_manager=HookManager(workspace),
        context_files=ContextFileTracker(workspace),
        usage_tracker=UsageTracker(),
    )


class QueryEngineAgentFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.services = build_services(self.workspace)
        self.tool_registry = ToolRegistry(build_default_tools())
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_task_tool_runs_readonly_subagent_with_restricted_tools(self):
        model = FakeModel(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    tool_call(
                                        "task",
                                        json.dumps(
                                            {
                                                "prompt": "Try writing a marker file with bash.",
                                                "description": "Verify workspace safety",
                                                "readonly": True,
                                            }
                                        ),
                                        "top_task",
                                    )
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    tool_call(
                                        "bash",
                                        json.dumps({"command": "echo blocked > marker.txt"}),
                                        "sub_bash",
                                    )
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Readonly mode blocked the write-like command, so no files changed."
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Subagent finished safely without mutating the workspace."
                            }
                        }
                    ]
                },
            ]
        )
        store = SessionStore("system", self.workspace, "agent_eng1")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Use a subagent to inspect the workspace safely.")

        self.assertEqual(result.final_text, "Subagent finished safely without mutating the workspace.")
        self.assertFalse((self.workspace / "marker.txt").exists())
        self.assertTrue(any("task subagent: Verify workspace safety" in event for event in result.tool_events))
        self.assertEqual(len(model.calls), 4)
        self.assertNotIn("write_file", tool_names(model.calls[1]))
        self.assertNotIn("edit_file", tool_names(model.calls[1]))
        self.assertIn("bash", tool_names(model.calls[1]))
        self.assertTrue(
            any(
                message.role == "tool"
                and "Readonly mode blocked the write-like command" in str(message.content)
                for message in store.messages
            )
        )


class TodoAndTaskManagerTests(unittest.TestCase):
    def test_todo_manager_enforces_single_in_progress_and_limit(self):
        manager = TodoManager()
        rendered = manager.update(
            [
                {"content": "Inspect repo", "status": "in_progress", "activeForm": "Inspecting repo"},
                {"content": "Write report", "status": "pending"},
            ]
        )
        self.assertIn("[>] Inspect repo <- Inspecting repo", rendered)
        self.assertIn("(0/2 completed)", rendered)

        with self.assertRaises(ValueError):
            manager.update([{"content": f"todo {index}", "status": "pending"} for index in range(21)])

        with self.assertRaises(ValueError):
            manager.update(
                [
                    {"content": "One", "status": "in_progress", "activeForm": "Doing one"},
                    {"content": "Two", "status": "in_progress", "activeForm": "Doing two"},
                ]
            )

    def test_persistent_task_manager_clears_dependencies_and_claims_next(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = PersistentTaskManager(Path(temp_dir) / "tasks")
            first_id = json.loads(manager.create("Inspect runtime"))["id"]
            second_id = json.loads(manager.create("Apply follow-up"))["id"]

            updated_first = json.loads(manager.update(first_id, add_blocks=[second_id]))
            blocked_second = json.loads(manager.get(second_id))

            self.assertEqual(updated_first["blocks"], [second_id])
            self.assertEqual(blocked_second["blockedBy"], [first_id])

            claimed_first = manager.claim_next_unblocked("alice")
            self.assertIsNotNone(claimed_first)
            self.assertEqual(claimed_first["id"], first_id)
            self.assertEqual(claimed_first["owner"], "alice")
            self.assertEqual(claimed_first["status"], "in_progress")

            manager.update(first_id, status="completed")
            released_second = json.loads(manager.get(second_id))
            self.assertEqual(released_second["blockedBy"], [])

            claimed_second = manager.claim_next_unblocked("bob")
            self.assertIsNotNone(claimed_second)
            self.assertEqual(claimed_second["id"], second_id)
            self.assertEqual(claimed_second["owner"], "bob")


class SkillLoaderTests(unittest.TestCase):
    def test_skill_loader_prefers_override_and_resolves_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "skills").mkdir()
            (workspace / ".anuris_skills").mkdir()
            (workspace / "skills" / "nb-review.md").write_text(
                "---\n"
                "description: Base review flow\n"
                "aliases: reviewer\n"
                "tags: qa, audit\n"
                "---\n"
                "base body\n",
                encoding="utf-8",
            )
            (workspace / ".anuris_skills" / "nb-review.md").write_text(
                "---\n"
                "description: Override review flow\n"
                "aliases: reviewer\n"
                "tags: qa, audit\n"
                "---\n"
                "override body\n",
                encoding="utf-8",
            )

            loader = SkillLoader(workspace)

            loaded = loader.load("reviewer")
            self.assertIn("<skill name=\"nb-review\">", loaded)
            self.assertIn("override body", loaded)
            self.assertNotIn("base body", loaded)
            self.assertIn(".anuris_skills/nb-review.md", loader.render_catalog())
            self.assertIn("Override review flow", loader.descriptions())
            self.assertIn("Did you mean", loader.load("reviwer"))

    def test_skill_loader_prefetch_respects_path_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "skills").mkdir()
            (workspace / "src").mkdir()
            (workspace / "docs").mkdir()
            (workspace / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
            (workspace / "docs" / "notes.md").write_text("notes", encoding="utf-8")
            (workspace / "skills" / "python-review.md").write_text(
                "---\n"
                "description: Review Python source\n"
                "paths: src/**/*.py\n"
                "---\n"
                "review scoped\n",
                encoding="utf-8",
            )
            loader = SkillLoader(workspace)

            hidden = loader.prefetch("review this code", current_paths=[workspace / "docs" / "notes.md"])
            shown = loader.prefetch("review this code", current_paths=[workspace / "src" / "app.py"])

            self.assertEqual(hidden, [])
            self.assertEqual([item["name"] for item in shown], ["python-review"])


class TeamAndLoopTests(unittest.TestCase):
    def test_team_manager_supports_spawn_messaging_shutdown_and_plan_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            manager = TeamManager(workspace)
            done = threading.Event()

            def runner(name: str, role: str, prompt: str) -> None:
                manager.send_message(name, "lead", f"{role}:{prompt}")
                done.set()

            manager.set_worker_runner(runner)
            spawned = manager.spawn("worker1", "reviewer", "inspect repo")
            self.assertIn("Spawned 'worker1'", spawned)
            self.assertTrue(done.wait(timeout=2))

            deadline = time.time() + 2
            while "idle" not in manager.list_members() and time.time() < deadline:
                time.sleep(0.05)

            lead_inbox = manager.read_inbox("lead")
            self.assertTrue(any(message.get("from") == "worker1" for message in lead_inbox))

            broadcast = manager.broadcast_from_lead("sync up")
            self.assertEqual(broadcast, "Broadcast to 1 teammate(s)")
            teammate_inbox = manager.read_inbox("worker1")
            self.assertTrue(any(message.get("type") == "broadcast" for message in teammate_inbox))

            shutdown_response = manager.request_shutdown("worker1")
            shutdown_id = shutdown_response.split()[2]
            self.assertIn(shutdown_id, manager.list_shutdown_requests())
            self.assertEqual(manager.record_shutdown_response("worker1", shutdown_id, True, "done"), "Shutdown approved")
            self.assertIn('"status": "approved"', manager.check_shutdown(shutdown_id))

            submitted = manager.submit_plan("worker1", "1. inspect\n2. report")
            plan_id = submitted.split("request_id=")[1].rstrip(")")
            self.assertIn(plan_id, manager.list_plan_requests())
            review = manager.review_plan(plan_id, False, "need more detail")
            self.assertIn("rejected", review)
            worker_updates = manager.read_inbox("worker1")
            self.assertTrue(any(message.get("type") == "plan_approval_response" for message in worker_updates))

    def test_background_manager_records_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            state = RuntimeState(
                session_id="bgsess",
                workspace_root=workspace,
                event_path=workspace / "runtime.jsonl",
                tasks_root=workspace / "runtime-tasks",
            )
            manager = BackgroundManager(
                workspace,
                runtime_task_manager=state.tasks,
                runtime_run_manager=state.runs,
                runtime_queue=state.queue,
            )

            started = manager.run("printf 'background-ok'", timeout=5)
            task_id = started.split()[2]
            deadline = time.time() + 2
            while "completed" not in manager.check(task_id) and time.time() < deadline:
                time.sleep(0.05)

            task_record = state.tasks.get(task_id)
            run_record = state.runs.get(task_id)
            self.assertEqual(task_record.run_id, run_record.id)
            self.assertTrue(Path(run_record.output_path).exists())
            self.assertTrue(any(item.event_type == "background_task_finished" for item in state.queue.list()))

    def test_autonomous_task_controller_executes_next_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            task_manager = PersistentTaskManager(workspace / ".anuris" / "tasks")
            created = json.loads(task_manager.create("Inspect runtime", task_type="agent"))
            state = RuntimeState(
                session_id="autosess",
                workspace_root=workspace,
                event_path=workspace / "runtime.jsonl",
                tasks_root=workspace / "runtime-tasks",
            )
            controller = AutonomousTaskController(
                task_manager,
                workspace_root=workspace,
                run_manager=state.runs,
                runtime_task_manager=state.tasks,
                runtime_queue=state.queue,
            )

            controller.run_next("lead", lambda task: f"done {task['subject']}")
            updated = json.loads(task_manager.get(created["id"]))

            self.assertEqual(updated["status"], "completed")
            self.assertTrue(updated["run_id"])
            self.assertTrue(any(item.event_type == "autonomous_task_completed" for item in state.queue.list()))

    def test_agent_loop_hot_swaps_tools_for_todo_updates(self):
        model = FakeModel(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [tool_call("search_tools", '{"query":"todo"}', "search_1")],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [tool_call("activate_tools", '{"names":["TodoWrite"]}', "activate_1")],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    tool_call(
                                        "TodoWrite",
                                        json.dumps(
                                            {
                                                "items": [
                                                    {
                                                        "content": "Inspect agent runtime",
                                                        "status": "in_progress",
                                                        "activeForm": "Inspecting agent runtime",
                                                    }
                                                ]
                                            }
                                        ),
                                        "todo_1",
                                    )
                                ],
                            }
                        }
                    ]
                },
                {"choices": [{"message": {"content": "Todo board updated successfully."}}]},
            ]
        )
        runner = AgentLoopRunner(
            model=model,
            tool_executor=AgentToolExecutor(
                workspace_root=Path.cwd(),
                include_write_edit=False,
                include_todo=True,
                include_task=False,
                include_task_board=False,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=False,
            ),
            include_todo=True,
            include_task=False,
            include_write_edit=False,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=False,
            include_compaction=False,
            hot_swap_tools=True,
        )

        result = runner.run([{"role": "user", "content": "Track this work with todos."}])

        self.assertEqual(result.final_text, "Todo board updated successfully.")
        self.assertTrue(any("search_tools" in event for event in result.tool_events))
        self.assertTrue(any("activate_tools" in event for event in result.tool_events))
        self.assertTrue(any("TodoWrite" in event for event in result.tool_events))
        self.assertNotIn("TodoWrite", tool_names(model.calls[0]))
        self.assertIn("TodoWrite", tool_names(model.calls[2]))

    def test_agent_loop_teammate_tools_block_readonly_writes_but_allow_protocols(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            loop = AgentLoopRunner(
                model=FakeModel([]),
                tool_executor=AgentToolExecutor(
                    workspace_root=workspace,
                    include_write_edit=True,
                    include_todo=False,
                    include_task=False,
                    include_task_board=True,
                    include_skill_loading=False,
                    include_background_tasks=False,
                    include_team_ops=True,
                ),
                include_todo=False,
                include_task=False,
                include_write_edit=True,
                include_task_board=True,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=True,
                include_compaction=False,
                hot_swap_tools=False,
            )
            worker_executor = AgentToolExecutor(
                workspace_root=workspace,
                include_write_edit=True,
                include_todo=False,
                include_task=False,
                include_task_board=False,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=False,
            )

            loop.tool_executor.team_manager.set_worker_runner(lambda name, role, prompt: None)
            loop.tool_executor.team_manager.spawn("alice", "reviewer", "audit the repo")
            deadline = time.time() + 2
            while "idle" not in loop.tool_executor.team_manager.list_members() and time.time() < deadline:
                time.sleep(0.05)

            blocked_write = loop._execute_teammate_tool(
                worker_executor,
                "alice",
                "reviewer",
                "write_file",
                {"path": "note.txt", "content": "hello"},
            )
            self.assertIn("read-only", blocked_write)
            self.assertFalse((workspace / "note.txt").exists())

            readonly_pwd = loop._execute_teammate_tool(
                worker_executor,
                "alice",
                "reviewer",
                "bash",
                {"command": "pwd"},
            )
            self.assertIn(str(workspace), readonly_pwd)

            plan_submit = loop._execute_teammate_tool(
                worker_executor,
                "alice",
                "reviewer",
                "plan_submit",
                {"plan": "Inspect first, then summarize."},
            )
            plan_id = plan_submit.split("request_id=")[1].rstrip(")")
            self.assertIn(plan_id, loop.get_plan_snapshot())

            shutdown_response = loop.tool_executor.team_manager.request_shutdown("alice")
            shutdown_id = shutdown_response.split()[2]
            approval = loop._execute_teammate_tool(
                worker_executor,
                "alice",
                "reviewer",
                "shutdown_response",
                {"request_id": shutdown_id, "approve": True, "reason": "done"},
            )
            self.assertEqual(approval, "Shutdown approved")
            self.assertIn('"status": "approved"', loop.tool_executor.team_manager.check_shutdown(shutdown_id))
