import base64
import hashlib
import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from anuris.config import Config
from anuris.debug_server import DebugHTTPServer, DebugSessionManager
from anuris.session import ChatSession
from anuris.engine import SessionStore
from anuris.runtime import RuntimeRunManager, RuntimeState, ToolResultStore
from anuris.runtime.tool_results import PERSIST_POLICY_NEVER
from anuris.services.hooks import HookManager
from anuris.services.memory import MemoryManager


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        del messages, stream, tools, tool_choice
        return self.responses.pop(0)


class SlowFakeModel(FakeModel):
    def __init__(self, responses, *, delay_sec: float = 0.0):
        super().__init__(responses)
        self.delay_sec = delay_sec

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        if self.delay_sec > 0:
            threading.Event().wait(self.delay_sec)
        return super().create_completion(messages, stream, tools=tools, tool_choice=tool_choice)


class RuntimeFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_store_supports_event_paging(self):
        store = SessionStore("system", self.workspace, "history1")
        for index in range(4):
            store.add_user_message(f"user {index}")
            store.add_assistant_message(f"assistant {index}")
        latest = store.latest_events(limit=2)
        self.assertEqual(len(latest.events), 2)
        older = store.older_events(latest.first_id, limit=2)
        self.assertEqual(len(older.events), 2)
        self.assertTrue(older.has_more)

    def test_runtime_state_tracks_runs_and_queue(self):
        state = RuntimeState(
            session_id="runtime1",
            workspace_root=self.workspace,
            event_path=self.workspace / "runtime.jsonl",
            tasks_root=self.workspace / "runtime-tasks",
        )
        run = state.runs.create("run1", "session_request", self.workspace, description="Inspect runtime")
        task = state.tasks.create(
            "task1",
            "background_command",
            "Inspect runtime",
            run_id=run.id,
            workspace_root=str(self.workspace),
            worktree_id=str(self.workspace),
            artifact_dir=run.artifact_dir,
            transcript_path=run.transcript_path,
        )

        event = state.publish("request_started", request_id="req1", request_kind="message")

        self.assertEqual(event["type"], "request_started")
        self.assertTrue((Path(run.artifact_dir) / "run.json").exists())
        self.assertEqual(state.tasks.get(task.id).run_id, run.id)
        queued = state.queue.peek()
        self.assertIsNotNone(queued)
        self.assertEqual(queued.event_type, "request_started")

    def test_tool_result_store_persists_large_payloads(self):
        store = ToolResultStore(self.workspace / "tool-results", inline_limit=16, preview_chars=10)
        persisted = store.prepare(
            tool_name="bash",
            tool_call_id="call_big",
            model_content="0123456789abcdefghijklmnopqrstuvwxyz",
        )
        self.assertTrue(persisted.metadata["stored_externally"])
        artifact_path = Path(persisted.metadata["artifact_path"])
        self.assertTrue(artifact_path.exists())
        self.assertIn("Tool output stored externally", persisted.content_for_model)
        self.assertEqual(store.read_artifact(str(artifact_path)), "0123456789abcdefghijklmnopqrstuvwxyz")

    def test_tool_result_store_keeps_never_persist_payloads_inline(self):
        store = ToolResultStore(self.workspace / "tool-results", inline_limit=16, preview_chars=10)
        inline = store.prepare(
            tool_name="read_file",
            tool_call_id="call_read",
            model_content="0123456789abcdefghijklmnopqrstuvwxyz",
            persist_policy=PERSIST_POLICY_NEVER,
        )

        self.assertFalse(inline.metadata["stored_externally"])
        self.assertEqual(inline.metadata["persistence_policy"], PERSIST_POLICY_NEVER)
        self.assertEqual(inline.content_for_model, "0123456789abcdefghijklmnopqrstuvwxyz")

    def test_run_manager_terminal_updates_do_not_duplicate_metadata_fields(self):
        manager = RuntimeRunManager(self.workspace / "runs")
        run = manager.create("run_terminal", "session_request", self.workspace)

        completed = manager.complete(
            run.id,
            transcript_path=self.workspace / "artifacts" / "transcript.md",
            output_path=self.workspace / "artifacts" / "output.log",
            task_id="task-terminal",
            summary="done",
        )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.task_id, "task-terminal")
        self.assertTrue(completed.transcript_path.endswith("transcript.md"))
        self.assertTrue(completed.output_path.endswith("output.log"))
        self.assertEqual(completed.metadata, {"summary": "done"})

    def test_memory_manager_separates_workspace_and_session_memory(self):
        manager = MemoryManager(self.workspace)
        manager.append("workspace fact")
        manager.append_session("sess1", "session fact")
        self.assertIn("workspace fact", manager.read())
        self.assertIn("session fact", manager.read_session("sess1"))
        self.assertEqual(manager.read_session("other"), "No memory saved.")

    def test_hook_manager_exposes_structured_results(self):
        manager = HookManager(self.workspace)
        hooks_path = self.workspace / ".anuris" / "hooks.json"
        hooks_path.write_text(
            json.dumps({"hooks": [{"event": "user_message", "command": "printf ok", "blocking": True}]}),
            encoding="utf-8",
        )
        manager.reload()
        results = manager.run_structured("user_message", {"type": "user_message"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "success")
        self.assertTrue(results[0]["blocking"])

    def test_debug_http_server_supports_history_and_sse_stream(self):
        manager = DebugSessionManager(
            self.config,
            workspace_root=self.workspace,
            debug_dir=self.workspace / ".debug",
            model_factory=lambda config: FakeModel([{"choices": [{"message": {"content": "Hello stream."}}]}]),
        )
        server = DebugHTTPServer(manager, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(server.host, server.port, timeout=5)
            conn.request("POST", "/sessions", body=json.dumps({"session_id": "sse1"}), headers={"Content-Type": "application/json"})
            created = json.loads(conn.getresponse().read().decode("utf-8"))
            self.assertEqual(created["session_id"], "sse1")

            conn = http.client.HTTPConnection(server.host, server.port, timeout=5)
            conn.request(
                "POST",
                "/sessions/sse1/message/stream",
                body=json.dumps({"message": "hello"}),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("event: request_started", body)
            self.assertIn("event: stream_completed", body)

            conn = http.client.HTTPConnection(server.host, server.port, timeout=5)
            conn.request("GET", "/sessions/sse1/history?limit=5")
            history = json.loads(conn.getresponse().read().decode("utf-8"))
            self.assertTrue(history["events"])
            self.assertIn("first_id", history)
        finally:
            server.shutdown()

    def test_debug_http_server_supports_websocket_stream(self):
        manager = DebugSessionManager(
            self.config,
            workspace_root=self.workspace,
            debug_dir=self.workspace / ".debug",
            model_factory=lambda config: FakeModel([{"choices": [{"message": {"content": "Hello websocket."}}]}]),
        )
        manager.create_session({"session_id": "ws1"})
        server = DebugHTTPServer(manager, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = socket.create_connection((server.host, server.port), timeout=5)
            key = base64.b64encode(b"runtime-test-key").decode("ascii")
            request = (
                "GET /sessions/ws1/ws HTTP/1.1\r\n"
                f"Host: {server.host}:{server.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            client.sendall(request.encode("utf-8"))
            response = client.recv(4096).decode("utf-8", errors="ignore")
            self.assertIn("101 Switching Protocols", response)
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()).decode("ascii")
            self.assertIn(accept, response)

            client.sendall(_masked_frame(json.dumps({"message": "hello", "request_kind": "message"})))
            first = _read_text_frame(client)
            second = _read_text_frame(client)
            combined = "\n".join([response, first, second])
            self.assertIn("session_connected", combined)
            self.assertTrue("request_started" in combined or "stream_completed" in combined)
            client.close()
        finally:
            server.shutdown()

    def test_chat_session_stream_emits_progress_and_notice_events(self):
        (self.workspace / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [_tool_call("read_file", '{"path":"sample.txt"}', "read_1")]}}]},
                {"choices": [{"message": {"content": "Done."}}]},
            ]
        )
        session = ChatSession(self.config, workspace_root=self.workspace, model=model, session_id="live_updates_1")

        events = list(session.handle_input_stream("Inspect the sample file."))
        event_types = [event["type"] for event in events]

        self.assertIn("tool_called", event_types)
        self.assertIn("runtime_notice", event_types)
        self.assertIn("progress_update", event_types)
        self.assertIn("stream_completed", event_types)
        self.assertLess(event_types.index("runtime_notice"), event_types.index("stream_completed"))
        self.assertLess(event_types.index("progress_update"), event_types.index("stream_completed"))

    def test_chat_session_stream_emits_heartbeat_for_slow_requests(self):
        model = SlowFakeModel(
            [{"choices": [{"message": {"content": "Done."}}]}],
            delay_sec=1.2,
        )
        session = ChatSession(self.config, workspace_root=self.workspace, model=model, session_id="live_updates_2")

        events = list(session.handle_input_stream("Reply slowly."))
        event_types = [event["type"] for event in events]

        self.assertIn("heartbeat", event_types)
        self.assertIn("stream_completed", event_types)
        self.assertLess(event_types.index("heartbeat"), event_types.index("stream_completed"))


def _masked_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    mask = b"mask"
    length = len(payload)
    if length >= 126:
        raise AssertionError("test payload too large")
    header = bytes([0x81, 0x80 | length])
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + masked


def _tool_call(name: str, arguments: str, tool_id: str = "call_1") -> dict:
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _read_text_frame(client: socket.socket) -> str:
    frame = client.recv(65536)
    if not frame:
        return ""
    length = frame[1] & 0x7F
    index = 2
    if length == 126:
        length = int.from_bytes(frame[index:index + 2], "big")
        index += 2
    elif length == 127:
        length = int.from_bytes(frame[index:index + 8], "big")
        index += 8
    return frame[index:index + length].decode("utf-8", errors="ignore")
