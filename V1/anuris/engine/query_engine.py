from __future__ import annotations

import uuid
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ChatModel
from ..tools.base import ToolExecutionResult, ToolPermissionError
from ..runtime import ToolResultStore
from .completion import (
    CompletionPayload,
    StreamingAccumulator,
    extract_completion_payload,
    parse_tool_arguments,
)
from .compaction_controller import CompactionController, DEFAULT_KEEP_LAST, EMERGENCY_KEEP_LAST
from .context import PermissionContext, SessionServices, ToolUseContext
from .messages import ConversationMessage, EngineResponse, extract_text_content
from .session_store import SessionStore
from .turn_budget import (
    DEFAULT_BASE_TURN_LIMIT,
    DEFAULT_MAX_TURN_LIMIT,
    DEFAULT_SUBAGENT_BASE_TURN_LIMIT,
    DEFAULT_SUBAGENT_MAX_TURN_LIMIT,
    DEFAULT_SUBAGENT_TURN_EXTENSION_STEP,
    DEFAULT_TURN_EXTENSION_STEP,
    AdaptiveTurnBudget,
    RoundProgress,
)
from .turn_policy import LoopProgressGuard, continuation_message_for, tool_batch_fingerprint, tool_call_validation_error, validate_pairing

DEFAULT_MAX_TURNS = DEFAULT_BASE_TURN_LIMIT


class QueryEngine:
    """Turn-based query engine with guarded long-task continuations."""

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
        max_turns: int = DEFAULT_MAX_TURNS,
        turn_extension_step: int = DEFAULT_TURN_EXTENSION_STEP,
        max_turn_limit: int = DEFAULT_MAX_TURN_LIMIT,
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
        self.turn_extension_step = turn_extension_step
        self.max_turn_limit = max(max_turn_limit, max_turns)
        self.auto_compact_chars = auto_compact_chars
        self.compaction_controller = CompactionController()

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
        previous_run_id = getattr(self, "_active_run_id", "")
        self._active_run_id = str((metadata or {}).get("run_id", "") or "")
        try:
            self.session_store.refresh_task_anchor(prompt, self.services)
            self._record_user_message(prompt, attachments or [], metadata or {})
            runtime_notices = self._drain_runtime_notices()
            prefetched_skills = self._prefetch_skills(prompt)
            progress_guard = LoopProgressGuard()
            turn_budget = AdaptiveTurnBudget(
                base_limit=self.max_turns,
                extension_step=self.turn_extension_step,
                max_limit=self.max_turn_limit,
            )
            continuation_count = 0
            reasoning_parts: List[str] = []
            tool_events: List[str] = []
            final_segments: List[str] = []
            last_tool_batch_fingerprint = ""
            round_index = 1
            while True:
                self._assert_message_pairing()
                progress = RoundProgress(
                    compaction=self._maybe_auto_compact(
                        prompt if round_index == 1 else "",
                        emergency=turn_budget.should_emergency_compact(round_index, continuation_count),
                    )
                )
                payload, tool_context = self._run_round(
                    permission,
                    allowed_tool_names,
                    runtime_notices,
                    prefetched_skills,
                    round_index,
                )
                if payload.reasoning:
                    reasoning_parts.append(payload.reasoning)
                    self._emit("assistant_reasoning", content=payload.reasoning, round=round_index)
                self._append_assistant_payload(payload, round_index)
                validation_error = tool_call_validation_error(payload.tool_calls)
                if validation_error:
                    raise RuntimeError(f"Tool call payload invalid: {validation_error}")
                if payload.tool_calls:
                    tool_messages, summaries = self._execute_tool_calls(
                        payload.tool_calls,
                        tool_context,
                        allowed_tool_names,
                        round_index,
                    )
                    current_tool_batch = tool_batch_fingerprint(payload.tool_calls, tool_messages)
                    previous_tool_batch_fingerprint = last_tool_batch_fingerprint
                    last_tool_batch_fingerprint = current_tool_batch or last_tool_batch_fingerprint
                    tool_events.extend(summaries)
                    loop_decision = progress_guard.record(payload.tool_calls, tool_messages)
                    progress = RoundProgress(
                        tool_batch_changed=(
                            bool(current_tool_batch)
                            and current_tool_batch != previous_tool_batch_fingerprint
                            and not loop_decision.low_value_repetition
                        ),
                        compaction=progress.compaction,
                    )
                    if loop_decision.low_value_repetition:
                        self._emit("low_value_read_repetition", round=round_index)
                    if loop_decision.stall_reason:
                        self._emit("loop_progress_stalled", round=round_index, reason=loop_decision.stall_reason)
                        raise RuntimeError(loop_decision.stall_reason)
                    self._ensure_turn_budget(turn_budget, round_index + 1, progress)
                    round_index += 1
                    continue
                content_text = extract_text_content(payload.content)
                if content_text:
                    final_segments.append(content_text)
                    progress = RoundProgress(
                        assistant_text=True,
                        compaction=progress.compaction,
                    )
                continuation = continuation_message_for(payload.finish_reason, continuation_count)
                anchor = self.session_store.continuation_anchor()
                if continuation and anchor:
                    continuation = f"{continuation}\n\n{anchor}"
                if continuation:
                    continuation_count += 1
                    progress = RoundProgress(
                        assistant_text=progress.assistant_text,
                        continuation=True,
                        compaction=progress.compaction,
                    )
                    self.session_store.add_user_message(
                        continuation,
                        metadata={"is_meta": True, "continuation": True, "round": round_index},
                    )
                    self._emit(
                        "continuation_scheduled",
                        round=round_index,
                        finish_reason=payload.finish_reason,
                        continuation_count=continuation_count,
                    )
                    self._ensure_turn_budget(turn_budget, round_index + 1, progress)
                    round_index += 1
                    continue
                return self._build_response(payload.content, reasoning_parts, round_index, tool_events, final_segments)
        finally:
            self._active_run_id = previous_run_id

    def run_subagent(self, prompt: str, description: str, readonly: bool = True) -> str:
        allowed_tool_names = _subagent_tool_names(readonly)
        run_manager = getattr(self.services, "run_manager", None)
        run_record = None
        if run_manager is not None:
            run_record = run_manager.create(
                f"{self.session_store.session_id}_subrun_{uuid.uuid4().hex[:8]}",
                "forked_run",
                self.workspace_root,
                description=description,
                owner="subagent",
                parent_run_id=getattr(self, "_active_run_id", ""),
                metadata={"readonly": readonly},
            )
        subagent = QueryEngine(
            model=self.model,
            session_store=SessionStore(
                system_prompt=self.session_store.system_prompt,
                workspace_root=self.workspace_root,
                session_id=f"{self.session_store.session_id}_sub_{uuid.uuid4().hex[:8]}",
            ),
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace_root,
            config=self.config,
            event_callback=self.event_callback,
            ui=None,
            switch_workspace=self.switch_workspace,
            reset_workspace=self.reset_workspace,
            max_turns=min(self.max_turns, DEFAULT_SUBAGENT_BASE_TURN_LIMIT),
            turn_extension_step=DEFAULT_SUBAGENT_TURN_EXTENSION_STEP,
            max_turn_limit=min(self.max_turn_limit, DEFAULT_SUBAGENT_MAX_TURN_LIMIT),
            auto_compact_chars=self.auto_compact_chars,
        )
        try:
            result = subagent.submit(
                f"Task: {description}\n\n{prompt}".strip(),
                permission_context=PermissionContext(
                    mode="readonly" if readonly else "default",
                    allowed_tools=allowed_tool_names,
                ),
                allowed_tool_names=allowed_tool_names,
                metadata={
                    "subagent": True,
                    "description": description,
                    "run_id": getattr(run_record, "id", ""),
                },
            )
        except Exception:
            if run_record is not None:
                run_manager.fail(
                    run_record.id,
                    transcript_path=str(subagent.session_store.session_dir / "transcript.md"),
                )
            raise
        if run_record is not None:
            run_manager.complete(
                run_record.id,
                transcript_path=str(subagent.session_store.session_dir / "transcript.md"),
                final_text=result.final_text,
            )
            if result.final_text:
                run_manager.append_output(run_record.id, result.final_text + "\n")
        return result.final_text

    def _run_round(
        self,
        permission_context: PermissionContext,
        allowed_tool_names: Optional[set[str]],
        runtime_notices: List[Dict[str, Any]],
        prefetched_skills: List[Dict[str, Any]],
        round_index: int,
    ) -> tuple[CompletionPayload, ToolUseContext]:
        self._emit("agent_round_started", round=round_index)
        tool_context = self._build_tool_context(permission_context, allowed_tool_names)
        active_tools = self.tool_registry.get_schemas(tool_context, allowed_tool_names=allowed_tool_names)
        api_messages = self._build_api_messages(runtime_notices, prefetched_skills)
        self._emit(
            "before_model_call",
            round=round_index,
            active_tools=[tool["function"]["name"] for tool in active_tools],
            prefetched_skills=[item["name"] for item in prefetched_skills],
            queued_notices=len(runtime_notices),
        )
        payload = self._consume_completion(api_messages, active_tools, round_index)
        return payload, tool_context

    def _consume_completion(
        self,
        messages: List[Dict[str, Any]],
        active_tools: List[Dict[str, Any]],
        round_index: int,
    ) -> CompletionPayload:
        response = self.model.create_completion(
            messages=messages,
            stream=True,
            tools=active_tools or None,
            tool_choice="auto" if active_tools else None,
        )
        if isinstance(response, dict) or hasattr(response, "choices") or not isinstance(response, IterableABC):
            return extract_completion_payload(response)
        accumulator = StreamingAccumulator()
        for chunk in response:
            delta = accumulator.ingest(chunk)
            if delta["content"]:
                self._emit("assistant_delta", content=delta["content"], round=round_index)
        payload = accumulator.build()
        validation_error = accumulator.validation_error()
        if validation_error and active_tools:
            self._emit("streaming_fallback_triggered", round=round_index, reason=validation_error)
            fallback = self.model.create_completion(
                messages=messages,
                stream=False,
                tools=active_tools or None,
                tool_choice="auto" if active_tools else None,
            )
            fallback_payload = extract_completion_payload(fallback)
            self._emit("provider_streaming_incompatible", round=round_index, reason=validation_error)
            return fallback_payload
        return payload

    def _execute_tool_calls(
        self,
        tool_calls: List[Any],
        tool_context: ToolUseContext,
        allowed_tool_names: Optional[set[str]],
        round_index: int,
    ) -> tuple[List[ConversationMessage], List[str]]:
        messages: List[ConversationMessage] = []
        summaries: List[str] = []
        for tool_call in tool_calls:
            args = parse_tool_arguments(tool_call.arguments_json)
            self._emit("tool_called", tool_name=tool_call.name, arguments=args, tool_call_id=tool_call.id, round=round_index)
            result = self._execute_one_tool(tool_call, args, tool_context, allowed_tool_names)
            result = self._prepare_tool_result(tool_call.name, tool_call.id, result)
            message = self.session_store.add_tool_result(
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                content=result.model_content,
                is_error=result.is_error,
                metadata=result.metadata,
            )
            messages.append(message)
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
        return messages, summaries

    def _execute_one_tool(
        self,
        tool_call: Any,
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

    def _record_user_message(
        self,
        prompt: str,
        attachments: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        content: Any = prompt if not attachments else [{"type": "text", "text": prompt}, *attachments]
        self.session_store.add_user_message(content, metadata=metadata)
        self._emit("user_message", content=prompt, attachments=metadata.get("attachments", []))

    def _drain_runtime_notices(self) -> List[Dict[str, Any]]:
        center = getattr(self.services, "notification_center", None)
        notices = center.drain_for_model(limit=6) if center is not None else []
        if notices:
            self._emit("runtime_notice_injected", notices=notices)
        return notices

    def _prefetch_skills(self, prompt: str) -> List[Dict[str, Any]]:
        loader = getattr(self.services, "skill_loader", None)
        current_paths = []
        tracker = getattr(self.services, "context_files", None)
        if tracker is not None and hasattr(tracker, "list_paths"):
            current_paths = tracker.list_paths()
        skills = loader.prefetch(prompt, current_paths=current_paths) if loader is not None else []
        if skills:
            self._emit("skill_prefetch", skills=skills)
        return skills

    def _build_api_messages(
        self,
        runtime_notices: List[Dict[str, Any]],
        prefetched_skills: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        api_messages = self.session_store.to_api_messages()
        transient_messages = [
            *self._task_anchor_messages(),
            *self._context_anchor_messages(),
            *self._memory_messages(),
            *_notice_messages(runtime_notices),
            *_skill_messages(prefetched_skills),
        ]
        if not transient_messages:
            return api_messages
        if not api_messages:
            return transient_messages
        return [api_messages[0], *transient_messages, *api_messages[1:]]

    def _memory_messages(self) -> List[Dict[str, Any]]:
        manager = getattr(self.services, "memory_manager", None)
        if manager is None:
            return []
        workspace_memory = manager.read()
        session_memory = manager.read_session(self.session_store.session_id)
        messages: List[Dict[str, Any]] = []
        if workspace_memory != "No memory saved.":
            messages.append({"role": "system", "content": f"Workspace memory:\n{workspace_memory}"})
        if session_memory != "No memory saved.":
            messages.append({"role": "system", "content": f"Session memory:\n{session_memory}"})
        if messages:
            self._emit("memory_injected", workspace=bool(messages[:1]), session=len(messages) > 1)
        return messages

    def _append_assistant_payload(self, payload: CompletionPayload, round_index: int) -> None:
        self.session_store.add_assistant_message(
            content=payload.content,
            tool_calls=payload.tool_calls,
            reasoning=payload.reasoning,
            metadata={"round": round_index, "finish_reason": payload.finish_reason},
        )

    def _build_response(
        self,
        content: Any,
        reasoning_parts: List[str],
        round_index: int,
        tool_events: List[str],
        final_segments: List[str],
    ) -> EngineResponse:
        final_text = "".join(final_segments) if final_segments else extract_text_content(content)
        self.session_store.record_task_progress(final_text, tool_events)
        self.session_store.write_transcript()
        self._emit("assistant_message", content=final_text, round=round_index)
        return EngineResponse(
            final_text=final_text,
            reasoning_text="\n\n".join(part for part in reasoning_parts if part),
            rounds=round_index,
            tool_events=tool_events,
        )

    def _task_anchor_messages(self) -> List[Dict[str, Any]]:
        anchor_message = self.session_store.task_anchor_message()
        if not anchor_message:
            return []
        return [{"role": "system", "content": anchor_message}]

    def _context_anchor_messages(self) -> List[Dict[str, Any]]:
        anchor_message = self.session_store.context_anchor_message()
        if not anchor_message:
            return []
        return [{"role": "system", "content": anchor_message}]

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
            metadata={"allowed_tool_names": allowed_tool_names, "tool_registry": getattr(self.tool_registry, "tools", [])},
        )

    def _prepare_tool_result(self, tool_name: str, tool_call_id: str, result: ToolExecutionResult) -> ToolExecutionResult:
        store = getattr(self.services, "tool_result_store", None)
        if store is None:
            store = ToolResultStore(self.session_store.session_dir / "runtime" / "tool-results")
            self.services.tool_result_store = store
        if store is None:
            return result
        tool = self.tool_registry.by_name.get(tool_name)
        persist_policy = getattr(tool, "result_persistence_policy", "persist")
        prepared = store.prepare(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            model_content=result.model_content,
            display_content=result.display_content,
            persist_policy=persist_policy,
        )
        return ToolExecutionResult(
            model_content=prepared.content_for_model,
            display_content=prepared.content_for_display,
            is_error=result.is_error,
            summary=result.summary,
            metadata={**result.metadata, **prepared.metadata},
        )

    def _assert_message_pairing(self) -> None:
        issue = validate_pairing(self.session_store.messages)
        if not issue.has_issue:
            return
        self._emit(
            "tool_pairing_failed",
            missing_tool_results=issue.missing_tool_results,
            orphaned_tool_results=issue.orphaned_tool_results,
        )
        raise RuntimeError(
            "Tool pairing mismatch detected: "
            f"missing={issue.missing_tool_results} orphaned={issue.orphaned_tool_results}"
        )

    def _maybe_auto_compact(self, pending_prompt: str = "", *, emergency: bool = False) -> bool:
        compact_mode = "emergency" if emergency else "normal"
        keep_last = EMERGENCY_KEEP_LAST if emergency else DEFAULT_KEEP_LAST
        compacted = False
        if self.compaction_controller.micro_compact_tool_results(self.session_store.messages, emergency=emergency):
            compacted = True
            self.session_store.sync_context_anchor()
            self._emit("tool_results_micro_compacted", compact_mode=compact_mode)
        budget_service = getattr(self.services, "context_budget", None)
        if budget_service is None:
            if self.session_store.approximate_size() < self.auto_compact_chars:
                return compacted
            focus = "emergency turn budget relief" if emergency else "automatic compaction"
            decision = self.compaction_controller.compact(self.session_store, focus, keep_last=keep_last)
            if decision.circuit_open:
                self._emit("compaction_circuit_open", failures=self.compaction_controller.failures)
                return compacted
            if not decision.changed:
                return compacted
            self._emit("compact_boundary", content=decision.summary, focus=focus, compact_mode=compact_mode)
            return True
        snapshot = budget_service.analyze(pending_prompt=pending_prompt)
        if not snapshot.should_compact and not emergency:
            return compacted
        focus = snapshot.compact_focus or ("emergency turn budget relief" if emergency else "automatic compaction")
        decision = self.compaction_controller.compact(self.session_store, focus, keep_last=keep_last)
        if decision.circuit_open:
            self._emit("compaction_circuit_open", failures=self.compaction_controller.failures)
            return compacted
        if not decision.changed:
            return compacted
        self._emit(
            "compact_boundary",
            content=decision.summary,
            focus=focus,
            compact_reason=snapshot.compact_reason or ("approaching adaptive turn budget" if emergency else ""),
            compact_mode=compact_mode,
            budget=snapshot.to_dict(),
        )
        return True

    def _ensure_turn_budget(self, turn_budget: AdaptiveTurnBudget, next_round: int, progress: RoundProgress) -> None:
        decision = turn_budget.ensure_capacity(next_round, progress)
        if decision.extended:
            self._emit(
                "turn_budget_extended",
                previous_limit=decision.previous_limit,
                new_limit=decision.new_limit,
                extension_count=decision.extension_count,
                reason=decision.reason,
            )
            return
        if not decision.exhausted:
            return
        self.session_store.write_transcript()
        self._emit(
            "turn_budget_exhausted",
            limit=decision.new_limit,
            extension_count=decision.extension_count,
            reason=decision.reason,
        )
        raise RuntimeError(
            f"Query turn budget exhausted ({decision.new_limit}) without sufficient progress: {decision.reason}"
        )

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_callback:
            self.event_callback({"type": event_type, **payload})


def _notice_messages(runtime_notices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not runtime_notices:
        return []
    lines = ["Queued runtime notices for this turn:"]
    lines.extend(f"- {item.get('message', '')}" for item in runtime_notices)
    return [{"role": "system", "content": "\n".join(lines)}]


def _skill_messages(prefetched_skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not prefetched_skills:
        return []
    lines = ["Relevant available skills (load with `load_skill` if useful):"]
    lines.extend(f"- {item['name']}: {item['description']}" for item in prefetched_skills)
    return [{"role": "system", "content": "\n".join(lines)}]


def _subagent_tool_names(readonly: bool) -> set[str]:
    names = {
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
        names |= {"write_file", "edit_file", "task_create", "task_update"}
    return names
