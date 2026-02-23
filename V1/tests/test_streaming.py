import unittest
from types import SimpleNamespace

from anuris.streaming import StreamRenderer


class FakeUI:
    def __init__(self):
        self.messages = []
        self.separators = 0

    def display_message(self, content, **kwargs):
        self.messages.append(content)

    def display_separator(self):
        self.separators += 1


def make_chunk(content=None, reasoning_content=None, reasoning_details=None):
    delta = SimpleNamespace()
    if content is not None:
        delta.content = content
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    if reasoning_details is not None:
        delta.reasoning_details = reasoning_details
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


class StreamRendererTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUI()
        self.renderer = StreamRenderer(self.ui)

    def test_collects_reasoning_and_content(self):
        stream = [
            make_chunk(reasoning_content="thinking..."),
            make_chunk(content="Hello "),
            make_chunk(content="World"),
        ]

        result = self.renderer.process(stream)

        self.assertFalse(result.interrupted)
        self.assertEqual(result.reasoning_content, "thinking...")
        self.assertEqual(result.full_response, "Hello World")

    def test_routes_think_tag_content_to_reasoning(self):
        stream = [
            make_chunk(content="Hello "),
            make_chunk(content="<think>"),
            make_chunk(content="secret"),
            make_chunk(content="</think>"),
            make_chunk(content="World"),
        ]

        result = self.renderer.process(stream)

        self.assertFalse(result.interrupted)
        self.assertEqual(result.reasoning_content, "secret")
        self.assertEqual(result.full_response, "Hello World")

    def test_returns_partial_output_when_interrupted(self):
        def interrupted_stream():
            yield make_chunk(content="Partial")
            raise KeyboardInterrupt

        result = self.renderer.process(interrupted_stream())

        self.assertTrue(result.interrupted)
        self.assertEqual(result.full_response, "Partial")

    def test_collects_incremental_reasoning_details(self):
        stream = [
            make_chunk(reasoning_details=[{"text": "I think"}]),
            make_chunk(reasoning_details=[{"text": "I think step by step"}]),
            make_chunk(content="Answer"),
        ]

        result = self.renderer.process(stream)

        self.assertFalse(result.interrupted)
        self.assertEqual(result.reasoning_content, "I think step by step")
        self.assertEqual(result.full_response, "Answer")

    def test_supports_anthropic_content_block_events(self):
        stream = [
            {"type": "content_block_start", "content_block": {"type": "thinking", "thinking": "Plan"}},
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": " first"}},
            {"type": "content_block_start", "content_block": {"type": "text", "text": "Hello "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "World"}},
        ]

        result = self.renderer.process(stream)

        self.assertFalse(result.interrupted)
        self.assertEqual(result.reasoning_content, "Plan first")
        self.assertEqual(result.full_response, "Hello World")


if __name__ == "__main__":
    unittest.main()
