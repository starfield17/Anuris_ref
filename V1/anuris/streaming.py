from dataclasses import dataclass
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


class StreamRenderer:
    """Renders and parses streaming deltas into assistant output and reasoning."""

    def __init__(self, ui: ChatUI):
        self.ui = ui

    def process(self, response_stream: Any) -> StreamResult:
        state = _RenderState()

        try:
            for chunk in response_stream:
                delta = chunk.choices[0].delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    self._enter_reasoning_mode(state)
                    self._append_reasoning_text(delta.reasoning_content, state)

                if hasattr(delta, "content") and delta.content:
                    self._process_content_delta(delta.content, state)

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
