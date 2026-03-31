from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..model import ChatModel
from ..tools.base import ToolExecutionResult
from .context import PermissionContext, SessionServices, ToolUseContext
from .messages import ConversationMessage, EngineResponse, ToolCall, extract_text_content
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
        attachments = attachments or []
        metadata = metadata or {}

        user_content: Any = prompt
        if attachments:
            user_content = [{"type": "text", "text": prompt}, *attachments]
        self.session_store.add_user_message(user_content, metadata=metadata)
        self._emit("user_message", content=prompt, attachments=metadata.get("attachments", []))

        reasoning_parts: List[str] = []
        tool_events: List[str] = []

        for round_index in range(1, self.max_turns + 1):
            self._maybe_auto_compact()
            self._emit("agent_round_started", round=round_index)
            tool_context = self._build_tool_context(permission, allowed_tool_names)
            active_tools = self.tool_registry.get_schemas(tool_context, allowed_tool_names=allowed_tool_names)
            response = self.model.create_completion(
                messages=self.session_store.to_api_messages(),
                stream=False,
                tools=active_tools or None,
                tool_choice="auto" if active_tools else None,
            )
            content, reasoning, tool_calls = self._extract_completion_payload(response)
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
                self.session_store.write_transcript()
                final_text = extract_text_content(content)
                self._emit("assistant_message", content=final_text, round=round_index)
                return EngineResponse(
                    final_text=final_text,
                    reasoning_text="\n\n".join(part for part in reasoning_parts if part),
                    rounds=round_index,
                    tool_events=tool_events,
                )

            for tool_call in tool_calls:
                args = self._parse_tool_arguments(tool_call.arguments_json)
                self._emit("tool_called", tool_name=tool_call.name, arguments=args, tool_call_id=tool_call.id, round=round_index)
                try:
                    tool = self.tool_registry.require(tool_call.name, tool_context, allowed_tool_names=allowed_tool_names)
                    result = tool.execute(args, tool_context)
                except Exception as exc:
                    result = ToolExecutionResult(
                        model_content=f"Error: {exc}",
                        display_content=f"Error: {exc}",
                        is_error=True,
                        summary=f"{tool_call.name} failed",
                    )
                self.session_store.add_tool_result(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=result.model_content,
                    is_error=result.is_error,
                    metadata=result.metadata,
                )
                summary = result.summary or f"{tool_call.name}: {result.model_content[:120]}"
                tool_events.append(summary)
                self._emit(
                    "tool_result",
                    tool_name=tool_call.name,
                    content=result.display_content,
                    is_error=result.is_error,
                    round=round_index,
                )

        self.session_store.write_transcript()
        raise RuntimeError("Maximum query turns exceeded")

    def run_subagent(self, prompt: str, description: str, readonly: bool = True) -> str:
        subagent_store = SessionStore(
            system_prompt=self.session_store.system_prompt,
            workspace_root=self.workspace_root,
            session_id=f"{self.session_store.session_id}_sub_{uuid.uuid4().hex[:8]}",
        )
        subagent_engine = QueryEngine(
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
        result = subagent_engine.submit(
            f"Task: {description}\n\n{prompt}".strip(),
            permission_context=PermissionContext(
                mode="readonly" if readonly else "default",
                allowed_tools=allowed_tool_names,
            ),
            allowed_tool_names=allowed_tool_names,
            metadata={"subagent": True, "description": description},
        )
        return result.final_text

    def _build_tool_context(
        self,
        permission_context: PermissionContext,
        allowed_tool_names: Optional[set[str]],
    ) -> ToolUseContext:
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
            metadata={
                "allowed_tool_names": allowed_tool_names,
                "tool_registry": getattr(self.tool_registry, "tools", []),
            },
        )

    def _maybe_auto_compact(self) -> None:
        if self.session_store.approximate_size() < self.auto_compact_chars:
            return
        summary = self.session_store.compact_history("automatic compaction")
        self._emit("compact_boundary", content=summary)

    def _emit(self, event_type: str, **payload: Any) -> None:
        if not self.event_callback:
            return
        event = {"type": event_type, **payload}
        self.event_callback(event)

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
                message = choices[0].message
                content = self._normalize_content(getattr(message, "content", ""))
                reasoning = str(getattr(message, "reasoning_content", "") or "")
                return content, reasoning, self._normalize_tool_calls(getattr(message, "tool_calls", []))

        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                content = self._normalize_content(message.get("content", ""))
                reasoning = str(message.get("reasoning_content", "") or "")
                return content, reasoning, self._normalize_tool_calls(message.get("tool_calls", []))

            content = response.get("content", "")
            reasoning = str(response.get("reasoning_content", "") or "")
            tool_calls = response.get("tool_calls", [])
            return self._normalize_content(content), reasoning, self._normalize_tool_calls(tool_calls)

        raise ValueError("Unsupported completion response shape")

    @staticmethod
    def _normalize_content(content: Any) -> Any:
        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        blocks.append({"type": "text", "text": str(item.get("text", ""))})
                    elif item.get("type") == "tool_use":
                        continue
                elif hasattr(item, "type") and getattr(item, "type") == "text":
                    blocks.append({"type": "text", "text": str(getattr(item, "text", ""))})
            if blocks:
                return blocks
        return content or ""

    def _normalize_tool_calls(self, tool_calls: Iterable[Any]) -> List[ToolCall]:
        normalized: List[ToolCall] = []
        for item in tool_calls or []:
            if hasattr(item, "function"):
                function = getattr(item, "function")
                normalized.append(
                    ToolCall(
                        id=str(getattr(item, "id", uuid.uuid4().hex[:8])),
                        name=str(getattr(function, "name", "")),
                        arguments_json=str(getattr(function, "arguments", "{}")),
                    )
                )
                continue
            if isinstance(item, dict):
                function = item.get("function", {})
                normalized.append(
                    ToolCall(
                        id=str(item.get("id", uuid.uuid4().hex[:8])),
                        name=str(function.get("name", item.get("name", ""))),
                        arguments_json=str(function.get("arguments", item.get("arguments", "{}"))),
                    )
                )
                continue
            if getattr(item, "type", "") == "tool_use":
                normalized.append(
                    ToolCall(
                        id=str(getattr(item, "id", uuid.uuid4().hex[:8])),
                        name=str(getattr(item, "name", "")),
                        arguments_json=json.dumps(getattr(item, "input", {})),
                    )
                )
        return normalized
