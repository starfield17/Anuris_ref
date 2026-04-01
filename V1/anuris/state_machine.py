from enum import Enum, auto
from pathlib import Path
from typing import Optional

from rich.prompt import Prompt

from .config import Config
from .config import ConfigManager
from .session import ChatSession
from .ui import ChatUI


class ChatState(Enum):
    IDLE = auto()
    RUNNING = auto()
    EXITING = auto()


class ChatStateMachine:
    """Thin TTY shell around the refactored ChatSession."""

    def __init__(self, config: Config, ui: ChatUI, workspace_root: Optional[Path] = None, config_manager: Optional[ConfigManager] = None):
        self.config = config
        self.ui = ui
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.session = ChatSession(config, ui, workspace_root=self.workspace_root, config_manager=config_manager)
        self.current_state = ChatState.IDLE

    def run(self) -> None:
        while self.current_state != ChatState.EXITING:
            if self.current_state == ChatState.IDLE:
                self.ui.display_welcome(self.config.model)
                self.current_state = ChatState.RUNNING
                continue

            if self.session.attachment_manager.attachments:
                if hasattr(self.ui, "display_activity_event"):
                    self.ui.display_activity_event("context", "pending attachments", tone="info")
                else:
                    self.ui.display_message("\n[Pending attachments]", style="cyan")
                self.ui.display_attachments(self.session.attachment_manager.list_attachments())

            user_input = self.ui.display_prompt()
            if not user_input:
                continue

            if user_input.lower() in {"q", "quit", "exit"}:
                confirm = Prompt.ask("Are you sure you want to quit? (y/n)", default="n").strip().lower()
                if confirm == "y":
                    self.ui.display_message("Goodbye!", style="yellow")
                    self.current_state = ChatState.EXITING
                continue

            try:
                self.session.handle_input(user_input)
            except Exception as exc:
                self.ui.display_message(f"Error: {exc}", style="red")
