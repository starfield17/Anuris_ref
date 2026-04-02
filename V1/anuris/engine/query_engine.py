from __future__ import annotations

import json
import uuid
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ChatModel
from ..tools.base import ToolExecutionResult, ToolPermissionError
from .context import PermissionContext, SessionServices, ToolUseContext
from .messages import EngineResponse, ToolCall, extract_text_content
from .session_store import SessionStore


class QueryEngine:
    """Claude Code inspired query loop with a Python-native tool runtime."""

    def __init__(
        self,
        model: ChatModel,
        session_store: SessionStore,
        tool_registry: Any,
        services: SessionServices,
        workspace_root: Path,
        config: Any,
        event_callback: Optional[Any] = None,
        ui: Any = None,
        switch_workspace: Optional[Any] = None,
        reset_workspace: Optional[Any] = None,
        max_turns: int = 12,
        auto_compact_chars: int = 18000,
    ):
        self.model = model
        self.session_store = session_store
        self.tool_registry = tool_registry
        self.services = services
        self.workspace_root = Path(workspace_root).resolve()
        self.config = config
        self.event_callback = event_callback
        self.ui = ui
        self.switch_workspace = switch_workspace or (lambda path: f"workspace switching unavailable: {path}")
        self.reset_workspace = reset_workspace or (lambda: "workspace reset unavailable")
        self.max_turns = max_turns
        self.auto_compact_chars = auto_compact_chars

    def submit(
        self,
        prompt: str,
        *,
        attachments: Optional[List[Dict[str, Any]]] = None,
        permission_context: Optional[PermissionContext] = None,
        allowed_tool_names: Optional[set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EngineResponse:
        permission = permission_context or PermissionContext()
        user_metadata = metadata or {}
        self._record_user_message(prompt, attachments or [], user_metadata)
        runtime_notices = self._drain_runtime_notices()
        prefetched_skills = self._prefetch_skills(prompt)
        reasoning_parts: List[str] = []
        tool_events: List[str] = []

        for round_index in range(1, self.max_turns + 1):
            self._maybe_auto_compact(prompt if round_index == 1 else "")
            self._emit("agent_round_started", round=round_index)
            tool_context = self._build_tool_context(permission, allowed_tool_names)
            active_tools = self.tool_registry.get_schemas(tool_context, allowed_tool_names=allowed_tool_names)
            api_messages = self._build_api_messages(runtime_notices, prefetched_skills)
            self._emit(
                "before_model_call",
                round=round_index,
                active_tools=[tool["function"]["name"] for tool in active_tools],
                prefetched_skills=[item["name"] for item in prefetched_skills],
                queued_notices=len(runtime_notices),
            )
            content, reasoning, tool_calls = self._consume_completion(api_messages, active_tools, round_index)
            if reasoning:
                reasoning_parts.append(reasoning)
                self._emit("assistant_reasoning", content=reasoning, round=round_index)
            self.session_store.add_assistant_message(
                content=content,
                tool_calls=tool_calls,
                reasoning=reasoning,
                metadata={"round": round_index},
            )
            if not tool_calls:
                final_text = extract_text_content(content)
                self.session_store.write_transcript()
                self._emit("assistant_message", content=final_text, round=round_index)
                return EngineResponse(
                    final_text=final_text,
                    reasoning_text="\n\n".join(part for part in reasoning_parts if part),
                    rounds=round_index,
                    tool_events=tool_events,
                )
            tool_events.extend(self._execute_tool_calls(tool_calls, tool_context, allowed_tool_names, round_index))

        self.session_store.write_transcript()
        raise RuntimeError("Maximum query turns exceeded")

    def run_subagent(self, prompt: str, description: str, readonly: bool = True) -> str:
        subagent_store = SessionStore(
            system_prompt=self.session_store.system_prompt,
            workspace_root=self.workspace_root,
            session_id=f"{self.session_store.session_id}_sub_{uuid.uuid4().hex[:8]}",
        )
        allowed_tool_names = {
            "bash",
            "read_file",
            "glob",
            "grep",
            "load_skill",
            "todo_write",
            "task_get",
            "task_list",
            "tool_search",
            "list_mcp_resources",
            "read_mcp_resource",
        }
        if not readonly:
            allowed_tool_names |= {"write_file", "edit_file", "task_create", "task_update"}
        subagent = QueryEngine(
            model=self.model,
            session_store=subagent_store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace_root,
            config=self.config,
            event_callback=self.event_callback,
            ui=None,
            switch_workspace=self.switch_workspace,
            reset_workspace=self.reset_workspace,
            max_turns=max(4, self.max_turns // 2),
            auto_compact_chars=self.auto_compact_chars,
        )
        result = subagent.submit(
            f"Task: {description}\n\n{prompt}".strip(),
            permission_context=PermissionContext(
                mode="readonly" if readonly else "default",
                allowed_tools=allowed_tool_names,
            ),
            allowed_tool_names=allowed_tool_names,
            metadata={"subagent": True, "description": description},
        )
        return result.final_text

    def _record_user_message(self, prompt: str, attachments: List[Dict[str, Any]], metadata: Dict[str, Any]) -> None:
        user_content: Any = prompt
        if attachments:
            user_content = [{"type": "text", "text": prompt}, *attachments]
        self.session_store.add_user_message(user_content, metadata=metadata)
        self._emit("user_message", content=prompt, attachments=metadata.get("attachments", []))

    def _drain_runtime_notices(self) -> List[Dict[str, Any]]:
        center = getattr(self.services, "notification_center", None)
        notices = center.drain_for_model(limit=6) if center is not None else []
        if notices:
            self._emit("runtime_notice_injected", notices=notices)
        return notices

    def _prefetch_skills(self, prompt: str) -> List[Dict[str, Any]]:
        loader = getattr(self.services, "skill_loader", None)
        skills = loader.prefetch(prompt) if loader is not None else []
        if skills:
            self._emit("skill_prefetch", skills=skills)
        return skills

    def _build_api_messages(self, runtime_notices: List[Dict[str, Any]], prefetched_skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        api_messages = self.session_store.to_api_messages()
        transient_messages: List[Dict[str, Any]] = []
        for memory_message in self._memory_messages():
            transient_messages.append(memory_message)
        if runtime_notices:
            lines = ["Queued runtime notices for this turn:"]
            lines.extend(f"- {item.get('message', '')}" for item in runtime_notices)
            transient_messages.append({"role": "system", "content": "\n".join(lines)})
        if prefetched_skills:
            lines = ["Relevant available skills (load with `load_skill` if useful):"]
            lines.extend(f"- {item['name']}: {item['description']}" for item in prefetched_skills)
            transient_messages.append({"role": "system", "content": "\n".join(lines)})
        if not transient_messages:
            return api_messages
        if api_messages:
            return [api_messages[0], *transient_messages, *api_messages[1:]]
        return transient_messages

    def _memory_messages(self) -> List[Dict[str, Any]]:
        manager = getattr(self.services, "memory_manager", None)
        if manager is None:
            return []
        workspace_memory = manager.read()
        session_memory = manager.read_session(self.session_store.session_id)
        items: List[Dict[str, Any]] = []
        if workspace_memory != "No memory saved.":
            items.append({"role": "system", "content": f"Workspace memory:\n{workspace_memory}"})
        if session_memory != "No memory saved.":
            items.append({"role": "system", "content": f"Session memory:\n{session_memory}"})
        if items:
            self._emit("memory_injected", workspace=bool(items[:1]), session=len(items) > 1)
        return items

    def _consume_completion(
        self,
        messages: List[Dict[str, Any]],
        active_tools: List[Dict[str, Any]],
        round_index: int,
    ) -> tuple[Any, str, List[ToolCall]]:
        response = self.model.create_completion(
            messages=messages,
            stream=True,
            tools=active_tools or None,
            tool_choice="auto" if active_tools else None,
        )
        if isinstance(response, dict) or hasattr(response, "choices") or not isinstance(response, IterableABC):
            return self._extract_completion_payload(response)
        return self._consume_streaming_response(response, round_index)

    def _consume_streaming_response(self, response: Any, round_index: int) -> tuple[str, str, List[ToolCall]]:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_parts: Dict[str, Dict[str, str]] = {}
        for chunk in response:
            delta = self._extract_stream_delta(chunk)
            if delta["content"]:
                content_parts.append(delta["content"])
                self._emit("assistant_delta", content=delta["content"], round=round_index)
            if delta["reasoning"]:
                reasoning_parts.append(delta["reasoning"])
            for item in delta["tool_calls"]:
                record = tool_parts.setdefault(item["id"], {"id": item["id"], "name": "", "arguments_json": ""})
                if item["name"]:
                    record["name"] = item["name"]
                record["arguments_json"] += item["arguments_json"]
        tool_calls = [ToolCall(**item) for item in tool_parts.values()]
        return "".join(content_parts), "".join(reasoning_parts), tool_calls

    def _execute_tool_calls(
        self,
        tool_calls: List[ToolCall],
        tool_context: ToolUseContext,
        allowed_tool_names: Optional[set[str]],
        round_index: int,
    ) -> List[str]:
        summaries: List[str] = []
        for tool_call in tool_calls:
            args = self._parse_tool_arguments(tool_call.arguments_json)
            self._emit("tool_called", tool_name=tool_call.name, arguments=args, tool_call_id=tool_call.id, round=round_index)
            result = self._execute_one_tool(tool_call, args, tool_context, allowed_tool_names)
            self.session_store.add_tool_result(
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                content=result.model_content,
                is_error=result.is_error,
                metadata=result.metadata,
            )
            summaries.append(result.summary or f"{tool_call.name}: {result.model_content[:120]}")
            self._emit(
                "tool_result",
                tool_name=tool_call.name,
                content=result.display_content,
                is_error=result.is_error,
                metadata=result.metadata,
                summary=result.summary,
                round=round_index,
            )
        return summaries

    def _execute_one_tool(
        self,
        tool_call: ToolCall,
        args: Dict[str, Any],
        tool_context: ToolUseContext,
        allowed_tool_names: Optional[set[str]],
    ) -> ToolExecutionResult:
        try:
            tool = self.tool_registry.require(tool_call.name, tool_context, allowed_tool_names=allowed_tool_names)
            return tool.execute(args, tool_context)
        except ToolPermissionError as exc:
            return ToolExecutionResult(
                model_content=f"Error: {exc.denial.get('message', str(exc))}",
                display_content=f"Error: {exc.denial.get('message', str(exc))}",
                is_error=True,
                summary=f"{tool_call.name} denied",
                metadata={"permission_denial": exc.denial},
            )
        except Exception as exc:
            return ToolExecutionResult(
                model_content=f"Error: {exc}",
                display_content=f"Error: {exc}",
                is_error=True,
                summary=f"{tool_call.name} failed",
            )

    def _build_tool_context(self, permission_context: PermissionContext, allowed_tool_names: Optional[set[str]]) -> ToolUseContext:
        return ToolUseContext(
            workspace_root=self.workspace_root,
            session_store=self.session_store,
            services=self.services,
            permission_context=permission_context,
            emit_event=self._emit,
            run_subagent=self.run_subagent,
            switch_workspace=self.switch_workspace,
            reset_workspace=self.reset_workspace,
            config=self.config,
            ui=self.ui,
            metadata={"allowed_tool_names": allowed_tool_names, "tool_registry": getattr(self.tool_registry, "tools", [])},
        )

    def _maybe_auto_compact(self, pending_prompt: str = "") -> None:
        budget_service = getattr(self.services, "context_budget", None)
        if budget_service is None and self.session_store.approximate_size() >= self.auto_compact_chars:
            summary = self.session_store.compact_history("automatic compaction")
            self._emit("compact_boundary", content=summary)
            return
        if budget_service is None:
            return
        snapshot = budget_service.analyze(pending_prompt=pending_prompt)
        if not snapshot.should_compact:
            return
        focus = snapshot.compact_focus or "automatic compaction"
        summary = self.session_store.compact_history(focus)
        self._emit("compact_boundary", content=summary, focus=focus, compact_reason=snapshot.compact_reason, budget=snapshot.to_dict())

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_callback:
            self.event_callback({"type": event_type, **payload})

    @staticmethod
    def _parse_tool_arguments(arguments_json: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Tool arguments were not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must decode to an object")
        return parsed

    def _extract_completion_payload(self, response: Any) -> tuple[Any, str, List[ToolCall]]:
        if hasattr(response, "choices"):
            choices = getattr(response, "choices", [])
            if choices:
                return self._extract_choice_payload(choices[0].message)
        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                return self._extract_choice_payload(choices[0].get("message", {}))
            return self._extract_choice_payload(response)
        raise ValueError("Unsupported completion response shape")

    def _extract_choice_payload(self, message: Any) -> tuple[Any, str, List[ToolCall]]:
        content = self._normalize_content(_read_attr(message, "content", ""))
        reasoning = str(_read_attr(message, "reasoning_content", "") or "")
        tool_calls = self._normalize_tool_calls(_read_attr(message, "tool_calls", []))
        return content, reasoning, tool_calls

    def _extract_stream_delta(self, chunk: Any) -> Dict[str, Any]:
        choice = chunk.get("choices", [{}])[0] if isinstance(chunk, dict) else (getattr(chunk, "choices", [{}])[0])
        delta = choice.get("delta", choice.get("message", {})) if isinstance(choice, dict) else getattr(choice, "delta", getattr(choice, "message", None))
        content = self._coerce_text(_read_attr(delta, "content", ""))
        reasoning = str(_read_attr(delta, "reasoning_content", "") or _read_attr(delta, "reasoning", "") or "")
        return {"content": content, "reasoning": reasoning, "tool_calls": self._stream_tool_calls(delta)}

    def _coerce_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        parts: List[str] = []
        for item in value or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "text"):
                parts.append(str(getattr(item, "text", "")))
        return "".join(parts)

    def _stream_tool_calls(self, delta: Any) -> List[Dict[str, str]]:
        raw = _read_attr(delta, "tool_calls", []) or []
        items: List[Dict[str, str]] = []
        for index, entry in enumerate(raw):
            function = _read_attr(entry, "function", {})
            items.append(
                {
                    "id": str(_read_attr(entry, "id", f"call_{index}")),
                    "name": str(_read_attr(function, "name", "")),
                    "arguments_json": str(_read_attr(function, "arguments", "")),
                }
            )
        return items

    @staticmethod
    def _normalize_content(content: Any) -> Any:
        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    blocks.append({"type": "text", "text": str(item.get("text", ""))})
                elif hasattr(item, "type") and getattr(item, "type") == "text":
                    blocks.append({"type": "text", "text": str(getattr(item, "text", ""))})
            return blocks or ""
        return content or ""

    def _normalize_tool_calls(self, tool_calls: Any) -> List[ToolCall]:
        normalized: List[ToolCall] = []
        for item in tool_calls or []:
            function = _read_attr(item, "function", {})
            if getattr(item, "type", "") == "tool_use":
                normalized.append(ToolCall(id=str(getattr(item, "id", uuid.uuid4().hex[:8])), name=str(getattr(item, "name", "")), arguments_json=json.dumps(getattr(item, "input", {}))))
                continue
            normalized.append(
                ToolCall(
                    id=str(_read_attr(item, "id", uuid.uuid4().hex[:8])),
                    name=str(_read_attr(function, "name", _read_attr(item, "name", ""))),
                    arguments_json=str(_read_attr(function, "arguments", _read_attr(item, "arguments", "{}"))),
                )
            )
        return normalized


def _read_attr(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
