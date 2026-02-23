from pathlib import Path

from .bootstrap import (
    build_arg_parser,
    ensure_required_config,
    maybe_save_config,
    merge_runtime_config,
    resolve_system_prompt_arg,
)
from .config import ConfigManager
from .state_machine import ChatStateMachine
from .ui import ChatUI


def main() -> None:
    """Main entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()

    resolve_system_prompt_arg(args)

    config_manager = ConfigManager()
    config, config_dict = merge_runtime_config(args, config_manager)
    maybe_save_config(args, config_dict, config_manager)
    config = ensure_required_config(config, config_manager)

    ui = ChatUI()
    app_workspace = Path(__file__).resolve().parent.parent
    chat_app = ChatStateMachine(config, ui, workspace_root=app_workspace)
    chat_app.run()
