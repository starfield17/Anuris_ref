import tempfile
import unittest
from pathlib import Path

from rich.panel import Panel

from anuris.attachments import AttachmentManager
from anuris.commands import CommandDispatcher
from anuris.history import ChatHistory


class FakeUI:
    def __init__(self):
        self.messages = []
        self.attachments_views = []

    def display_message(self, content, **kwargs):
        self.messages.append(content)

    def display_attachments(self, attachments):
        self.attachments_views.append(attachments)


class CommandDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.history = ChatHistory(system_prompt="test")
        self.attachment_manager = AttachmentManager()
        self.ui = FakeUI()
        self.dispatcher = CommandDispatcher(self.history, self.attachment_manager, self.ui)

    def test_unknown_command_returns_false(self):
        self.assertFalse(self.dispatcher.execute("not_exist", ""))

    def test_attach_list_detach_cycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_file = Path(tmp_dir) / "demo.txt"
            sample_file.write_text("demo content", encoding="utf-8")

            self.assertTrue(self.dispatcher.execute("attach", str(sample_file)))
            self.assertEqual(len(self.attachment_manager.attachments), 1)

            self.assertTrue(self.dispatcher.execute("files", ""))
            self.assertEqual(len(self.ui.attachments_views), 1)
            self.assertEqual(self.ui.attachments_views[0][0]["name"], "demo.txt")

            self.assertTrue(self.dispatcher.execute("detach", "0"))
            self.assertEqual(len(self.attachment_manager.attachments), 0)

    def test_clear_resets_history_and_attachments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_file = Path(tmp_dir) / "demo.txt"
            sample_file.write_text("demo content", encoding="utf-8")
            self.attachment_manager.add_attachment(str(sample_file))

        self.history.add_message("user", "hello")
        self.assertEqual(len(self.history.messages), 2)
        self.assertEqual(len(self.attachment_manager.attachments), 1)

        self.assertTrue(self.dispatcher.execute("clear", ""))

        self.assertEqual(len(self.history.messages), 1)
        self.assertEqual(self.history.messages[0]["role"], "system")
        self.assertEqual(len(self.attachment_manager.attachments), 0)

    def test_save_and_load_history(self):
        self.history.add_message("user", "hello")
        self.history.add_message("assistant", "world")

        with tempfile.TemporaryDirectory() as tmp_dir:
            history_file = Path(tmp_dir) / "chat_history.json"

            self.assertTrue(self.dispatcher.execute("save", str(history_file)))
            self.history.clear()
            self.assertEqual(len(self.history.messages), 1)

            self.assertTrue(self.dispatcher.execute("load", str(history_file)))
            self.assertGreater(len(self.history.messages), 1)
            self.assertEqual(self.history.messages[1]["content"], "hello")

    def test_help_renders_panel(self):
        self.assertTrue(self.dispatcher.execute("help", ""))
        self.assertTrue(any(isinstance(message, Panel) for message in self.ui.messages))

    def test_extra_handler_can_be_registered(self):
        calls = []
        dispatcher = CommandDispatcher(
            self.history,
            self.attachment_manager,
            self.ui,
            extra_handlers={"agent": lambda args: calls.append(args)},
        )

        self.assertTrue(dispatcher.execute("agent", "on"))
        self.assertEqual(calls, ["on"])


if __name__ == "__main__":
    unittest.main()
