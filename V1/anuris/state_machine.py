from enum import Enum, auto

from rich.prompt import Prompt

from .attachments import AttachmentManager
from .commands import CommandDispatcher
from .config import Config
from .history import ChatHistory
from .model import ChatModel
from .prompts import prompt_manager
from .streaming import StreamRenderer
from .ui import ChatUI


class ChatState(Enum):
    """Enum representing the possible states of the chat application."""

    IDLE = auto()
    WAITING_FOR_USER = auto()
    PROCESSING = auto()
    RESPONDING = auto()
    ERROR = auto()
    EXITING = auto()


class ChatStateMachine:
    """Main application using state machine pattern."""

    def __init__(self, config: Config, ui: ChatUI):
        self.config = config
        self.ui = ui
        resolved_system_prompt = prompt_manager.resolve_prompt_source(config.system_prompt)
        self.history = ChatHistory(system_prompt=resolved_system_prompt)
        self.model = ChatModel(config)
        self.attachment_manager = AttachmentManager()
        self.command_dispatcher = CommandDispatcher(self.history, self.attachment_manager, self.ui)
        self.stream_renderer = StreamRenderer(self.ui)
        self.current_state = ChatState.IDLE

        self.transitions = {
            ChatState.IDLE: self._handle_idle_state,
            ChatState.WAITING_FOR_USER: self._handle_waiting_state,
            ChatState.PROCESSING: self._handle_processing_state,
            ChatState.RESPONDING: self._handle_responding_state,
            ChatState.ERROR: self._handle_error_state,
            ChatState.EXITING: lambda: ChatState.EXITING,
        }

        self.context = {
            "user_input": "",
            "error_message": "",
            "is_command": False,
            "response_text": "",
            "reasoning_text": "",
        }

    def run(self) -> None:
        """Run the state machine until exit state is reached."""
        while self.current_state != ChatState.EXITING:
            next_state_handler = self.transitions.get(self.current_state)
            if next_state_handler:
                try:
                    self.current_state = next_state_handler()
                except Exception as exc:
                    self.context["error_message"] = str(exc)
                    self.current_state = ChatState.ERROR
            else:
                self.context["error_message"] = f"No handler for state: {self.current_state}"
                self.current_state = ChatState.ERROR

    def _handle_idle_state(self) -> ChatState:
        """Initialize the chat application."""
        self.ui.display_welcome(self.config.model)
        return ChatState.WAITING_FOR_USER

    def _handle_waiting_state(self) -> ChatState:
        """Wait for and process user input."""
        if self.attachment_manager.attachments:
            self.ui.display_message("\n[Current attachments]", style="cyan")
            self.ui.display_attachments(self.attachment_manager.list_attachments())

        user_input = self.ui.display_prompt()

        if not user_input:
            return ChatState.WAITING_FOR_USER

        if user_input.lower() in ["q", "quit", "exit"]:
            user_choice = Prompt.ask("Are you sure you want to quit? (y/n)", default="n").strip().lower()
            if user_choice == "y":
                self.ui.display_message("\nGoodbye!", style="yellow")
                return ChatState.EXITING
            return ChatState.WAITING_FOR_USER

        self.context["user_input"] = user_input
        self.context["is_command"] = user_input.startswith("/")

        return ChatState.PROCESSING

    def _handle_processing_state(self) -> ChatState:
        """Process user input."""
        if self.context["is_command"]:
            cmd_parts = self.context["user_input"][1:].split(maxsplit=1)
            cmd_name = cmd_parts[0]
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

            if self.command_dispatcher.execute(cmd_name, cmd_args):
                return ChatState.WAITING_FOR_USER

            self.context["error_message"] = f"Unknown command: {cmd_name}"
            return ChatState.ERROR

        return ChatState.RESPONDING

    def _handle_responding_state(self) -> ChatState:
        """Handle API response with attachments."""
        try:
            messages = self.history.messages + [{"role": "user", "content": self.context["user_input"]}]
            api_attachments = (
                self.attachment_manager.prepare_for_api() if self.attachment_manager.attachments else None
            )

            response_stream = self.model.get_response(messages, api_attachments)
            current_attachments = self.attachment_manager.attachments.copy()
            self.attachment_manager.clear_attachments()

            stream_result = self.stream_renderer.process(response_stream)

            if stream_result.interrupted:
                self.ui.display_message("\n[Response interrupted by user]", style="yellow")
                if stream_result.full_response:
                    self.history.add_message(
                        "user",
                        self.context["user_input"],
                        attachments=current_attachments,
                    )
                    self.history.add_message(
                        "assistant",
                        stream_result.full_response,
                        stream_result.reasoning_content,
                    )
                return ChatState.WAITING_FOR_USER

            if stream_result.full_response:
                self.history.add_message(
                    "user",
                    self.context["user_input"],
                    attachments=current_attachments,
                )
                self.history.add_message(
                    "assistant",
                    stream_result.full_response,
                    stream_result.reasoning_content,
                )
                self.ui.display_message("")
            else:
                raise Exception("No content in response chunks")

            return ChatState.WAITING_FOR_USER

        except Exception as exc:
            self.context["error_message"] = str(exc)
            return ChatState.ERROR

    def _handle_error_state(self) -> ChatState:
        """Handle error state."""
        self.ui.display_message(f"\nError: {self.context['error_message']}", style="red")
        return ChatState.WAITING_FOR_USER
