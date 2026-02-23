import argparse

from rich.console import Console
from rich.prompt import Prompt

from .config import Config, ConfigManager


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Anuris_API_CLI with Attachments")
    parser.add_argument("--api-key", help="API key")
    parser.add_argument("--model", help="Model to use")
    parser.add_argument("--proxy", help="Proxy server address (e.g., socks5://127.0.0.1:7890)")
    parser.add_argument("--base-url", help="API base URL (e.g., https://api.example.com)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--temperature", type=float, help="Temperature parameter for generation (e.g., 0.7)")
    parser.add_argument(
        "--reasoning",
        choices=["on", "off"],
        default=None,
        help="Enable or disable reasoning mode for providers that support it (e.g., DeepSeek thinking mode).",
    )
    parser.add_argument("--system-prompt", help="Custom system prompt")
    parser.add_argument("--system-prompt-file", help="File containing custom system prompt")
    parser.add_argument(
        "--save-config",
        action="store_true",
        help="Save the current settings as default configuration",
    )
    return parser


def resolve_system_prompt_arg(args: argparse.Namespace) -> None:
    """Resolve --system-prompt-file into --system-prompt content when provided."""
    system_prompt = None
    if args.system_prompt_file:
        try:
            with open(args.system_prompt_file, "r", encoding="utf-8") as file_obj:
                system_prompt = file_obj.read()
        except Exception as exc:
            print(f"Error reading system prompt file: {str(exc)}")
    elif args.system_prompt:
        system_prompt = args.system_prompt

    if system_prompt:
        args.system_prompt = system_prompt


def merge_runtime_config(args: argparse.Namespace, config_manager: ConfigManager) -> tuple[Config, dict]:
    """Merge CLI args into persisted config and return both config object and dict."""
    config = config_manager.load_config()
    config_dict = config.to_dict()

    for key, value in vars(args).items():
        if key == "reasoning" and isinstance(value, str):
            value = value == "on"
        if value is not None and key in config_dict:
            config_dict[key] = value

    return Config.from_dict(config_dict), config_dict


def maybe_save_config(args: argparse.Namespace, config_dict: dict, config_manager: ConfigManager) -> None:
    """Persist merged config when --save-config is set."""
    if args.save_config:
        config_manager.save_config(**config_dict)
        print("Configuration saved successfully!")


def ensure_required_config(config: Config, config_manager: ConfigManager) -> Config:
    """Prompt user for required config fields when missing and persist them."""
    console = Console()

    def ask(prompt: str) -> str:
        try:
            return Prompt.ask(prompt)
        except (KeyboardInterrupt, EOFError):
            console.print("\nConfiguration cancelled.", style="yellow")
            raise SystemExit(130) from None

    if not config.base_url:
        base_url = ask("Please enter the API base URL (e.g., https://api.deepseek.com/v1)")
        config_manager.save_config(base_url=base_url)
        config.base_url = base_url
        console.print("Base URL saved successfully!", style="green")

    if not config.model:
        model = ask("Please enter the model name (e.g., deepseek-chat)")
        config_manager.save_config(model=model)
        config.model = model
        console.print("Model saved successfully!", style="green")

    if not config.api_key:
        api_key = ask("Please enter your API key")
        config_manager.save_config(api_key=api_key)
        config.api_key = api_key
        console.print("API key saved successfully!", style="green")

    return config
