import tempfile
import unittest
from pathlib import Path

from anuris.agent.skills import SkillLoader
from anuris.agent.tasks import PersistentTaskManager
from anuris.agent.todo import TodoManager
from anuris.config import Config
from anuris.engine import QueryEngine, SessionServices, SessionStore
from anuris.services import (
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
from anuris.session import ChatSession
from anuris.tools import ToolRegistry, build_default_tools


class FakeModel:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
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
        if self.responses:
            return self.responses.pop(0)
        return {"choices": [{"message": {"content": "unused"}}]}


class DirectorySkillLoaderTests(unittest.TestCase):
    def test_directory_skill_loads_resources_and_yaml_frontmatter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            skill_dir = workspace / "skills" / "taskmaster"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "assets").mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: taskmaster\n"
                "description: >\n"
                "  Plan long tasks and keep progress visible.\n"
                "aliases:\n"
                "  - planner\n"
                "tags:\n"
                "  - plan\n"
                "  - workflow\n"
                "---\n"
                "Use the task protocol.\n",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "todo_csv.py").write_text("print('todo')\n", encoding="utf-8")
            (skill_dir / "assets" / "SPEC_TEMPLATE.md").write_text("# spec\n", encoding="utf-8")

            loader = SkillLoader(workspace)

            loaded = loader.load("planner")
            self.assertIn("<skill name=\"taskmaster\">", loaded)
            self.assertIn("Use the task protocol.", loaded)
            self.assertIn("skills/taskmaster/SKILL.md", loaded)
            self.assertIn("skills/taskmaster/scripts/todo_csv.py", loaded)
            self.assertIn("skills/taskmaster/assets/SPEC_TEMPLATE.md", loaded)
            self.assertIn("kind=package", loader.render_catalog())
            self.assertIn("Plan long tasks and keep progress visible.", loader.descriptions())

    def test_directory_skill_overrides_flat_skill_with_same_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "skills").mkdir()
            (workspace / ".anuris_skills" / "review").mkdir(parents=True)
            (workspace / "skills" / "review.md").write_text(
                "---\n"
                "description: Flat skill\n"
                "---\n"
                "flat body\n",
                encoding="utf-8",
            )
            (workspace / ".anuris_skills" / "review" / "SKILL.md").write_text(
                "---\n"
                "description: Package skill\n"
                "aliases: reviewer\n"
                "---\n"
                "package body\n",
                encoding="utf-8",
            )

            loader = SkillLoader(workspace)

            self.assertIn("package body", loader.load("reviewer"))
            self.assertNotIn("flat body", loader.load("review"))
            self.assertIn(".anuris_skills/review/SKILL.md", loader.render_catalog())


class PackageSkillIntegrationTests(unittest.TestCase):
    def test_query_engine_prefetches_directory_skill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "skills" / "taskmaster").mkdir(parents=True)
            (workspace / "skills" / "taskmaster" / "SKILL.md").write_text(
                "---\n"
                "description: Make plans and track long-task progress.\n"
                "---\n"
                "Use taskmaster.\n",
                encoding="utf-8",
            )
            services = self._build_services(workspace)
            model = FakeModel([{"choices": [{"message": {"content": "Use taskmaster if needed."}}]}])
            store = SessionStore("system", workspace, "skillpkg1")
            engine = QueryEngine(
                model=model,
                session_store=store,
                tool_registry=ToolRegistry(build_default_tools()),
                services=services,
                workspace_root=workspace,
                config=Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            )

            result = engine.submit("Please make a plan for this repo.")

            self.assertEqual(result.final_text, "Use taskmaster if needed.")
            injected = [message.get("content", "") for message in model.calls[0]["messages"]]
            self.assertTrue(any("Relevant available skills" in str(item) for item in injected))
            self.assertTrue(any("taskmaster" in str(item) for item in injected))

    def test_skills_command_lists_directory_skills(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "skills" / "todo-list-csv").mkdir(parents=True)
            (workspace / "skills" / "todo-list-csv" / "SKILL.md").write_text(
                "---\n"
                "description: Sync TODO CSV state with plans.\n"
                "---\n"
                "Use todo list csv.\n",
                encoding="utf-8",
            )
            session = ChatSession(
                Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
                model=FakeModel(),
                workspace_root=workspace,
                session_id="skillpkg2",
            )

            response = session.handle_input("/skills")

            self.assertIn("todo-list-csv", response.output_text)
            self.assertIn("skills/todo-list-csv/SKILL.md", response.output_text)

    def _build_services(self, workspace: Path) -> SessionServices:
        task_manager = PersistentTaskManager(workspace / ".anuris" / "tasks")
        return SessionServices(
            todo_manager=TodoManager(),
            task_manager=task_manager,
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
            notification_center=NotificationCenter(),
            runtime_watcher=RuntimeWatcher(task_manager),
        )


if __name__ == "__main__":
    unittest.main()
