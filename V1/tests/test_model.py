import unittest
from types import SimpleNamespace

from anuris.config import Config
from anuris.model import ChatModel


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class ChatModelReasoningTests(unittest.TestCase):
    def _build_model(self, base_url: str, model_name: str, reasoning: bool) -> tuple[ChatModel, FakeCompletions]:
        config = Config(
            api_key="test",
            base_url=base_url,
            model=model_name,
            reasoning=reasoning,
        )
        model = ChatModel(config)
        fake = FakeCompletions()
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        return model, fake

    def test_create_completion_sets_thinking_enabled_for_deepseek(self):
        model, fake = self._build_model(
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            reasoning=True,
        )

        model.create_completion(messages=[{"role": "user", "content": "hello"}], stream=False)

        self.assertEqual(fake.calls[0]["extra_body"], {"thinking": {"type": "enabled"}})

    def test_create_completion_sets_thinking_disabled_for_deepseek(self):
        model, fake = self._build_model(
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            reasoning=False,
        )

        model.create_completion(messages=[{"role": "user", "content": "hello"}], stream=False)

        self.assertEqual(fake.calls[0]["extra_body"], {"thinking": {"type": "disabled"}})

    def test_create_completion_skips_extra_body_for_non_deepseek(self):
        model, fake = self._build_model(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            reasoning=False,
        )

        model.create_completion(messages=[{"role": "user", "content": "hello"}], stream=False)

        self.assertNotIn("extra_body", fake.calls[0])


if __name__ == "__main__":
    unittest.main()
