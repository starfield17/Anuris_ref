from dataclasses import dataclass
from typing import Any

from .ui import ChatUI


@dataclass
class StreamResult:
    """Processed output from a streaming completion response."""

    full_response: str
    reasoning_content: str
    interrupted: bool


class StreamRenderer:
    """Renders and parses streaming deltas into assistant output and reasoning."""

    def __init__(self, ui: ChatUI):
        self.ui = ui

    def process(self, response_stream: Any) -> StreamResult:
        full_response = ""
        reasoning_content = ""
        is_reasoning = False
        is_first_content = True
        in_think_tag = False
        buffered_content = ""

        try:
            for chunk in response_stream:
                delta = chunk.choices[0].delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    content = delta.reasoning_content
                    if not is_reasoning:
                        self.ui.display_message("\n[Reasoning Chain]", style="bold yellow")
                        is_reasoning = True
                    reasoning_content += content
                    self.ui.display_message(content, end="", flush=True)

                if hasattr(delta, "content") and delta.content:
                    content = delta.content
                    buffered_content += content

                    if not in_think_tag and "<think>" in buffered_content:
                        tag_pos = buffered_content.find("<think>")
                        pre_tag_content = buffered_content[:tag_pos]

                        if pre_tag_content:
                            if is_reasoning:
                                self.ui.display_separator()
                                is_reasoning = False
                                is_first_content = True

                            if is_first_content and not full_response:
                                self.ui.display_message("\nAnuris: ", style="bold blue", end="")
                                is_first_content = False

                            full_response += pre_tag_content
                            self.ui.display_message(pre_tag_content, end="", flush=True)

                        in_think_tag = True
                        if not is_reasoning:
                            self.ui.display_message("\n[Reasoning Chain]", style="bold yellow")
                            is_reasoning = True

                        think_content = buffered_content[tag_pos + 7 :]
                        reasoning_content += think_content
                        self.ui.display_message(think_content, end="", flush=True)
                        buffered_content = think_content

                    elif in_think_tag and "</think>" in buffered_content:
                        tag_pos = buffered_content.find("</think>")
                        think_part = buffered_content[:tag_pos]

                        if think_part:
                            reasoning_content += think_part
                            self.ui.display_message(think_part, end="", flush=True)

                        in_think_tag = False
                        is_reasoning = True

                        post_tag_content = buffered_content[tag_pos + 8 :]

                        if post_tag_content:
                            if is_reasoning:
                                self.ui.display_separator()
                                is_reasoning = False

                            if is_first_content and not full_response:
                                self.ui.display_message("\nAnuris: ", style="bold blue", end="")
                                is_first_content = False

                            full_response += post_tag_content
                            self.ui.display_message(post_tag_content, end="", flush=True)

                        buffered_content = post_tag_content

                    elif not in_think_tag:
                        if "<think>" not in buffered_content:
                            if is_reasoning:
                                self.ui.display_separator()
                                is_reasoning = False
                                is_first_content = True

                            if is_first_content and not full_response:
                                self.ui.display_message("\nAnuris: ", style="bold blue", end="")
                                is_first_content = False

                            full_response += content
                            self.ui.display_message(content, end="", flush=True)
                            buffered_content = ""

                    elif in_think_tag and "</think>" not in buffered_content:
                        reasoning_content += content
                        self.ui.display_message(content, end="", flush=True)
                        buffered_content = ""

            if buffered_content and not in_think_tag:
                if is_reasoning:
                    self.ui.display_separator()

                if is_first_content and not full_response:
                    self.ui.display_message("\nAnuris: ", style="bold blue", end="")

                full_response += buffered_content
                self.ui.display_message(buffered_content, end="", flush=True)

            return StreamResult(
                full_response=full_response,
                reasoning_content=reasoning_content,
                interrupted=False,
            )

        except KeyboardInterrupt:
            return StreamResult(
                full_response=full_response,
                reasoning_content=reasoning_content,
                interrupted=True,
            )
