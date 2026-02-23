import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from anuris.agent.loop import AgentLoopRunner
from anuris.agent.tools import AgentToolExecutor


class FakeCompletions:
    def __init__(self, responses):
        self._responses = responses
        self.calls = 0
        self.request_payloads = []

    def create(self, **kwargs):
        if self.calls >= len(self._responses):
            raise AssertionError("No fake response left")
        self.request_payloads.append(kwargs)
        response = self._responses[self.calls]
        self.calls += 1
        return response


class FakeModel:
    def __init__(self, responses):
        self.config = SimpleNamespace(model="fake-model", temperature=0.3)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        return self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
        )


def make_response(content, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call(tool_id, name, arguments):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=tool_id, function=function)


class FakeBackgroundManager:
    def __init__(self, notifications=None):
        self.notifications = list(notifications or [])

    def run(self, command, timeout=300):
        return "started"

    def check(self, task_id=None):
        return "No background tasks."

    def drain_notifications(self):
        items = list(self.notifications)
        self.notifications.clear()
        return items


class AgentLoopRunnerTests(unittest.TestCase):
    def test_returns_direct_content_without_tools(self):
        model = FakeModel([make_response("final answer", tool_calls=None)])
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4)

        result = runner.run([
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ])

        self.assertEqual(result.final_text, "final answer")
        self.assertEqual(result.rounds, 1)
        self.assertEqual(result.tool_events, [])
        first_payload_messages = model.client.chat.completions.request_payloads[0]["messages"]
        self.assertEqual(first_payload_messages[0]["role"], "system")
        self.assertIn("You are a coding agent", first_payload_messages[0]["content"])

    def test_executes_tool_calls_then_returns_final_text(self):
        tool_calls = [
            make_tool_call(
                "call_1",
                "write_file",
                '{"path":"out.txt","content":"hello"}',
            )
        ]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="done", tool_calls=None),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            model = FakeModel(responses)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace),
                max_rounds=4,
            )

            result = runner.run([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "create file"},
            ])

            self.assertEqual(result.final_text, "done")
            self.assertEqual(result.rounds, 2)
            self.assertEqual((workspace / "out.txt").read_text(), "hello")
            self.assertEqual(len(result.tool_events), 1)
            self.assertIn("write_file", result.tool_events[0])

    def test_includes_reasoning_content_in_assistant_messages_when_required(self):
        tool_calls = [make_tool_call("call_1", "read_file", '{"path":"missing.txt"}')]
        first_message = SimpleNamespace(content="", tool_calls=tool_calls, reasoning_content="thinking")
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=first_message)]),
            make_response(content="done", tool_calls=None),
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(
            model=model,
            tool_executor=AgentToolExecutor(),
            max_rounds=4,
            require_reasoning_content=True,
        )

        runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "previous assistant"},
                {"role": "user", "content": "go"},
            ]
        )

        # second request should contain assistant entries with explicit reasoning_content
        second_request_messages = model.client.chat.completions.request_payloads[1]["messages"]
        assistant_messages = [message for message in second_request_messages if message.get("role") == "assistant"]
        self.assertTrue(assistant_messages)
        self.assertTrue(all("reasoning_content" in message for message in assistant_messages))

    def test_handles_task_tool_call_via_subagent_callback(self):
        tool_calls = [make_tool_call("call_1", "task", '{"prompt":"inspect","agent_type":"Explore"}')]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="parent done", tool_calls=None),
        ]
        model = FakeModel(responses)
        executor = AgentToolExecutor(
            include_task=True,
            subagent_runner=lambda prompt, agent_type: f"subagent:{agent_type}:{prompt}",
        )
        runner = AgentLoopRunner(
            model=model,
            tool_executor=executor,
            max_rounds=4,
            include_task=True,
        )

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "delegate"},
            ]
        )

        self.assertEqual(result.final_text, "parent done")
        self.assertTrue(any(event.startswith("task -> subagent:Explore:inspect") for event in result.tool_events))

    def test_progress_callback_receives_round_and_tool_events(self):
        tool_calls = [make_tool_call("call_1", "read_file", '{"path":"missing.txt"}')]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="done", tool_calls=None),
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4)
        events = []

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "go"},
            ],
            progress_callback=events.append,
        )

        self.assertEqual(result.final_text, "done")
        self.assertTrue(any(event.startswith("[agent] round 1") for event in events))
        self.assertTrue(any(event.startswith("[tool] read_file ->") for event in events))

    def test_executes_persistent_task_tool_calls(self):
        tool_calls = [
            make_tool_call(
                "call_1",
                "task_create",
                '{"subject":"Investigate regression","description":"Find root cause"}',
            )
        ]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="tracked", tool_calls=None),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            model = FakeModel(responses)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace, include_task_board=True),
                max_rounds=4,
                include_task_board=True,
            )

            result = runner.run(
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "track this task"},
                ]
            )

            self.assertEqual(result.final_text, "tracked")
            task_file = workspace / ".anuris_tasks" / "task_1.json"
            self.assertTrue(task_file.exists())
            self.assertIn("task_create", result.tool_events[0])

    def test_executes_load_skill_tool_calls(self):
        tool_calls = [make_tool_call("call_1", "load_skill", '{"name":"python"}')]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="used skill", tool_calls=None),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            skills_dir = workspace / ".anuris_skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "python.md").write_text(
                "---\n"
                "description: Python coding conventions\n"
                "---\n"
                "Prefer readable code over clever tricks.",
                encoding="utf-8",
            )
            model = FakeModel(responses)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace, include_skill_loading=True),
                max_rounds=4,
                include_skill_loading=True,
            )

            result = runner.run(
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "need coding style"},
                ]
            )

            self.assertEqual(result.final_text, "used skill")
            self.assertTrue(any(event.startswith("load_skill -> <skill name=\"python\">") for event in result.tool_events))

    def test_uses_configured_workspace_root_for_skill_loading(self):
        tool_calls = [make_tool_call("call_1", "load_skill", '{"name":"python"}')]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="used skill", tool_calls=None),
        ]

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as other_dir:
            workspace = Path(workspace_dir)
            skills_dir = workspace / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "python.md").write_text(
                "---\n"
                "description: Python coding conventions\n"
                "---\n"
                "Prefer readable code over clever tricks.",
                encoding="utf-8",
            )

            previous_cwd = Path.cwd()
            os.chdir(other_dir)
            try:
                model = FakeModel(responses)
                runner = AgentLoopRunner(
                    model=model,
                    workspace_root=workspace,
                    include_skill_loading=True,
                    max_rounds=4,
                )
                result = runner.run(
                    [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "need coding style"},
                    ]
                )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result.final_text, "used skill")
            self.assertTrue(any(event.startswith("load_skill -> <skill name=\"python\">") for event in result.tool_events))

    def test_injects_background_notifications_before_round(self):
        responses = [make_response(content="done", tool_calls=None)]
        model = FakeModel(responses)
        notifications = [
            {
                "task_id": "abc12345",
                "status": "completed",
                "result": "lint clean",
                "command": "ruff check .",
            }
        ]
        executor = AgentToolExecutor(
            include_background_tasks=True,
            background_manager=FakeBackgroundManager(notifications),
        )
        runner = AgentLoopRunner(
            model=model,
            tool_executor=executor,
            include_background_tasks=True,
            max_rounds=3,
        )

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "continue"},
            ]
        )

        self.assertEqual(result.final_text, "done")
        first_payload_messages = model.client.chat.completions.request_payloads[0]["messages"]
        background_messages = [
            message
            for message in first_payload_messages
            if message.get("role") == "user" and "<background-results>" in str(message.get("content", ""))
        ]
        self.assertTrue(background_messages)

    def test_readonly_teammate_blocks_write_and_unsafe_bash(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            model = FakeModel([make_response("done", tool_calls=None)])
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(
                    workspace_root=workspace,
                    include_task_board=False,
                    include_skill_loading=False,
                    include_background_tasks=False,
                    include_team_ops=True,
                ),
                include_task_board=False,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=True,
                max_rounds=2,
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

            blocked_write = runner._execute_teammate_tool(
                worker_executor=worker_executor,
                teammate="alice",
                role="reviewer",
                tool_name="write_file",
                args={"path": "x.txt", "content": "hello"},
            )
            self.assertIn("read-only", blocked_write)

            blocked_bash = runner._execute_teammate_tool(
                worker_executor=worker_executor,
                teammate="alice",
                role="reviewer",
                tool_name="bash",
                args={"command": "echo hi > out.txt"},
            )
            self.assertIn("read-only", blocked_bash)

            allowed_bash = runner._execute_teammate_tool(
                worker_executor=worker_executor,
                teammate="alice",
                role="reviewer",
                tool_name="bash",
                args={"command": "ls"},
            )
            self.assertNotIn("Error:", allowed_bash)

    def test_teammate_budget_stop_sends_message_to_lead(self):
        tool_calls = [make_tool_call("call_1", "read_file", '{"path":"missing.txt"}')]
        responses = [make_response(content="", tool_calls=tool_calls)]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            executor = AgentToolExecutor(
                workspace_root=workspace,
                include_task_board=False,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=True,
            )
            runner = AgentLoopRunner(
                model=FakeModel(responses),
                tool_executor=executor,
                include_task_board=False,
                include_skill_loading=False,
                include_background_tasks=False,
                include_team_ops=True,
                teammate_max_rounds=1,
                teammate_max_tool_calls=5,
                teammate_max_runtime_sec=60,
            )

            runner._run_teammate_worker(name="alice", role="coder", prompt="inspect workspace")

            inbox_messages = executor.team_manager.read_inbox("lead")
            self.assertTrue(inbox_messages)
            combined = "\n".join(str(item.get("content", "")) for item in inbox_messages)
            self.assertIn("auto-stop", combined)
            self.assertIn("round budget exceeded", combined)

    def test_teammate_budget_reason_checks_runtime_and_limits(self):
        model = FakeModel([make_response("done", tool_calls=None)])
        runner = AgentLoopRunner(
            model=model,
            tool_executor=AgentToolExecutor(include_team_ops=False),
            include_team_ops=False,
            teammate_max_rounds=2,
            teammate_max_tool_calls=3,
            teammate_max_runtime_sec=10,
        )

        runtime_reason = runner._teammate_budget_reason(
            started_at=time.monotonic() - 11,
            total_rounds=0,
            total_tool_calls=0,
        )
        self.assertIn("runtime exceeded", runtime_reason)

        round_reason = runner._teammate_budget_reason(
            started_at=time.monotonic(),
            total_rounds=2,
            total_tool_calls=0,
        )
        self.assertIn("round budget exceeded", round_reason)

        call_reason = runner._teammate_budget_reason(
            started_at=time.monotonic(),
            total_rounds=1,
            total_tool_calls=3,
        )
        self.assertIn("tool-call budget exceeded", call_reason)

    def test_supports_anthropic_content_response_shape(self):
        responses = [
            {"content": [{"type": "text", "text": "hello from anthropic"}]},
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=2)

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ]
        )

        self.assertEqual(result.final_text, "hello from anthropic")
        self.assertEqual(result.rounds, 1)

    def test_supports_anthropic_tool_use_blocks(self):
        responses = [
            {
                "content": [
                    {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "missing.txt"}}
                ]
            },
            {"content": [{"type": "text", "text": "done"}]},
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=3)

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "check file"},
            ]
        )

        self.assertEqual(result.final_text, "done")
        self.assertTrue(any(event.startswith("read_file ->") for event in result.tool_events))

    def test_hot_swap_disables_tools_for_plain_chat(self):
        model = FakeModel([make_response("hi there", tool_calls=None)])
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4, hot_swap_tools=True)

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ]
        )

        self.assertEqual(result.final_text, "hi there")
        payload = model.client.chat.completions.request_payloads[0]
        names = [item["function"]["name"] for item in payload.get("tools") or []]
        self.assertIn("search_tools", names)
        self.assertNotIn("write_file", names)
        self.assertEqual(payload.get("tool_choice"), "auto")

    def test_hot_swap_disables_tools_for_test_ping_chat(self):
        model = FakeModel([make_response("yes", tool_calls=None)])
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4, hot_swap_tools=True)

        result = runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "test,are you working?"},
            ]
        )

        self.assertEqual(result.final_text, "yes")
        payload = model.client.chat.completions.request_payloads[0]
        names = [item["function"]["name"] for item in payload.get("tools") or []]
        self.assertIn("search_tools", names)
        self.assertNotIn("write_file", names)
        self.assertEqual(payload.get("tool_choice"), "auto")

    def test_hot_swap_can_activate_write_tools_via_meta_tools(self):
        tool_calls_round_1 = [
            make_tool_call("call_1", "search_tools", '{"query":"write file","limit":5}'),
            make_tool_call(
                "call_2",
                "activate_tools",
                '{"names":["read_file","write_file","edit_file"],"mode":"add"}',
            ),
        ]
        tool_calls_round_2 = [
            make_tool_call("call_3", "write_file", '{"path":"a.txt","content":"hello"}'),
        ]
        responses = [
            make_response("", tool_calls=tool_calls_round_1),
            make_response("", tool_calls=tool_calls_round_2),
            make_response("done", tool_calls=None),
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4, hot_swap_tools=True)

        result = runner.run(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "please update file"}]
        )

        self.assertEqual(result.final_text, "done")
        first_payload_tools = model.client.chat.completions.request_payloads[0].get("tools") or []
        first_names = [item["function"]["name"] for item in first_payload_tools]
        self.assertIn("search_tools", first_names)
        self.assertNotIn("write_file", first_names)

        second_payload_tools = model.client.chat.completions.request_payloads[1].get("tools") or []
        names = [item["function"]["name"] for item in second_payload_tools]
        self.assertIn("write_file", names)
        self.assertIn("edit_file", names)


if __name__ == "__main__":
    unittest.main()
