from dataclasses import dataclass, field
from typing import Any

from .ui import ChatUI


@dataclass
class StreamResult:
    """Processed output from a streaming completion response."""

    full_response: str
    reasoning_content: str
    interrupted: bool


@dataclass
class _RenderState:
    full_response: str = ""
    reasoning_content: str = ""
    is_reasoning: bool = False
    is_first_content: bool = True
    in_think_tag: bool = False
    buffered_content: str = ""
    reasoning_detail_buffers: dict[int, str] = field(default_factory=dict)


class StreamRenderer:
    """Renders and parses streaming deltas into assistant output and reasoning."""

    def __init__(self, ui: ChatUI):
        self.ui = ui

    def process(self, response_stream: Any) -> StreamResult:
        state = _RenderState()

        try:
            for chunk in response_stream:
                delta = self._extract_openai_delta(chunk)
                if delta is not None:
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        self._enter_reasoning_mode(state)
                        self._append_reasoning_text(delta.reasoning_content, state)
                    if hasattr(delta, "reasoning_details") and delta.reasoning_details:
                        self._process_reasoning_details(delta.reasoning_details, state)
                    if hasattr(delta, "content") and delta.content:
                        self._process_content_delta(delta.content, state)
                else:
                    self._process_anthropic_chunk(chunk, state)

            self._flush_buffered_content(state)

            return StreamResult(
                full_response=state.full_response,
                reasoning_content=state.reasoning_content,
                interrupted=False,
            )

        except KeyboardInterrupt:
            return StreamResult(
                full_response=state.full_response,
                reasoning_content=state.reasoning_content,
                interrupted=True,
            )

    def _process_content_delta(self, content: str, state: _RenderState) -> None:
        state.buffered_content += content

        if not state.in_think_tag and "<think>" in state.buffered_content:
            self._handle_think_start(state)
            return

        if state.in_think_tag and "</think>" in state.buffered_content:
            self._handle_think_end(state)
            return

        if not state.in_think_tag and "<think>" not in state.buffered_content:
            self._switch_to_answer_mode(state, reset_first_content=True)
            self._append_answer_text(content, state)
            state.buffered_content = ""
            return

        if state.in_think_tag and "</think>" not in state.buffered_content:
            self._enter_reasoning_mode(state)
            self._append_reasoning_text(content, state)
            state.buffered_content = ""

    def _handle_think_start(self, state: _RenderState) -> None:
        tag_pos = state.buffered_content.find("<think>")
        pre_tag_content = state.buffered_content[:tag_pos]

        if pre_tag_content:
            self._switch_to_answer_mode(state, reset_first_content=True)
            self._append_answer_text(pre_tag_content, state)

        state.in_think_tag = True
        self._enter_reasoning_mode(state)

        think_content = state.buffered_content[tag_pos + 7 :]
        if think_content:
            self._append_reasoning_text(think_content, state)

        state.buffered_content = think_content

    def _handle_think_end(self, state: _RenderState) -> None:
        tag_pos = state.buffered_content.find("</think>")
        think_part = state.buffered_content[:tag_pos]

        if think_part:
            self._enter_reasoning_mode(state)
            self._append_reasoning_text(think_part, state)

        state.in_think_tag = False
        state.is_reasoning = True

        post_tag_content = state.buffered_content[tag_pos + 8 :]

        if post_tag_content:
            self._switch_to_answer_mode(state, reset_first_content=False)
            self._append_answer_text(post_tag_content, state)

        state.buffered_content = post_tag_content

    def _flush_buffered_content(self, state: _RenderState) -> None:
        if state.buffered_content and not state.in_think_tag:
            self._switch_to_answer_mode(state, reset_first_content=False)
            self._append_answer_text(state.buffered_content, state)

    def _enter_reasoning_mode(self, state: _RenderState) -> None:
        if not state.is_reasoning:
            self.ui.display_message("\n[Reasoning Chain]", style="bold yellow")
            state.is_reasoning = True

    def _switch_to_answer_mode(self, state: _RenderState, reset_first_content: bool) -> None:
        if state.is_reasoning:
            self.ui.display_separator()
            state.is_reasoning = False
            if reset_first_content:
                state.is_first_content = True

    def _append_reasoning_text(self, content: str, state: _RenderState) -> None:
        state.reasoning_content += content
        self.ui.display_message(content, end="", flush=True)

    def _append_answer_text(self, content: str, state: _RenderState) -> None:
        if state.is_first_content and not state.full_response:
            self.ui.display_message("\nAnuris: ", style="bold blue", end="")
            state.is_first_content = False

        state.full_response += content
        self.ui.display_message(content, end="", flush=True)

    def _extract_openai_delta(self, chunk: Any) -> Any:
        choices = getattr(chunk, "choices", None)
        if choices:
            first = choices[0]
            return getattr(first, "delta", None)
        if isinstance(chunk, dict):
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices:
                delta = choices[0].get("delta")
                if isinstance(delta, dict):
                    return type("OpenAIDelta", (), delta)()
        return None

    def _process_anthropic_chunk(self, chunk: Any, state: _RenderState) -> None:
        payload = self._to_mapping(chunk)
        if not payload:
            return

        event_type = str(payload.get("type", ""))
        if event_type == "content_block_start":
            content_block = payload.get("content_block", {})
            self._process_anthropic_content_block(content_block, state)
            return
        if event_type == "content_block_delta":
            delta = payload.get("delta", {})
            self._process_anthropic_delta(delta, state)
            return
        if event_type == "message_start":
            message = payload.get("message", {})
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    self._process_anthropic_content_block(block, state)
            return

        # Best effort: some wrappers emit direct Anthropic-like `delta` without event type.
        if "delta" in payload:
            self._process_anthropic_delta(payload.get("delta", {}), state)

    def _process_anthropic_content_block(self, block: Any, state: _RenderState) -> None:
        data = self._to_mapping(block)
        if not data:
            return
        block_type = str(data.get("type", ""))
        if block_type == "text":
            text = str(data.get("text", "") or "")
            if text:
                self._process_content_delta(text, state)
        elif block_type in {"thinking", "redacted_thinking"}:
            thinking = str(data.get("thinking", "") or data.get("text", "") or "")
            if thinking:
                self._enter_reasoning_mode(state)
                self._append_reasoning_text(thinking, state)

    def _process_anthropic_delta(self, delta: Any, state: _RenderState) -> None:
        data = self._to_mapping(delta)
        if not data:
            return
        delta_type = str(data.get("type", ""))
        if delta_type == "text_delta":
            text = str(data.get("text", "") or "")
            if text:
                self._process_content_delta(text, state)
        elif delta_type in {"thinking_delta", "signature_delta"}:
            thinking = str(data.get("thinking", "") or data.get("text", "") or "")
            if thinking:
                self._enter_reasoning_mode(state)
                self._append_reasoning_text(thinking, state)

    @staticmethod
    def _to_mapping(value: Any) -> dict:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {}

    def _process_reasoning_details(self, details: Any, state: _RenderState) -> None:
        for index, detail in enumerate(details):
            text = ""
            if isinstance(detail, dict):
                text = str(detail.get("text", "") or "")
            else:
                text = str(getattr(detail, "text", "") or "")
            if not text:
                continue

            previous = state.reasoning_detail_buffers.get(index, "")
            if text.startswith(previous):
                delta_text = text[len(previous) :]
            else:
                delta_text = text
            state.reasoning_detail_buffers[index] = text

            if delta_text:
                self._enter_reasoning_mode(state)
                self._append_reasoning_text(delta_text, state)
