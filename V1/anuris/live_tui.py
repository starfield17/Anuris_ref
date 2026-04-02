from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

MAX_ACTIVITY_ITEMS = 6
MAX_NOTICE_ITEMS = 4
MAX_ASSISTANT_PREVIEW_CHARS = 1200
MAX_REASONING_PREVIEW_CHARS = 500


@dataclass(frozen=True)
class LiveActivity:
    label: str
    detail: str
    tone: str = "info"


@dataclass
class LiveToolStatus:
    name: str
    status: str
    detail: str = ""
    tone: str = "info"


@dataclass
class LiveTurnState:
    prompt: str = ""
    request_id: str = ""
    status: str = "running"
    stage: str = "starting"
    round_count: int = 0
    assistant_text: str = ""
    reasoning_text: str = ""
    final_text: str = ""
    error_text: str = ""
    last_progress: str = ""
    last_heartbeat_at: str = ""
    heartbeat_count: int = 0
    active_tools: Dict[str, LiveToolStatus] = field(default_factory=dict)
    recent_activity: Deque[LiveActivity] = field(
        default_factory=lambda: deque(maxlen=MAX_ACTIVITY_ITEMS)
    )
    recent_notices: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_NOTICE_ITEMS)
    )

    def apply_event(self, event: Dict[str, Any]) -> None:
        event_type = str(event.get("type", "") or "")
        if event_type == "request_started":
            self.request_id = str(event.get("request_id", "") or "")
            self.status = "running"
            self.stage = "started"
            self._record("request", "Request started")
            return
        if event_type == "assistant_delta":
            self.assistant_text += str(event.get("content", "") or "")
            return
        if event_type == "assistant_reasoning":
            content = str(event.get("content", "") or "")
            if content:
                self.reasoning_text = _append_block(self.reasoning_text, content)
                self.stage = "thinking"
            return
        if event_type == "assistant_message":
            self.final_text = str(event.get("content", "") or "")
            return
        if event_type == "tool_called":
            key = _tool_key(event)
            tool_name = str(event.get("tool_name", "") or "tool")
            self.active_tools[key] = LiveToolStatus(
                name=tool_name,
                status="running",
                detail=f"running {tool_name}",
                tone="warning",
            )
            self.stage = "tool_running"
            self._record("tool", f"Running {tool_name}", tone="warning")
            return
        if event_type == "tool_result":
            tool_name = str(event.get("tool_name", "") or "tool")
            self._complete_tool(tool_name)
            tone = "danger" if event.get("is_error") else "success"
            detail = str(event.get("summary", "") or f"{tool_name} {'failed' if event.get('is_error') else 'completed'}")
            self._record("tool", detail, tone=tone)
            return
        if event_type == "runtime_notice":
            self.recent_notices.append(dict(event))
            detail = str(event.get("display_message", "") or event.get("message", "") or "")
            tone = str(event.get("tone", "info") or "info")
            label = str(event.get("channel", "runtime") or "runtime")
            self._record(label, detail, tone=tone)
            return
        if event_type == "progress_update":
            self.status = str(event.get("status", self.status) or self.status)
            self.stage = str(event.get("stage", self.stage) or self.stage)
            self.last_progress = str(event.get("summary", "") or "")
            if self.last_progress:
                self._record("progress", self.last_progress)
            if event.get("round_count"):
                self.round_count = int(event.get("round_count", 0) or 0)
            return
        if event_type == "heartbeat":
            self.last_heartbeat_at = str(event.get("last_activity_at", "") or "")
            self.heartbeat_count += 1
            self.status = str(event.get("status", self.status) or self.status)
            return
        if event_type == "request_finished":
            self.status = "completed"
            self.stage = "finished"
            self.round_count = int(event.get("round_count", 0) or self.round_count)
            self._record("request", "Request finished", tone="success")
            return
        if event_type == "request_failed":
            self.status = "failed"
            self.stage = "failed"
            self.error_text = str(event.get("error", "") or "Request failed")
            self._record("error", self.error_text, tone="danger")
            return
        if event_type == "request_summary":
            self.round_count = int(event.get("round_count", 0) or self.round_count)
            return
        if event_type == "stream_completed":
            response = event.get("response", {}) or {}
            self.complete_from_response(response)
            return
        detail = str(event.get("message", "") or "")
        if detail and event_type:
            self._record(event_type.replace("_", " "), detail)

    def complete_from_response(self, response: Dict[str, Any]) -> None:
        self.status = "completed" if not response.get("interrupted") else "interrupted"
        self.stage = "finished"
        self.final_text = str(response.get("final_text", "") or self.final_text)
        self.round_count = int(response.get("round_count", 0) or self.round_count)
        reasoning = str(response.get("reasoning_text", "") or "")
        if reasoning:
            self.reasoning_text = reasoning

    def assistant_preview(self) -> str:
        text = self.assistant_text or self.final_text
        return _trim_tail(text, MAX_ASSISTANT_PREVIEW_CHARS)

    def reasoning_preview(self) -> str:
        return _trim_tail(self.reasoning_text, MAX_REASONING_PREVIEW_CHARS)

    def status_summary(self) -> str:
        if self.error_text:
            return self.error_text
        if self.last_progress:
            return self.last_progress
        if self.active_tools:
            return ", ".join(tool.name for tool in self.active_tools.values())
        return "Waiting for updates"

    def _record(self, label: str, detail: str, tone: str = "info") -> None:
        detail = detail.strip()
        if not detail:
            return
        self.recent_activity.append(LiveActivity(label=label, detail=detail, tone=tone))

    def _complete_tool(self, tool_name: str) -> None:
        for key, tool in list(self.active_tools.items()):
            if tool.name == tool_name:
                del self.active_tools[key]
                break


def render_live_turn(state: LiveTurnState, palette: Any, session_title: str) -> Group:
    overview = Table.grid(expand=True)
    overview.add_column(style=palette.accent_soft, ratio=1)
    overview.add_column(style=palette.assistant, ratio=3)
    overview.add_row("session", session_title)
    overview.add_row("status", state.status)
    overview.add_row("stage", state.stage)
    overview.add_row("summary", state.status_summary())
    if state.request_id:
        overview.add_row("request", state.request_id)
    if state.round_count:
        overview.add_row("rounds", str(state.round_count))
    if state.heartbeat_count:
        overview.add_row("heartbeats", str(state.heartbeat_count))

    renderables: list[Any] = [
        Panel(
            overview,
            title="current turn",
            title_align="left",
            border_style=palette.border,
            box=box.ROUNDED,
        )
    ]
    assistant = state.assistant_preview()
    if assistant:
        renderables.append(
            Panel(
                Text(assistant, style=palette.assistant),
                title="assistant draft",
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )
    reasoning = state.reasoning_preview()
    if reasoning:
        renderables.append(
            Panel(
                Text(reasoning, style=palette.reasoning),
                title="thinking",
                title_align="left",
                border_style=palette.border,
                box=box.ROUNDED,
            )
        )
    activity = _build_activity_table(state, palette)
    if activity is not None:
        renderables.append(activity)
    return Group(*renderables)


def describe_plain_event(event: Dict[str, Any]) -> LiveActivity | None:
    event_type = str(event.get("type", "") or "")
    if event_type == "tool_called":
        return LiveActivity("tool", f"Running {event.get('tool_name', '')}", "warning")
    if event_type == "tool_result":
        tone = "danger" if event.get("is_error") else "success"
        return LiveActivity("tool", str(event.get("summary", "") or f"{event.get('tool_name', '')} finished"), tone)
    if event_type == "progress_update":
        return LiveActivity("progress", str(event.get("summary", "") or "Progress updated"))
    if event_type == "runtime_notice":
        return LiveActivity(
            str(event.get("channel", "runtime") or "runtime"),
            str(event.get("display_message", "") or event.get("message", "") or ""),
            str(event.get("tone", "info") or "info"),
        )
    if event_type == "heartbeat":
        return LiveActivity("heartbeat", "Still running")
    if event_type == "request_failed":
        return LiveActivity("error", str(event.get("error", "") or "Request failed"), "danger")
    return None


def _build_activity_table(state: LiveTurnState, palette: Any) -> Panel | None:
    has_tools = bool(state.active_tools)
    has_activity = bool(state.recent_activity)
    has_notices = bool(state.recent_notices)
    if not (has_tools or has_activity or has_notices):
        return None
    table = Table(box=box.SIMPLE_HEAVY, border_style=palette.border, pad_edge=False)
    table.add_column("kind", style=palette.accent_soft, width=12)
    table.add_column("detail", style=palette.assistant)
    if has_tools:
        for tool in state.active_tools.values():
            table.add_row("tool", f"{tool.name} ({tool.status})")
    for item in list(state.recent_activity)[-MAX_ACTIVITY_ITEMS:]:
        table.add_row(item.label, item.detail)
    for notice in list(state.recent_notices)[-MAX_NOTICE_ITEMS:]:
        table.add_row(str(notice.get("channel", "runtime")), str(notice.get("display_message", "") or notice.get("message", "") or ""))
    return Panel(
        table,
        title="activity",
        title_align="left",
        border_style=palette.border,
        box=box.ROUNDED,
    )


def _append_block(current: str, new_text: str) -> str:
    return f"{current}\n\n{new_text}".strip() if current else new_text


def _trim_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"…{text[-limit:]}"


def _tool_key(event: Dict[str, Any]) -> str:
    raw = str(event.get("tool_call_id", "") or "")
    if raw:
        return raw
    return f"{event.get('tool_name', 'tool')}:{event.get('round', 0)}"
