import argparse

from rich.console import Console
from rich.prompt import Prompt

from .config import Config, ConfigManager
from .state_machine import ChatStateMachine
from .ui import ChatUI


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Anuris_API_CLI with Attachments")
    parser.add_argument("--api-key", help="API key")
    parser.add_argument("--model", help="Model to use")
    parser.add_argument("--proxy", help="Proxy server address (e.g., socks5://127.0.0.1:7890)")
    parser.add_argument("--base-url", help="API base URL (e.g., https://api.example.com)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--temperature", type=float, help="Temperature parameter for generation (e.g., 0.7)")
    parser.add_argument("--system-prompt", help="Custom system prompt")
    parser.add_argument("--system-prompt-file", help="File containing custom system prompt")
    parser.add_argument(
        "--save-config",
        action="store_true",
        help="Save the current settings as default configuration",
    )
    args = parser.parse_args()

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

    config_manager = ConfigManager()
    config = config_manager.load_config()

    config_dict = config.to_dict()
    for key, value in vars(args).items():
        if value is not None and key in config_dict:
            config_dict[key] = value

    config = Config.from_dict(config_dict)

    if args.save_config:
        config_manager.save_config(**config_dict)
        print("Configuration saved successfully!")

    if not config.base_url:
        console = Console()
        base_url = Prompt.ask("Please enter the API base URL (e.g., https://api.deepseek.com/v1)")
        config_manager.save_config(base_url=base_url)
        config.base_url = base_url
        console.print("Base URL saved successfully!", style="green")

    if not config.model:
        console = Console()
        model = Prompt.ask("Please enter the model name (e.g., deepseek-chat)")
        config_manager.save_config(model=model)
        config.model = model
        console.print("Model saved successfully!", style="green")

    if not config.api_key:
        console = Console()
        api_key = Prompt.ask("Please enter your API key")
        config_manager.save_config(api_key=api_key)
        config.api_key = api_key
        console.print("API key saved successfully!", style="green")

    ui = ChatUI()
    chat_app = ChatStateMachine(config, ui)
    chat_app.run()
