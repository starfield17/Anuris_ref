from pathlib import Path

from .bootstrap import (
    build_arg_parser,
    ensure_required_config,
    maybe_save_config,
    merge_runtime_config,
    resolve_system_prompt_arg,
)
from .config import ConfigManager
from .debug_server import DebugHTTPServer, DebugSessionManager
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

    app_workspace = Path(__file__).resolve().parent.parent
    if args.debug_server:
        debug_dir = Path(args.debug_dir).resolve() if args.debug_dir else app_workspace / ".anuris_debug"
        manager = DebugSessionManager(config, workspace_root=app_workspace, debug_dir=debug_dir)
        server = DebugHTTPServer(manager, host=args.debug_host, port=args.debug_port)
        print(f"Debug server listening on http://{args.debug_host}:{args.debug_port}")
        print(f"Debug artifacts directory: {debug_dir}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDebug server stopped.")
        finally:
            server.shutdown()
        return

    ui = ChatUI()
    chat_app = ChatStateMachine(config, ui, workspace_root=app_workspace)
    chat_app.run()
