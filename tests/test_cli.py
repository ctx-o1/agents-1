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

from wcwidth import iter_graphemes, wcswidth

from src import agent, chat, cli, storage
from src.cli import Terminal
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
            Terminal(stream).answer("**Hello**")
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

    def test_reasoning_flushes_fragments_wraps_and_resets_between_rounds(self):
        stream = io.StringIO()
        terminal = Terminal(stream, color=False)
        terminal.width = 40
        terminal.reasoning(1, "First ")
        self.assertIn("  First", stream.getvalue())
        terminal.reasoning(1, "step.\n" + "a" * 33 + " wrapped\n" + "x" * 80)
        terminal.end_reasoning()
        terminal.reasoning(2, "Next step.")
        terminal.end_reasoning()
        output = stream.getvalue()
        self.assertEqual(output.count("Reasoning / round 1"), 1)
        self.assertIn("Reasoning / round 2\n  Next step.\n", output)
        self.assertIn("a" * 33 + "\n  wrapped", output)
        self.assertNotIn("  | ", output)
        self.assertTrue(all(len(line) <= 40 for line in output.splitlines()))
        self.assertNotIn("\x1b", output)

    def test_reasoning_sanitizes_escape_sequences_split_across_chunks(self):
        stream = io.StringIO()
        terminal = Terminal(stream, color=False)
        for part in ("Visible\x1b", "[2", "J text\x1b]0;", "hostile title\x1b", "\\\nNext\r\b\tline"):
            terminal.reasoning(1, part)
        terminal.end_reasoning()
        terminal.reasoning(2, "Safe")
        terminal.end_reasoning()
        output = stream.getvalue()
        self.assertIn("Visible text", output)
        self.assertIn("Next line", output)
        self.assertIn("Safe", output)
        self.assertNotIn("hostile title", output)
        self.assertNotIn("[2J", output)
        for control in ("\x1b", "\b", "\r", "\x07"):
            self.assertNotIn(control, output)

    def test_empty_reasoning_never_opens_a_panel(self):
        stream = io.StringIO()
        Terminal(stream).end_reasoning()
        self.assertEqual(stream.getvalue(), "")

    def test_unicode_reasoning_wraps_by_cells_without_splitting_graphemes(self):
        for cluster in ("你", "😀", "e\u0301", "👩\u200d💻", "❤️", "1️⃣", "🇮🇳"):
            for fragment_size in (1, 7, 1000):
                with self.subTest(cluster=cluster, fragment_size=fragment_size):
                    stream = io.StringIO()
                    terminal = Terminal(stream, color=False)
                    terminal.width = 40
                    text = "x" * 37 + cluster * 40
                    for start in range(0, len(text), fragment_size):
                        terminal.reasoning(1, text[start:start + fragment_size])
                    terminal.end_reasoning()
                    lines = [line for line in stream.getvalue().splitlines() if line.startswith("  ")]
                    self.assertTrue(all(wcswidth(line) <= 40 for line in lines))
                    self.assertEqual("".join(line[2:] for line in lines), text)
                    # Segmentation must be unchanged by chunk boundaries or wrapping.
                    self.assertEqual([g for line in lines for g in iter_graphemes(line[2:])],
                                     list(iter_graphemes(text)))
                    if wcswidth(cluster) == 2:
                        self.assertEqual(lines[0], "  " + "x" * 37)

    def test_end_reasoning_flushes_the_last_grapheme_before_the_next_round(self):
        stream = io.StringIO()
        terminal = Terminal(stream, color=False)
        terminal.reasoning(1, "e")
        terminal.reasoning(1, "\u0301")
        terminal.end_reasoning()
        terminal.reasoning(2, "Z")
        terminal.end_reasoning()
        self.assertIn("  e\u0301\n", stream.getvalue())
        self.assertIn("Reasoning / round 2\n  Z\n", stream.getvalue())


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, value in (
            ("src.storage.SESSIONS_DIR", root / "sessions"),
            ("src.storage.MEMORY_FILES", {"agent": root / "MEMORY.md", "user": root / "USER.md"}),
            ("src.chat.WORKSPACE", root / "workspace"),
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
             patch.object(cli, "OpenAI", return_value=context), \
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
             patch.object(agent, "WORKSPACE", chat.WORKSPACE), \
             patch.object(cli, "OpenAI", return_value=context), \
             patch("builtins.input", return_value="A simple question"), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            agent.main(["--plain"])
        self.assertEqual(len(client.requests), 1)
        self.assertIn("A concise answer.", stdout.getvalue())
        self.assertIn("session", stderr.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue() + stderr.getvalue())

    def test_both_entry_points_support_streaming_and_buffered_reasoning(self):
        for entry in (agent, chat):
            for flags, streaming in (([], True), (["--stream"], True), (["--no-stream"], False)):
                with self.subTest(entry=entry.__name__, flags=flags):
                    response = reply("A final answer.")
                    response.choices[0].message.reasoning = "Inspecting the problem."
                    client = FakeClient([response])
                    context = MagicMock()
                    context.__enter__.return_value = client
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
                         patch.object(agent, "WORKSPACE", chat.WORKSPACE), \
                         patch.object(cli, "OpenAI", return_value=context), \
                         patch("builtins.input", side_effect=["Work", "/exit"]), \
                         redirect_stdout(stdout), redirect_stderr(stderr):
                        entry.main(["--plain", *flags])
                    self.assertEqual(client.requests[0]["stream"], streaming)
                    self.assertIn("Reasoning / round 1", stderr.getvalue())
                    self.assertIn("Inspecting the problem.", stderr.getvalue())
                    self.assertNotIn("Inspecting", stdout.getvalue())
                    self.assertEqual(stdout.getvalue().count("A final answer."), 1)
                    self.assertNotIn("\x1b", stdout.getvalue() + stderr.getvalue())

    def test_both_entry_points_apply_settings_and_cli_stream_overrides(self):
        for entry in (agent, chat):
            for configured, flags, expected in (("false", [], False), ("false", ["--stream"], True),
                                                ("true", ["--no-stream"], False)):
                with self.subTest(entry=entry.__name__, configured=configured, flags=flags):
                    client = FakeClient([reply("Configured answer.")])
                    context = MagicMock()
                    context.__enter__.return_value = client
                    environment = {
                        "OPENROUTER_API_KEY": "offline-test", "OPENROUTER_MODEL": "custom/model",
                        "OPENROUTER_BASE_URL": "http://localhost:8000/v1",
                        "AGENT_1_MAX_OUTPUT_TOKENS": "8192", "AGENT_1_REASONING_ENABLED": "false",
                        "AGENT_1_STREAM": configured,
                    }
                    with patch.dict(os.environ, environment, clear=True), \
                         patch.object(agent, "WORKSPACE", chat.WORKSPACE), \
                         patch.object(cli, "OpenAI", return_value=context) as constructor, \
                         patch("builtins.input", side_effect=["Work", "/exit"]), \
                         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        entry.main(flags)
                    self.assertEqual(constructor.call_args.kwargs["base_url"], "http://localhost:8000/v1")
                    self.assertEqual(client.requests[0]["model"], "custom/model")
                    self.assertEqual(client.requests[0]["max_tokens"], 8192)
                    self.assertEqual(client.requests[0]["stream"], expected)
                    self.assertFalse(client.requests[0]["extra_body"]["reasoning"]["enabled"])

    def test_invalid_settings_fail_before_client_creation(self):
        for entry in (agent, chat):
            with self.subTest(entry=entry.__name__), \
                 patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test", "AGENT_1_MAX_ROUNDS": "0"}, clear=True), \
                 patch.object(cli, "OpenAI") as constructor:
                with self.assertRaisesRegex(SystemExit, "Configuration: AGENT_1_MAX_ROUNDS"):
                    entry.main([])
                constructor.assert_not_called()

    def test_live_prose_interleaves_with_reasoning_and_is_not_printed_twice(self):
        from tests.test_agent import FakeStream, chunk

        stdout, stderr = io.StringIO(), io.StringIO()
        client = FakeClient([FakeStream([
            chunk({"reasoning": "Inspecting."}),
            chunk({"content": "First "}),
            chunk({"reasoning": "Checking."}),
            chunk({"content": "answer."}),
            chunk(finish_reason="stop"),
        ])])
        context = MagicMock()
        context.__enter__.return_value = client
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
             patch.object(agent, "WORKSPACE", chat.WORKSPACE), \
             patch.object(cli, "OpenAI", return_value=context), \
             patch("builtins.input", return_value="Work"), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            agent.main(["--plain"])
        self.assertEqual(stdout.getvalue().count("First"), 1)
        self.assertEqual(stdout.getvalue().count("answer."), 1)
        self.assertIn("Inspecting.", stderr.getvalue())
        self.assertIn("Checking.", stderr.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue() + stderr.getvalue())

    def test_chat_preserves_multiline_task_whitespace(self):
        task = "    First line\n\n    second line  "
        client = FakeClient([reply("Read it.")])
        context = MagicMock()
        context.__enter__.return_value = client
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
             patch.object(cli, "OpenAI", return_value=context), \
             patch("builtins.input", side_effect=[task, "/exit"]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            chat.main(["--plain"])
        self.assertIn({"role": "user", "content": task}, client.requests[0]["messages"])


if __name__ == "__main__":
    unittest.main()
