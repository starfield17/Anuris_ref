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


def make_chunk(content=None, reasoning_content=None):
    delta = SimpleNamespace()
    if content is not None:
        delta.content = content
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
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


if __name__ == "__main__":
    unittest.main()
