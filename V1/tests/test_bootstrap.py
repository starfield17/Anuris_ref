import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anuris.bootstrap import (
    build_arg_parser,
    ensure_required_config,
    maybe_save_config,
    merge_runtime_config,
    resolve_system_prompt_arg,
)
from anuris.config import Config


class FakeConfigManager:
    def __init__(self, config: Config):
        self._config = config
        self.saved_calls = []

    def load_config(self) -> Config:
        return self._config

    def save_config(self, **kwargs) -> None:
        self.saved_calls.append(kwargs)
        for key, value in kwargs.items():
            setattr(self._config, key, value)


class BootstrapTests(unittest.TestCase):
    def test_build_arg_parser_parses_known_args(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--model", "demo-model", "--debug"])
        self.assertEqual(args.model, "demo-model")
        self.assertTrue(args.debug)

    def test_resolve_system_prompt_arg_from_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            prompt_file = Path(tmp_dir) / "prompt.md"
            prompt_file.write_text("system prompt from file", encoding="utf-8")
            args = argparse.Namespace(system_prompt_file=str(prompt_file), system_prompt=None)

            resolve_system_prompt_arg(args)

            self.assertEqual(args.system_prompt, "system prompt from file")

    def test_resolve_system_prompt_arg_prefers_inline_when_no_file(self):
        args = argparse.Namespace(system_prompt_file=None, system_prompt="inline prompt")

        resolve_system_prompt_arg(args)

        self.assertEqual(args.system_prompt, "inline prompt")

    def test_merge_runtime_config_applies_cli_overrides(self):
        saved_config = Config(api_key="saved-key", model="saved-model", temperature=0.1)
        manager = FakeConfigManager(saved_config)
        args = argparse.Namespace(
            api_key=None,
            model="cli-model",
            proxy=None,
            base_url="https://api.example.com/v1",
            debug=False,
            temperature=0.8,
            system_prompt=None,
            system_prompt_file=None,
            save_config=False,
        )

        merged_config, config_dict = merge_runtime_config(args, manager)

        self.assertEqual(merged_config.model, "cli-model")
        self.assertEqual(merged_config.base_url, "https://api.example.com/v1")
        self.assertEqual(merged_config.temperature, 0.8)
        self.assertEqual(config_dict["api_key"], "saved-key")

    def test_maybe_save_config_only_when_flag_set(self):
        manager = FakeConfigManager(Config())
        args_false = argparse.Namespace(save_config=False)
        args_true = argparse.Namespace(save_config=True)
        sample_dict = {"model": "demo"}

        maybe_save_config(args_false, sample_dict, manager)
        self.assertEqual(manager.saved_calls, [])

        with patch("builtins.print"):
            maybe_save_config(args_true, sample_dict, manager)
        self.assertEqual(manager.saved_calls, [sample_dict])

    @patch("anuris.bootstrap.Prompt.ask", side_effect=["https://api.example.com/v1", "gpt-test", "key-test"])
    @patch("anuris.bootstrap.Console")
    def test_ensure_required_config_prompts_and_persists_when_missing(self, mock_console_cls, mock_prompt_ask):
        config = Config(api_key="", model="", base_url="")
        manager = FakeConfigManager(config)

        ensured = ensure_required_config(config, manager)

        self.assertEqual(ensured.base_url, "https://api.example.com/v1")
        self.assertEqual(ensured.model, "gpt-test")
        self.assertEqual(ensured.api_key, "key-test")
        self.assertEqual(
            manager.saved_calls,
            [
                {"base_url": "https://api.example.com/v1"},
                {"model": "gpt-test"},
                {"api_key": "key-test"},
            ],
        )
        self.assertEqual(mock_prompt_ask.call_count, 3)
        self.assertEqual(mock_console_cls.call_count, 3)

    @patch("anuris.bootstrap.Prompt.ask")
    def test_ensure_required_config_skips_prompts_when_values_present(self, mock_prompt_ask):
        config = Config(api_key="k", model="m", base_url="https://api.example.com/v1")
        manager = FakeConfigManager(config)

        ensured = ensure_required_config(config, manager)

        self.assertEqual(ensured.api_key, "k")
        self.assertEqual(ensured.model, "m")
        self.assertEqual(ensured.base_url, "https://api.example.com/v1")
        self.assertEqual(manager.saved_calls, [])
        mock_prompt_ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
