import os
import shutil
from typing import Any, Dict, List

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class ChatUI:
    """User interface components managed declaratively."""

    def __init__(self):
        self.console = Console()
        self.separator_pattern = "*-"
        self.session = self._create_prompt_session()

    def _create_prompt_session(self) -> PromptSession:
        """Factory method for prompt session with key bindings."""
        key_bindings = KeyBindings()
        undo_stack = []
        redo_stack = []

        @key_bindings.add(Keys.Enter, eager=True)
        def _(event):
            event.current_buffer.validate_and_handle()

        @key_bindings.add(Keys.ControlD)
        def _(event):
            if event.current_buffer.text.strip():
                event.current_buffer.validate_and_handle()

        @key_bindings.add(Keys.ControlV)
        def _(event):
            try:
                import pyperclip

                text = pyperclip.paste()
                undo_stack.append(event.current_buffer.text)
                event.current_buffer.insert_text(text)
            except ImportError:
                self.display_message("pyperclip not installed.", style="red")
            except Exception as exc:
                self.display_message(f"Failed to paste: {str(exc)}", style="red")

        @key_bindings.add("c-z", eager=True)
        def _(event):
            if not undo_stack:
                current_text = event.current_buffer.text
                if current_text.strip():
                    undo_stack.append("")
                    redo_stack.append(current_text)
                    event.current_buffer.text = ""
            elif undo_stack:
                current_text = event.current_buffer.text
                last_state = undo_stack.pop()
                redo_stack.append(current_text)
                event.current_buffer.text = last_state

        @key_bindings.add("c-y", eager=True)
        def _(event):
            if redo_stack:
                current_text = event.current_buffer.text
                next_state = redo_stack.pop()
                undo_stack.append(current_text)
                event.current_buffer.text = next_state

        return PromptSession(
            history=FileHistory(os.path.expanduser("~/.chat_history")),
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=key_bindings,
        )

    def display_separator(self) -> None:
        """Display a visual separator."""
        terminal_width = shutil.get_terminal_size().columns
        repeat_count = terminal_width // len(self.separator_pattern)
        separator = self.separator_pattern * repeat_count
        if len(separator) < terminal_width:
            separator += separator[0]
        self.console.print(f"\n{separator}\n", style="bold yellow")

    def display_prompt(self) -> str:
        """Get user input through prompt."""
        try:
            text = self.session.prompt(
                "User: ",
                multiline=True,
                wrap_lines=True,
            )
            return text.strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def display_message(self, content: str, style: str = None, end: str = "\n", flush: bool = False) -> None:
        """Display a message to the user."""
        if flush:
            print(content, end=end, flush=True)
        else:
            self.console.print(content, style=style, end=end)

    def display_reasoning(self, content: str) -> None:
        """Display reasoning chain."""
        if content and content.strip():
            self.console.print(
                Panel.fit(
                    content,
                    title="[bold yellow]Reasoning Chain[/bold yellow]",
                    border_style="yellow",
                    padding=(1, 2),
                    title_align="left",
                )
            )

    def display_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        """Display attachment list in a table."""
        if not attachments:
            return

        table = Table(title="Attachments", title_style="bold cyan")
        table.add_column("#", style="dim", width=3)
        table.add_column("Name", style="green")
        table.add_column("Type", style="blue")
        table.add_column("Size", style="yellow")

        for attachment in attachments:
            table.add_row(
                str(attachment["index"]),
                attachment["name"],
                attachment["type"],
                attachment["size"],
            )

        self.console.print(table)

    def display_welcome(self, model: str) -> None:
        """Display welcome message with attachment info."""
        welcome_text = f"""
            [cyan]Anuris_API_CLI[/cyan] (Model: [green]{model}[/green])

            [yellow]Enter 'q' or 'exit' or 'quit' to quit[/yellow]

            [bold magenta]Commands:[/bold magenta]
            [blue]- /clear[/blue]    : Clear chat history
            [blue]- /save[/blue]     : Save chat history
            [blue]- /load[/blue]     : Load chat history
            [blue]- /attach[/blue]   : Attach file(s)
            [blue]- /detach[/blue]   : Remove attachment(s)
            [blue]- /files[/blue]    : List attachments
            [blue]- /agent[/blue]    : Toggle agent mode
            [blue]- /todos[/blue]    : Show todo board
            [blue]- /help[/blue]     : Show help

            [bold magenta]Shortcuts:[/bold magenta]
            [green]- Enter[/green]: Send message
            [green]- Ctrl+D[/green]: Send message
            [green]- Ctrl+V[/green]: Paste
            [green]- Ctrl+Z[/green]: Undo
            [green]- Ctrl+Y[/green]: Redo
            [green]- Up/Down[/green]: Navigate history
            """
        self.console.print(
            Panel.fit(
                welcome_text,
                title="[bold red]Welcome[/bold red]",
                border_style="blue",
                padding=(1, 2),
            )
        )
