import json
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional


VALID_MESSAGE_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_request",
    "plan_approval_response",
}


class MessageBus:
    """JSONL inbox bus used by lead and teammates."""

    def __init__(self, inbox_dir: Path):
        self.inbox_dir = inbox_dir.resolve()
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: Optional[Dict[str, object]] = None,
    ) -> str:
        if msg_type not in VALID_MESSAGE_TYPES:
            return f"Error: Invalid message type '{msg_type}'"

        payload: Dict[str, object] = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            payload.update(extra)

        inbox_path = self.inbox_dir / f"{to}.jsonl"
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with open(inbox_path, "a", encoding="utf-8") as file_obj:
                file_obj.write(line + "\n")
        return f"Sent {msg_type} to {to}"

    def read(self, name: str) -> List[Dict[str, object]]:
        inbox_path = self.inbox_dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return []

        with self._lock:
            lines = inbox_path.read_text(encoding="utf-8").splitlines()
            inbox_path.write_text("", encoding="utf-8")

        messages: List[Dict[str, object]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
        return messages


class TeamManager:
    """Persistent team roster + message bus + protocol trackers."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.team_dir = self.workspace_root / ".anuris_team"
        self.team_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.team_dir / "config.json"
        self.bus = MessageBus(self.team_dir / "inbox")
        self._lock = threading.Lock()
        self._worker_runner: Optional[Callable[[str, str, str], None]] = None
        self._threads: Dict[str, threading.Thread] = {}
        self._shutdown_requests: Dict[str, Dict[str, str]] = {}
        self._plan_requests: Dict[str, Dict[str, str]] = {}
        self._config = self._load_config()

    def set_worker_runner(self, runner: Callable[[str, str, str], None]) -> None:
        self._worker_runner = runner

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member_name = name.strip()
        member_role = role.strip() or "teammate"
        if not member_name:
            return "Error: teammate name is required"
        if not prompt.strip():
            return "Error: prompt is required"
        if self._worker_runner is None:
            return "Error: Team worker runner unavailable"

        with self._lock:
            member = self._find_member(member_name)
            if member:
                if member.get("status") not in ("idle", "shutdown", "error"):
                    return f"Error: '{member_name}' is currently {member.get('status')}"
                member["role"] = member_role
                member["status"] = "working"
            else:
                member = {"name": member_name, "role": member_role, "status": "working"}
                self._config["members"].append(member)
            self._save_config_locked()

        thread = threading.Thread(
            target=self._run_worker,
            args=(member_name, member_role, prompt),
            daemon=True,
        )
        self._threads[member_name] = thread
        thread.start()
        return f"Spawned '{member_name}' (role: {member_role})"

    def _run_worker(self, name: str, role: str, prompt: str) -> None:
        try:
            if self._worker_runner is not None:
                self._worker_runner(name, role, prompt)
        except Exception as exc:
            self.set_member_status(name, "error")
            self.bus.send("system", "lead", f"{name} failed: {exc}", "message")
            return

        with self._lock:
            member = self._find_member(name)
            if member and member.get("status") == "working":
                member["status"] = "idle"
                self._save_config_locked()

    def set_member_status(self, name: str, status: str) -> None:
        with self._lock:
            member = self._find_member(name)
            if not member:
                return
            member["status"] = status
            self._save_config_locked()

    def member_names(self) -> List[str]:
        with self._lock:
            return [member.get("name", "") for member in self._config.get("members", []) if member.get("name")]

    def list_members(self) -> str:
        with self._lock:
            members = list(self._config.get("members", []))
            team_name = self._config.get("team_name", "default")
        if not members:
            return "No teammates."
        lines = [f"Team: {team_name}"]
        for member in members:
            lines.append(
                f"- {member.get('name', '?')} ({member.get('role', 'teammate')}): {member.get('status', 'unknown')}"
            )
        return "\n".join(lines)

    def send_message(self, sender: str, to: str, content: str, msg_type: str = "message") -> str:
        return self.bus.send(sender, to, content, msg_type=msg_type)

    def send_from_lead(self, to: str, content: str, msg_type: str = "message") -> str:
        return self.send_message("lead", to, content, msg_type=msg_type)

    def broadcast_from_lead(self, content: str) -> str:
        names = self.member_names()
        sent = 0
        for name in names:
            if name == "lead":
                continue
            self.bus.send("lead", name, content, msg_type="broadcast")
            sent += 1
        return f"Broadcast to {sent} teammate(s)"

    def read_inbox(self, name: str) -> List[Dict[str, object]]:
        return self.bus.read(name)

    def read_inbox_text(self, name: str) -> str:
        return json.dumps(self.read_inbox(name), ensure_ascii=False, indent=2)

    def request_shutdown(self, teammate: str) -> str:
        name = teammate.strip()
        if not name:
            return "Error: teammate is required"
        request_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._shutdown_requests[request_id] = {"target": name, "status": "pending"}
        self.bus.send(
            "lead",
            name,
            "Please shutdown gracefully when safe.",
            msg_type="shutdown_request",
            extra={"request_id": request_id},
        )
        return f"Shutdown request {request_id} sent to {name}"

    def record_shutdown_response(
        self,
        sender: str,
        request_id: str,
        approve: bool,
        reason: str = "",
    ) -> str:
        with self._lock:
            if request_id in self._shutdown_requests:
                self._shutdown_requests[request_id]["status"] = "approved" if approve else "rejected"
            member = self._find_member(sender)
            if member and approve:
                member["status"] = "shutdown"
                self._save_config_locked()
        self.bus.send(
            sender,
            "lead",
            reason,
            msg_type="shutdown_response",
            extra={"request_id": request_id, "approve": approve},
        )
        return f"Shutdown {'approved' if approve else 'rejected'}"

    def check_shutdown(self, request_id: str) -> str:
        with self._lock:
            status = self._shutdown_requests.get(request_id)
        if not status:
            return f"Error: Unknown request_id '{request_id}'"
        return json.dumps(status, ensure_ascii=False, indent=2)

    def list_shutdown_requests(self) -> str:
        with self._lock:
            data = dict(self._shutdown_requests)
        if not data:
            return "No shutdown requests."
        lines = []
        for request_id, payload in sorted(data.items()):
            lines.append(f"- {request_id}: {payload.get('target', '?')} [{payload.get('status', 'unknown')}]")
        return "\n".join(lines)

    def submit_plan(self, sender: str, plan: str) -> str:
        plan_text = plan.strip()
        if not plan_text:
            return "Error: plan is required"
        request_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._plan_requests[request_id] = {"from": sender, "status": "pending", "plan": plan_text}
        self.bus.send(
            sender,
            "lead",
            plan_text,
            msg_type="plan_approval_request",
            extra={"request_id": request_id, "plan": plan_text},
        )
        return f"Plan submitted (request_id={request_id})"

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        with self._lock:
            payload = self._plan_requests.get(request_id)
            if not payload:
                return f"Error: Unknown request_id '{request_id}'"
            payload["status"] = "approved" if approve else "rejected"
            payload["feedback"] = feedback
            target = payload.get("from", "")
        self.bus.send(
            "lead",
            target,
            feedback,
            msg_type="plan_approval_response",
            extra={"request_id": request_id, "approve": approve, "feedback": feedback},
        )
        return f"Plan {request_id} marked as {payload['status']}"

    def list_plan_requests(self) -> str:
        with self._lock:
            data = dict(self._plan_requests)
        if not data:
            return "No plan requests."
        lines = []
        for request_id, payload in sorted(data.items()):
            lines.append(
                f"- {request_id}: from={payload.get('from', '?')} [{payload.get('status', 'unknown')}]"
            )
        return "\n".join(lines)

    def _load_config(self) -> Dict[str, object]:
        if self.config_path.exists():
            try:
                payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("team_name", "default")
        payload.setdefault("members", [])
        return payload

    def _save_config_locked(self) -> None:
        self.config_path.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_member(self, name: str) -> Optional[Dict[str, str]]:
        for member in self._config.get("members", []):
            if member.get("name") == name:
                return member
        return None
