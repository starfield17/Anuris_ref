import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from anuris.config import Config
from anuris.model import ChatModel


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class FailingCompletions:
    def __init__(self, fail_times: int, message: str):
        self.calls = []
        self.fail_times = fail_times
        self.message = message

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise Exception(self.message)
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

    def test_create_completion_skips_extra_body_for_openai(self):
        model, fake = self._build_model(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            reasoning=False,
        )

        model.create_completion(messages=[{"role": "user", "content": "hello"}], stream=False)

        self.assertNotIn("extra_body", fake.calls[0])

    def test_create_completion_skips_deepseek_extra_body_on_openrouter(self):
        model, fake = self._build_model(
            base_url="https://openrouter.ai/api/v1",
            model_name="deepseek/deepseek-r1",
            reasoning=True,
        )

        model.create_completion(messages=[{"role": "user", "content": "hello"}], stream=False)

        self.assertNotIn("extra_body", fake.calls[0])

    def test_normalize_base_url_adds_v1_when_missing(self):
        model, _ = self._build_model(
            base_url="https://api.deepseek.com",
            model_name="deepseek-chat",
            reasoning=False,
        )
        self.assertEqual(model.base_url, "https://api.deepseek.com/v1")

    def test_retries_without_tools_on_invalid_request_shape(self):
        model, _ = self._build_model(
            base_url="https://api.vendor.example/v1",
            model_name="vendor-chat",
            reasoning=False,
        )
        failing = FailingCompletions(
            fail_times=1,
            message="Error 400: unsupported parameter: tools",
        )
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=failing))

        result = model.create_completion(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            tools=[{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(failing.calls), 2)
        self.assertIn("tools", failing.calls[0])
        self.assertNotIn("tools", failing.calls[1])
        self.assertNotIn("tool_choice", failing.calls[1])

    def test_retries_drop_extra_body_before_tools(self):
        model, _ = self._build_model(
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            reasoning=True,
        )
        failing = FailingCompletions(
            fail_times=1,
            message="Error 400: invalid request schema",
        )
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=failing))

        result = model.create_completion(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            tools=[{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(failing.calls), 2)
        self.assertIn("extra_body", failing.calls[0])
        self.assertNotIn("extra_body", failing.calls[1])
        self.assertIn("tools", failing.calls[1])

    def test_retries_without_temperature_after_multiple_invalid_errors(self):
        model, _ = self._build_model(
            base_url="https://api.vendor.example/v1",
            model_name="vendor-chat",
            reasoning=False,
        )
        failing = FailingCompletions(
            fail_times=2,
            message="Error 400: invalid params",
        )
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=failing))

        result = model.create_completion(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            tools=[{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(failing.calls), 3)
        self.assertIn("temperature", failing.calls[1])
        self.assertNotIn("temperature", failing.calls[2])

    def test_does_not_retry_on_auth_error(self):
        model, _ = self._build_model(
            base_url="https://api.vendor.example/v1",
            model_name="vendor-chat",
            reasoning=False,
        )
        failing = FailingCompletions(
            fail_times=9,
            message="401 unauthorized: invalid api key",
        )
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=failing))

        with self.assertRaises(Exception):
            model.create_completion(
                messages=[{"role": "user", "content": "hello"}],
                stream=False,
                tools=[{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
                tool_choice="auto",
            )
        self.assertEqual(len(failing.calls), 1)


class ChatModelProxyTests(unittest.TestCase):
    @patch("anuris.model.OpenAI")
    @patch("anuris.model.httpx.Client")
    @patch("anuris.model.SyncProxyTransport.from_url")
    def test_config_socks_proxy_normalizes_socks_scheme(self, mock_from_url, mock_httpx_client, mock_openai):
        transport = object()
        http_client = object()
        mock_from_url.return_value = transport
        mock_httpx_client.return_value = http_client

        config = Config(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="demo",
            proxy="socks://127.0.0.1:8990",
        )

        ChatModel(config)

        mock_from_url.assert_called_once_with("socks5://127.0.0.1:8990")
        mock_httpx_client.assert_called_once()
        _, kwargs = mock_httpx_client.call_args
        self.assertIs(kwargs.get("transport"), transport)
        self.assertFalse(kwargs.get("trust_env", True))

        mock_openai.assert_called_once()
        self.assertIs(mock_openai.call_args.kwargs.get("http_client"), http_client)

    @patch.dict(os.environ, {"ALL_PROXY": "socks://127.0.0.1:8990"}, clear=True)
    @patch("anuris.model.OpenAI")
    @patch("anuris.model.httpx.Client")
    @patch("anuris.model.SyncProxyTransport.from_url")
    def test_env_socks_proxy_used_when_config_proxy_empty(
        self, mock_from_url, mock_httpx_client, mock_openai
    ):
        transport = object()
        http_client = object()
        mock_from_url.return_value = transport
        mock_httpx_client.return_value = http_client

        config = Config(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="demo",
            proxy="",
        )

        ChatModel(config)

        mock_from_url.assert_called_once_with("socks5://127.0.0.1:8990")
        _, kwargs = mock_httpx_client.call_args
        self.assertIs(kwargs.get("transport"), transport)
        self.assertFalse(kwargs.get("trust_env", True))
        self.assertIs(mock_openai.call_args.kwargs.get("http_client"), http_client)

    @patch.dict(os.environ, {"ALL_PROXY": "socks://127.0.0.1:8990"}, clear=True)
    @patch("anuris.model.OpenAI")
    @patch("anuris.model.httpx.Client")
    @patch("anuris.model.SyncProxyTransport.from_url")
    def test_config_proxy_overrides_env_proxy(self, mock_from_url, mock_httpx_client, mock_openai):
        http_client = object()
        mock_httpx_client.return_value = http_client

        config = Config(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="demo",
            proxy="http://127.0.0.1:8080",
        )

        ChatModel(config)

        mock_from_url.assert_not_called()
        mock_httpx_client.assert_called_once_with(proxy="http://127.0.0.1:8080", trust_env=False)
        self.assertIs(mock_openai.call_args.kwargs.get("http_client"), http_client)

    @patch.dict(
        os.environ,
        {"ALL_PROXY": "socks://127.0.0.1:8990", "NO_PROXY": "api.example.com"},
        clear=True,
    )
    @patch("anuris.model.OpenAI")
    @patch("anuris.model.httpx.Client")
    @patch("anuris.model.SyncProxyTransport.from_url")
    def test_no_proxy_disables_system_proxy(self, mock_from_url, mock_httpx_client, mock_openai):
        http_client = object()
        mock_httpx_client.return_value = http_client

        config = Config(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="demo",
            proxy="",
        )

        ChatModel(config)

        mock_from_url.assert_not_called()
        mock_httpx_client.assert_called_once_with(trust_env=False)
        self.assertIs(mock_openai.call_args.kwargs.get("http_client"), http_client)


if __name__ == "__main__":
    unittest.main()
