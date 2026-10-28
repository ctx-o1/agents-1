"""Focused terminal and prompt checks with fabricated input and no network."""

import io
import json
import os
import unittest
from unittest.mock import patch

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from wcwidth import wcswidth

from src.cli import PromptReader, Terminal, clean


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalUITests(unittest.TestCase):
    def test_streamed_text_preserves_code_and_filters_fragmented_controls(self):
        output = io.StringIO()
        terminal = Terminal(output, color=False)
        code = "```python\n\tprint('" + "x" * 100 + "')\n```"
        for fragment in ("Hello \x1b", "[2J", "world\x1b]0;private", " title\x07\n", code, "\ne", "\u0301"):
            terminal.text(1, fragment)
        terminal.end_text()
        result = output.getvalue()
        self.assertNotIn("agent(-1)", result)
        self.assertIn("Hello world", result)
        self.assertIn(code, result)
        self.assertIn("e\u0301", result)
        self.assertNotIn("private", result)
        self.assertNotIn("\x1b", result)

        # Interactive drafts finish as rendered Markdown once, after clearing
        # the transient display; redirects above retain exact code fences.
        interactive = TTY()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            terminal = Terminal(interactive, color=False)
            terminal.text(1, "**Rendered**\n\n```python\n")
            terminal.text(1, "print('ready')\n```")
            terminal.end_text()
        committed = interactive.getvalue().rsplit("\x1b[?25h", 1)[-1]
        self.assertEqual(committed.count("Rendered"), 1)
        self.assertIn("print('ready')", committed)
        self.assertNotIn("**", committed)
        self.assertNotIn("```", committed)

    def test_narrow_unicode_output_and_rich_edit_preview_stay_truthful(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"COLUMNS": "30"}):
            terminal = Terminal(output, color=False)
            terminal.answer("你" * 20)
            self.assertTrue(all(wcswidth(line) <= 30 for line in output.getvalue().splitlines()))
            self.assertLessEqual(wcswidth(terminal._one("😀" * 20, 12)), 12)
            with patch.dict(os.environ, {"COLUMNS": "48"}):
                self.assertEqual(terminal.width, 48)

        rendered = TTY()
        terminal = Terminal(rendered, color=False)
        with patch.dict(os.environ, {"TERM": "xterm-256color"}), patch("sys.stdin", TTY()), \
             patch.object(terminal.console, "clear") as clear_screen:
            terminal.clear()
            clear_screen.assert_called_once_with(home=True)
            terminal.answer("Padded response.")
        self.assertNotIn("  • response", rendered.getvalue())
        self.assertIn("\n  Padded response.", rendered.getvalue())
        arguments = json.dumps({"path": "config.py", "old_text": "before", "new_text": "after"})
        with patch.dict(os.environ, {"TERM": "xterm-256color"}), \
             patch("src.cli.time.monotonic", side_effect=[10.0, 10.25]):
            terminal.tool_started(1, "edit_file", arguments)
            terminal.tool(1, "edit_file", arguments, '{"error":"File changed before editing"}')
        result = clean(rendered.getvalue())
        self.assertIn("Requested edit", result)
        self.assertIn("-before", result)
        self.assertIn("+after", result)
        self.assertIn("running", result)
        self.assertIn("error", result)
        self.assertIn("0.25s", result)
        self.assertIn("File changed before editing", result)
        self.assertNotIn("applied", result)

    def test_prompt_falls_back_without_constructing_an_editor(self):
        for environment in ({"TERM": "dumb"}, {}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                 patch("sys.stdin", io.StringIO()), patch("builtins.input", return_value="work\nmore") as read, \
                 patch("src.cli.PromptSession") as session:
                terminal = Terminal(TTY(), color=False)
                with patch.object(terminal.console, "clear") as clear_screen:
                    terminal.clear()
                    clear_screen.assert_not_called()
                reader = PromptReader(terminal)
                self.assertEqual(reader.read(), "work\nmore")
                read.assert_called_once()
                session.assert_not_called()

    def test_multiline_paste_and_newline_binding_submit_one_draft(self):
        with create_pipe_input() as pipe, patch("sys.stdin", TTY()), \
             patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            def session_factory(**options):
                return PromptSession(input=pipe, output=DummyOutput(), **options)

            reader = PromptReader(Terminal(TTY(), color=False), session_factory=session_factory)
            # Bracketed paste inserts line breaks without submitting them.
            pipe.send_text("\x1b[200~first\nsecond\x1b[201~\r")
            self.assertEqual(reader.read(), "first\nsecond")
            # F2 inserts a line; Enter submits. Reuse the same in-memory session.
            original_session = reader._session
            pipe.send_text("third\x1bOQfourth\r")
            self.assertEqual(reader.read(), "third\nfourth")
            self.assertIs(reader._session, original_session)
            self.assertIn("first\nsecond", list(reader._session.history.get_strings()))


if __name__ == "__main__":
    unittest.main()
