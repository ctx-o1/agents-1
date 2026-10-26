"""Terminal checks and CLI integration using fabricated responses only."""

import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import agent
import chat
import storage
from cli import Terminal
from tests.test_agent import FakeClient, reply


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def test_redirected_text_is_plain_and_terminal_controls_are_removed(self):
        stream = io.StringIO()
        terminal = Terminal(stream=stream)
        terminal.answer("Visible\x1b[2J text\x1b]0;hostile title\x07\r\b\nNext\tline")
        output = stream.getvalue()
        for character in ("\x1b", "\x07", "\r", "\b"):
            self.assertNotIn(character, output)
        self.assertIn("Visible", output)
        self.assertIn("Next", output)
        self.assertNotIn("hostile title", output)

    def test_color_is_optional_and_no_color_and_dumb_terminal_are_respected(self):
        for environment in ({"NO_COLOR": "1"}, {"TERM": "dumb"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                stream = FakeTTY()
                Terminal(stream).answer("Hello")
                self.assertNotIn("\x1b", stream.getvalue())
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            stream = FakeTTY()
            Terminal(stream).answer("Hello")
            self.assertIn("\x1b[", stream.getvalue())
            plain = FakeTTY()
            Terminal(plain, color=False).answer("Hello")
            self.assertNotIn("\x1b", plain.getvalue())

    def test_code_indentation_tables_and_nested_fences_survive_narrow_output(self):
        long_command = "python script.py --value " + "x" * 100
        code = "\tif ready:\n        " + long_command
        nested = "````markdown\n```python\n" + code + "\n```\n````"
        table = "| Name | Description |\n| --- | --- |\n| agent | " + "long " * 20 + "|"
        stream = io.StringIO()
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            Terminal(stream).answer("Some prose.\n\n" + nested + "\n\n" + table)
        output = stream.getvalue()
        self.assertIn(nested, output)
        self.assertIn(table, output)

    def test_default_tool_activity_omits_content_and_keeps_status_visible(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            terminal = Terminal(stream)
            terminal.tool(1, "write_file", json.dumps({"path": "a" * 150, "content": "PRIVATE_FILE"}), json.dumps({"result": "PRIVATE_RESULT"}))
            terminal.tool(2, "memory", json.dumps({"action": "add", "target": "user", "content": "PRIVATE_FACT"}), json.dumps({"error": "Memory full"}))
        output = stream.getvalue()
        self.assertIn("ok", output)
        self.assertIn("error", output)
        self.assertIn("write_file", output)
        for private in ("PRIVATE_FILE", "PRIVATE_RESULT", "PRIVATE_FACT"):
            self.assertNotIn(private, output)
        self.assertNotIn('{"result"', output)

    def test_verbose_details_are_bounded_and_history_previews_are_marked(self):
        stream = io.StringIO()
        terminal = Terminal(stream)
        terminal.tool(1, "read_file", '{"path":"notes.txt"}', json.dumps({"result": "x" * 8000}), verbose=True)
        self.assertLess(len(stream.getvalue()), 4000)
        self.assertIn("truncated", stream.getvalue())
        stream.seek(0)
        stream.truncate()
        terminal.history([{"role": "tool", "content": json.dumps({"result": "note " * 300})}])
        self.assertIn("preview truncated", stream.getvalue())
        self.assertNotIn('{"result"', stream.getvalue())

    def test_session_ids_and_command_help_are_copyable(self):
        session_id = "session_" + "a" * 60
        stream = io.StringIO()
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            terminal = Terminal(stream)
            terminal.header(session_id, "example/model")
            terminal.info("Resumed session: " + session_id)
            terminal.sessions([{"id": session_id, "title": "An earlier task"}], session_id)
            terminal.help()
        self.assertGreaterEqual(stream.getvalue().count(session_id), 3)
        for command in ("/new", "/sessions", "/resume", "/memory", "/notes", "/exit"):
            self.assertIn(command, stream.getvalue())

    def test_progress_is_silent_for_redirected_output(self):
        stream = io.StringIO()
        Terminal(stream).status("Working...")
        self.assertEqual(stream.getvalue(), "")


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, value in (
            ("storage.SESSIONS_DIR", root / "sessions"),
            ("storage.MEMORY_FILES", {"agent": root / "MEMORY.md", "user": root / "USER.md"}),
            ("chat.WORKSPACE", root / "workspace"),
        ):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled"))
        network.start()
        self.addCleanup(network.stop)

    def test_formatting_does_not_change_saved_or_replayed_content(self):
        session = storage.new_session()
        raw_result = json.dumps({"result": "Original\x1b[31m file text"})
        answer = "## Result\n\n```python\nprint('unchanged')\n```"
        client = FakeClient([reply(calls=[("read_1", "read_file", {"path": "note.txt"})]), reply(answer)])
        rendered = io.StringIO()
        terminal = Terminal(rendered)

        def display(round_number, name, arguments, result):
            saved = storage.load_session(session.id)
            self.assertEqual(saved.messages[-1]["content"], raw_result)
            terminal.tool(round_number, name, arguments, result)

        with patch.object(agent, "execute_tool", return_value=raw_result):
            result = agent.run_agent(client, "Read the note", session, on_tool=display)
        terminal.answer(result)
        self.assertEqual(result, answer)
        self.assertEqual(session.messages[-1]["content"], answer)
        self.assertEqual(client.requests[1]["messages"][-1]["content"], raw_result)
        self.assertNotIn("Original", rendered.getvalue())
        self.assertNotIn("\x1b", rendered.getvalue())

    def test_display_failure_does_not_erase_a_completed_tool(self):
        session = storage.new_session()
        client = FakeClient([reply(calls=[("read_1", "memory", {"action": "read", "target": "agent"})])])
        with self.assertRaises(OSError):
            agent.run_agent(client, "Read memory", session, on_tool=MagicMock(side_effect=OSError("display failed")))
        saved = storage.load_session(session.id)
        self.assertEqual(saved.messages[-1]["tool_call_id"], "read_1")
        self.assertIn("result", json.loads(saved.messages[-1]["content"]))

    def test_slash_commands_never_call_the_model(self):
        saved = storage.new_session()
        saved.messages = [{"role": "user", "content": "Earlier task"}]
        storage.save_session(saved)
        client = FakeClient([])
        context = MagicMock()
        context.__enter__.return_value = client
        stdout, stderr = io.StringIO(), io.StringIO()
        commands = ["/help", "/sessions", "/history", "/memory", "/notes", f"/resume {saved.id}", "/new", "/exit"]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
             patch.object(chat, "load_dotenv"), \
             patch.object(chat, "OpenAI", return_value=context), \
             patch("builtins.input", side_effect=commands), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            chat.main(["--plain"])
        self.assertEqual(client.requests, [])
        self.assertIn(saved.id, stdout.getvalue())
        self.assertIn("Commands", stdout.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue() + stderr.getvalue())

    def test_one_shot_entry_point_uses_the_same_plain_format(self):
        client = FakeClient([reply("A concise answer.")])
        context = MagicMock()
        context.__enter__.return_value = client
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
             patch.object(agent, "load_dotenv"), \
             patch.object(agent, "WORKSPACE", chat.WORKSPACE), \
             patch.object(agent, "OpenAI", return_value=context), \
             patch("builtins.input", return_value="A simple question"), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            agent.main(["--plain"])
        self.assertEqual(len(client.requests), 1)
        self.assertIn("A concise answer.", stdout.getvalue())
        self.assertIn("session", stderr.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
