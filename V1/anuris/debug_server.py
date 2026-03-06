from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

from .config import Config
from .session import ChatSession


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DebugSessionRecorder:
    """Persists session metadata, event logs, and a replayable transcript."""

    def __init__(self, debug_dir: Path, session_id: str):
        self.session_id = session_id
        self.session_dir = debug_dir / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.session_dir / "events.jsonl"
        self.transcript_path = self.session_dir / "transcript.md"
        self.session_path = self.session_dir / "session.json"
        self._lock = threading.Lock()
        self.metadata: Dict[str, Any] = {}

    def initialize(self, *, session_name: Optional[str], agent_mode: bool, config: Config, workspace_root: Path) -> None:
        self.metadata = {
            "session_id": self.session_id,
            "session_name": session_name or self.session_id,
            "status": "idle",
            "agent_mode": agent_mode,
            "workspace_root": str(workspace_root),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "event_count": 0,
            "request_count": 0,
            "config": {
                "model": config.model,
                "base_url": config.base_url,
                "temperature": config.temperature,
                "reasoning": config.reasoning,
            },
        }
        self._write_session_file()
        self.record_event({"type": "session_created", "agent_mode": agent_mode})

    def record_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            payload = dict(event)
            payload.setdefault("session_id", self.session_id)
            payload.setdefault("timestamp", _utc_now())
            payload["sequence"] = self.metadata.get("event_count", 0) + 1

            with open(self.events_path, "a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

            self.metadata["event_count"] = payload["sequence"]
            self.metadata["updated_at"] = payload["timestamp"]
            event_type = payload.get("type")
            if event_type == "request_started":
                self.metadata["status"] = "running"
                self.metadata["request_count"] = self.metadata.get("request_count", 0) + 1
                self.metadata["last_request_id"] = payload.get("request_id")
                self.metadata["last_request_kind"] = payload.get("request_kind")
            elif event_type == "request_finished":
                self.metadata["status"] = "idle"
                self.metadata["last_result_preview"] = str(payload.get("final_text", "") or "")[:200]
            elif event_type == "request_failed":
                self.metadata["status"] = "failed"
                self.metadata["last_error"] = payload.get("error", "")
            self._write_session_file()
            self._write_transcript_file()

    def update_session(self, *, agent_mode: bool, status: Optional[str] = None) -> None:
        with self._lock:
            self.metadata["agent_mode"] = agent_mode
            if status:
                self.metadata["status"] = status
            self.metadata["updated_at"] = _utc_now()
            self._write_session_file()

    def get_session_payload(self) -> Dict[str, Any]:
        payload = dict(self.metadata)
        payload.update(
            {
                "session_dir": str(self.session_dir),
                "events_path": str(self.events_path),
                "transcript_path": str(self.transcript_path),
            }
        )
        return payload

    def load_events(self) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        with open(self.events_path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def read_transcript(self) -> str:
        if not self.transcript_path.exists():
            return ""
        return self.transcript_path.read_text(encoding="utf-8")

    def _write_session_file(self) -> None:
        with open(self.session_path, "w", encoding="utf-8") as file_obj:
            json.dump(self.metadata, file_obj, ensure_ascii=False, indent=2)

    def _write_transcript_file(self) -> None:
        transcript = self._render_transcript(self.load_events())
        self.transcript_path.write_text(transcript, encoding="utf-8")

    def _render_transcript(self, events: List[Dict[str, Any]]) -> str:
        lines = [
            f"# Debug Transcript: {self.metadata.get('session_name', self.session_id)}",
            "",
            f"- Session ID: `{self.session_id}`",
            f"- Status: `{self.metadata.get('status', 'unknown')}`",
            f"- Agent mode: `{self.metadata.get('agent_mode', True)}`",
            f"- Model: `{self.metadata.get('config', {}).get('model', '')}`",
            "",
        ]
        for event in events:
            event_type = event.get("type", "")
            if event_type == "session_created":
                continue
            if event_type == "request_started":
                lines.extend(
                    [
                        f"## Request {event.get('request_id', '')}",
                        f"- Kind: `{event.get('request_kind', 'message')}`",
                        f"- Agent mode: `{event.get('agent_mode', True)}`",
                        "",
                    ]
                )
            elif event_type == "user_message":
                lines.extend(["### User", "", str(event.get("content", "")), ""])
            elif event_type == "assistant_reasoning":
                lines.extend(["### Reasoning", "", "```text", str(event.get("content", "")), "```", ""])
            elif event_type == "tool_called":
                lines.extend(
                    [
                        f"### Tool `{event.get('tool_name', '')}`",
                        "",
                        "```json",
                        json.dumps(event.get("arguments", {}), ensure_ascii=False, indent=2),
                        "```",
                        "",
                    ]
                )
            elif event_type == "tool_result":
                lines.extend(["### Tool Result", "", "```text", str(event.get("content", "")), "```", ""])
            elif event_type == "assistant_message":
                lines.extend(["### Assistant", "", str(event.get("content", "")), ""])
            elif event_type == "request_failed":
                lines.extend(["### Error", "", "```text", str(event.get("error", "")), "```", ""])
            elif event_type == "request_finished":
                lines.extend([f"- Finished: rounds={event.get('round_count', 0)} interrupted={event.get('interrupted', False)}", ""])
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class DebugSessionEntry:
    session: ChatSession
    recorder: DebugSessionRecorder


class DebugSessionManager:
    """In-memory registry for headless debug sessions."""

    def __init__(
        self,
        base_config: Config,
        workspace_root: Path,
        debug_dir: Path,
        model_factory: Optional[Callable[[Config], Any]] = None,
    ):
        self.base_config = base_config
        self.workspace_root = Path(workspace_root).resolve()
        self.debug_dir = Path(debug_dir).resolve()
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.model_factory = model_factory
        self.sessions: Dict[str, DebugSessionEntry] = {}

    def create_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session_id = payload.get("session_id") or uuid4().hex[:12]
        config = self._build_config(payload)
        recorder = DebugSessionRecorder(self.debug_dir, session_id)
        model = self.model_factory(config) if self.model_factory else None
        session = ChatSession(
            config,
            workspace_root=self.workspace_root,
            model=model,
            event_callback=recorder.record_event,
            session_id=session_id,
        )
        if payload.get("agent_mode") is not None:
            session.agent_mode = bool(payload.get("agent_mode"))

        recorder.initialize(
            session_name=payload.get("session_name"),
            agent_mode=session.agent_mode,
            config=config,
            workspace_root=self.workspace_root,
        )
        self.sessions[session_id] = DebugSessionEntry(session=session, recorder=recorder)
        return recorder.get_session_payload()

    def submit_message(self, session_id: str, payload: Dict[str, Any], request_kind: str = "message") -> Dict[str, Any]:
        entry = self._get_entry(session_id)
        content = payload.get("message") if request_kind == "message" else payload.get("task") or payload.get("prompt")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Request body must include non-empty message/task text")
        attachment_paths = payload.get("attachments") or []
        response = entry.session.handle_input(
            content,
            request_kind=request_kind,
            attachment_paths=attachment_paths,
        )
        entry.recorder.update_session(agent_mode=entry.session.agent_mode, status="idle")
        result = response.to_dict()
        result.update(
            {
                "session_id": session_id,
                "events_path": str(entry.recorder.events_path),
                "transcript_path": str(entry.recorder.transcript_path),
            }
        )
        return result

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._get_entry(session_id).recorder.get_session_payload()

    def get_events(self, session_id: str) -> Dict[str, Any]:
        entry = self._get_entry(session_id)
        return {
            "session_id": session_id,
            "events": entry.recorder.load_events(),
            "events_path": str(entry.recorder.events_path),
        }

    def get_transcript(self, session_id: str) -> Dict[str, Any]:
        entry = self._get_entry(session_id)
        return {
            "session_id": session_id,
            "transcript": entry.recorder.read_transcript(),
            "transcript_path": str(entry.recorder.transcript_path),
        }

    def _get_entry(self, session_id: str) -> DebugSessionEntry:
        entry = self.sessions.get(session_id)
        if not entry:
            raise KeyError(f"Unknown session: {session_id}")
        return entry

    def _build_config(self, payload: Dict[str, Any]) -> Config:
        config_dict = self.base_config.to_dict()
        for key in ("model", "base_url", "temperature", "reasoning", "system_prompt"):
            if key in payload and payload[key] is not None:
                config_dict[key] = payload[key]
        return Config.from_dict(config_dict)


class DebugHTTPServer:
    """Thin HTTP wrapper around the debug session manager."""

    def __init__(self, manager: DebugSessionManager, host: str = "127.0.0.1", port: int = 8765):
        self.manager = manager
        self.host = host
        self.port = int(port)
        self.httpd = ThreadingHTTPServer((self.host, self.port), self._build_handler())
        self.port = int(self.httpd.server_address[1])

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _build_handler(self):
        manager = self.manager

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                try:
                    payload = self._read_json_body()
                    parsed = urlparse(self.path)
                    path_parts = [part for part in parsed.path.split("/") if part]
                    if path_parts == ["sessions"]:
                        self._respond_json(201, manager.create_session(payload))
                        return
                    if len(path_parts) == 3 and path_parts[0] == "sessions" and path_parts[2] in {"message", "task"}:
                        request_kind = "message" if path_parts[2] == "message" else "task"
                        result = manager.submit_message(path_parts[1], payload, request_kind=request_kind)
                        self._respond_json(200, result)
                        return
                    self._respond_json(404, {"error": f"Unknown path: {parsed.path}"})
                except KeyError as exc:
                    self._respond_json(404, {"error": str(exc)})
                except ValueError as exc:
                    self._respond_json(400, {"error": str(exc)})
                except Exception as exc:
                    self._respond_json(500, {"error": str(exc)})

            def do_GET(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    path_parts = [part for part in parsed.path.split("/") if part]
                    if len(path_parts) == 2 and path_parts[0] == "sessions":
                        self._respond_json(200, manager.get_session(path_parts[1]))
                        return
                    if len(path_parts) == 3 and path_parts[0] == "sessions" and path_parts[2] == "events":
                        self._respond_json(200, manager.get_events(path_parts[1]))
                        return
                    if len(path_parts) == 3 and path_parts[0] == "sessions" and path_parts[2] == "transcript":
                        self._respond_json(200, manager.get_transcript(path_parts[1]))
                        return
                    self._respond_json(404, {"error": f"Unknown path: {parsed.path}"})
                except KeyError as exc:
                    self._respond_json(404, {"error": str(exc)})
                except Exception as exc:
                    self._respond_json(500, {"error": str(exc)})

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

            def _read_json_body(self) -> Dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                if content_length <= 0:
                    return {}
                raw = self.rfile.read(content_length).decode("utf-8")
                if not raw.strip():
                    return {}
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("JSON body must be an object")
                return data

            def _respond_json(self, status_code: int, payload: Dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler
