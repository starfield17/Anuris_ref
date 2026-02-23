import glob
import os
from typing import Callable, Dict, Optional

from rich.panel import Panel

from .attachments import AttachmentManager
from .history import ChatHistory
from .ui import ChatUI

HELP_TEXT = """[bold cyan]Anuris_API_CLI Help[/bold cyan]

[bold yellow]Available Commands:[/bold yellow]
[green]/clear[/green]
    Clear all chat history and attachments
    Usage: /clear

[green]/save [filename][/green]
    Save current chat history to a JSON file
    Usage: /save [filename]
    Default filename: chat_history.json
    Example: /save my_chat.json

[green]/load [filename][/green]
    Load chat history from a JSON file
    Usage: /load [filename]
    Default filename: chat_history.json
    Example: /load my_chat.json

[green]/attach <file_path> [file_path2 ...][/green]
    Attach one or more files to your next message
    Usage: /attach <file_path> [file_path2 ...]
    Supports glob patterns (e.g., *.txt)
    Example: /attach image.jpg document.pdf
    Example: /attach ~/Downloads/*.png

[green]/detach [index][/green]
    Remove attachment(s)
    Usage: /detach [index]
    Without index: removes all attachments
    With index: removes specific attachment
    Example: /detach 0

[green]/files[/green]
    List all current attachments
    Usage: /files

[green]/help[/green]
    Display this help message
    Usage: /help

[green]/agent [on|off|status][/green]
    Toggle or inspect agent mode
    Usage: /agent [on|off|status]

[bold yellow]Keyboard Shortcuts:[/bold yellow]
[blue]Enter[/blue]        Start a new line in your message
[blue]Ctrl+D[/blue]       Send your message
[blue]Ctrl+V[/blue]       Paste text from clipboard
[blue]Ctrl+Z[/blue]       Undo last text change
[blue]Ctrl+Y[/blue]       Redo last undone change
[blue]Up/Down[/blue]      Navigate through command history

[bold yellow]Attachment Support:[/bold yellow]
- Images: .jpg, .jpeg, .png, .gif, .webp, .bmp
- Text files: .txt, .md, .json, .csv, .xml, .yaml, .yml
- Documents: .pdf, .doc, .docx, .xls, .xlsx, .ppt, .pptx
- Max file size: 20MB per file
- Multiple attachments supported

[bold yellow]Tips:[/bold yellow]
- Attachments are automatically cleared after sending a message
- Use /files to see current attachments before sending
- Chat history includes attachment information
"""


class CommandDispatcher:
    """Dispatches slash commands to handlers."""

    def __init__(
        self,
        history: ChatHistory,
        attachment_manager: AttachmentManager,
        ui: ChatUI,
        extra_handlers: Optional[Dict[str, Callable[[str], None]]] = None,
    ):
        self.history = history
        self.attachment_manager = attachment_manager
        self.ui = ui
        self.handlers: Dict[str, Callable[[str], None]] = {
            "clear": self._handle_clear,
            "save": self._handle_save,
            "load": self._handle_load,
            "help": self._handle_help,
            "attach": self._handle_attach,
            "detach": self._handle_detach,
            "files": self._handle_files,
        }
        if extra_handlers:
            self.handlers.update(extra_handlers)

    def execute(self, command_name: str, command_args: str) -> bool:
        """Execute a command by name. Returns False when command is unknown."""
        handler = self.handlers.get(command_name)
        if not handler:
            return False
        handler(command_args)
        return True

    def _handle_clear(self, args: str) -> None:
        self.history.clear()
        self.attachment_manager.clear_attachments()
        self.ui.display_message("Chat history and attachments cleared", style="yellow")

    def _handle_save(self, args: str) -> None:
        filename = args if args else "chat_history.json"
        self.history.save(filename)
        self.ui.display_message(f"Chat saved to {filename}", style="green")

    def _handle_load(self, args: str) -> None:
        filename = args if args else "chat_history.json"
        if self.history.load(filename):
            self.ui.display_message("Chat history loaded", style="green")
        else:
            self.ui.display_message(f"File not found: {filename}", style="red")

    def _handle_attach(self, args: str) -> None:
        if not args:
            self.ui.display_message("Usage: /attach <file_path> [file_path2 ...]", style="yellow")
            return

        file_paths = args.split()
        for file_path in file_paths:
            expanded_paths = glob.glob(os.path.expanduser(file_path))
            if not expanded_paths:
                self.ui.display_message(f"No files found matching: {file_path}", style="red")
                continue

            for path in expanded_paths:
                success, message = self.attachment_manager.add_attachment(path)
                self.ui.display_message(message, style="green" if success else "red")

    def _handle_detach(self, args: str) -> None:
        if not args:
            self.attachment_manager.clear_attachments()
            self.ui.display_message("All attachments removed", style="yellow")
            return

        try:
            index = int(args)
            success, message = self.attachment_manager.remove_attachment(index)
            self.ui.display_message(message, style="green" if success else "red")
        except ValueError:
            self.ui.display_message("Invalid index. Use /files to see attachment indices", style="red")

    def _handle_files(self, args: str) -> None:
        attachments = self.attachment_manager.list_attachments()
        if attachments:
            self.ui.display_attachments(attachments)
        else:
            self.ui.display_message("No attachments", style="yellow")

    def _handle_help(self, args: str) -> None:
        self.ui.display_message(Panel.fit(HELP_TEXT, border_style="blue"))
