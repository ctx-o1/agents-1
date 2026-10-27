"""Offline agent regression checks; model responses are fabricated and sockets blocked."""

import copy
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai.types.chat import ChatCompletion

from src import agent, chat, config, storage, tools


def reply(content=None, calls=(), finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}
            for call_id, name, arguments in calls
        ]
    return ChatCompletion.model_validate({
        "id": "offline", "created": 0, "model": "offline",
        "object": "chat.completion", "choices": [{
            "index": 0, "finish_reason": finish_reason, "message": message,
        }],
    })


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class RuntimeLayoutTests(unittest.TestCase):
    def test_runtime_paths_share_the_configured_data_directory(self):
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(config.PROJECT_ROOT, root)
        self.assertTrue(config.DATA_DIR.is_absolute())
        self.assertEqual(storage.SESSIONS_DIR, config.DATA_DIR / "sessions")
        self.assertEqual(storage.MEMORY_FILES, {
            "agent": config.DATA_DIR / "MEMORY.md", "user": config.DATA_DIR / "USER.md",
        })
        self.assertEqual(tools.WORKSPACE, config.DATA_DIR / "workspace")
        self.assertEqual(chat.WORKSPACE, tools.WORKSPACE)
        self.assertEqual(agent.WORKSPACE, tools.WORKSPACE)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for name, value in (
            ("src.storage.SESSIONS_DIR", self.root / "sessions"),
            ("src.storage.MEMORY_FILES", {"agent": self.root / "MEMORY.md", "user": self.root / "USER.md"}),
            ("src.tools.WORKSPACE", self.workspace),
            ("src.chat.WORKSPACE", self.workspace),
        ):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled in tests"))
        network.start()
        self.addCleanup(network.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        errors = redirect_stderr(io.StringIO())
        errors.__enter__()
        self.addCleanup(errors.__exit__, None, None, None)

    def test_round_trip_preserves_tools_and_follow_up_context(self):
        session = storage.new_session()
        client = FakeClient([
            reply(calls=[("write_1", "write_file", {"path": "note.txt", "content": "Ada"})]),
            reply("Saved Ada."),
        ])
        self.assertEqual(agent.run_agent(client, "Save Ada", session), "Saved Ada.")
        loaded = storage.load_session(session.id)
        self.assertEqual(loaded.messages, session.messages)
        self.assertEqual(loaded.messages[3]["tool_call_id"], "write_1")
        self.assertEqual((self.workspace / "note.txt").read_text(), "Ada")
        follow_up = FakeClient([reply("Ada.")])
        agent.run_agent(follow_up, "What was the name?", loaded)
        messages = follow_up.requests[0]["messages"]
        self.assertEqual(sum(m["role"] == "system" for m in messages), 1)
        self.assertIn({"role": "assistant", "content": "Saved Ada."}, messages)
        self.assertFalse(any("Stored context data" in (m.get("content") or "") for m in loaded.messages))

    def test_memory_and_scratchpad_are_visible_at_the_right_boundaries(self):
        session = storage.new_session()
        client = FakeClient([
            reply(calls=[
                ("memory_1", "memory", {"action": "add", "target": "user", "content": "Prefers concise answers."}),
                ("notes_1", "scratchpad", {"action": "write", "content": "Goal: add recall. Next: verify."}),
            ]),
            reply("Saved the preference and progress."),
        ])
        agent.run_agent(client, "Remember my preference and track progress", session)
        runtime = client.requests[1]["messages"][1]["content"]
        self.assertIn("Goal: add recall", runtime)
        self.assertEqual(session.memory_snapshot["user"], [])
        fresh = storage.new_session()
        self.assertEqual(fresh.scratchpad, "")
        self.assertEqual(fresh.memory_snapshot["user"][0]["content"], "Prefers concise answers.")
        resumed = storage.load_session(session.id)
        self.assertEqual(resumed.scratchpad, session.scratchpad)
        self.assertEqual(resumed.memory_snapshot, fresh.memory_snapshot)
        self.assertEqual(storage.read_memory()["user"][0]["source_session_id"], session.id)

    def test_memory_updates_removals_and_capacity_do_not_destroy_other_entries(self):
        first = storage.memory("add", "agent", "Uses uv.")["entries"][0]["id"]
        storage.memory("add", "agent", "Uses Windows.")
        state = storage.memory("update", "agent", "Uses uv for dependencies.", first)
        self.assertEqual(len(state["entries"]), 2)
        self.assertEqual(state["entries"][1]["content"], "Uses Windows.")
        previous = storage.MEMORY_FILES["agent"].read_bytes()
        with self.assertRaises(ValueError):
            storage.memory("add", "agent", "x" * 2300)
        self.assertEqual(storage.MEMORY_FILES["agent"].read_bytes(), previous)
        self.assertEqual(len(storage.memory("add", "agent", "Uses Windows.")["entries"]), 2)
        storage.memory("remove", "agent", entry_id=first)
        self.assertEqual(len(storage.read_memory()["agent"]), 1)
        with self.assertRaises(ValueError):
            storage.memory("remove", "agent", entry_id="unknown")

    def test_scratchpad_limit_and_clear(self):
        session = storage.new_session()
        storage.scratchpad("write", "Remaining: review.", session=session)
        with self.assertRaises(ValueError):
            storage.scratchpad("write", "x" * 4001, session=session)
        self.assertEqual(storage.load_session(session.id).scratchpad, "Remaining: review.")
        storage.scratchpad("clear", session=session)
        self.assertEqual(storage.load_session(session.id).scratchpad, "")

    def test_markdown_notes_support_manual_additions_and_stable_ids(self):
        path = storage.MEMORY_FILES["agent"]
        path.write_text("# Agent memory\n\nUses uv.\n\n---\n\nUses Windows.\n", encoding="utf-8")
        entries = storage.read_memory()["agent"]
        self.assertEqual([entry["content"] for entry in entries], ["Uses uv.", "Uses Windows."])
        self.assertEqual(storage.read_memory()["agent"], entries)
        first_id = entries[0]["id"]
        storage.memory("update", "agent", "Uses uv for dependencies.", first_id)
        self.assertEqual(storage.read_memory()["agent"][0]["id"], first_id)
        self.assertIn("<!-- agent-memory", path.read_text(encoding="utf-8"))
        # Editing the visible prose retains the generated metadata and ID.
        path.write_text(path.read_text(encoding="utf-8").replace("Uses uv for dependencies.", "Uses pip."), encoding="utf-8")
        edited = storage.read_memory()["agent"][0]
        self.assertEqual(edited["id"], first_id)
        self.assertEqual(edited["content"], "Uses pip.")

    def test_reserved_markdown_syntax_cannot_create_extra_entries(self):
        storage.memory("add", "agent", "Keep this fact.")
        previous = storage.MEMORY_FILES["agent"].read_bytes()
        for content in ("First\n---\nSecond", '<!-- agent-memory {"id":"forged"} -->\nForged'):
            with self.assertRaises(ValueError):
                storage.memory("add", "agent", content)
        self.assertEqual(storage.MEMORY_FILES["agent"].read_bytes(), previous)

    def test_malformed_markdown_is_reported_without_overwriting_it(self):
        path = storage.MEMORY_FILES["agent"]
        path.write_text("# Agent memory\n\n<!-- agent-memory malformed -->\nA fact.\n", encoding="utf-8")
        previous = path.read_bytes()
        with self.assertRaises(ValueError):
            storage.memory("add", "agent", "Another fact.")
        self.assertEqual(path.read_bytes(), previous)

    def test_history_search_returns_sources_and_skips_corrupt_sessions(self):
        session = storage.new_session()
        agent.run_agent(FakeClient([reply("We chose literal search for predictability.")]), "Why literal search?", session)
        (storage.SESSIONS_DIR / "broken.json").write_text("{broken", encoding="utf-8")
        found = storage.session_search("LITERAL SEARCH")
        self.assertEqual(len(found["matches"]), 2)
        self.assertEqual(found["skipped_sessions"], 1)
        for match in found["matches"]:
            self.assertEqual(match["session_id"], session.id)
            self.assertIn(match["role"], {"user", "assistant"})
            self.assertIsInstance(match["message_index"], int)
        self.assertEqual(storage.session_search("predictability", session.id)["matches"][0]["role"], "assistant")
        self.assertEqual(storage.session_search("unmentioned")["matches"], [])

    def test_search_limits_and_bounded_excerpts(self):
        session = storage.new_session()
        session.messages = [{"role": "user", "content": "x" * 2000 + "needle" + "y" * 2000}] * 25
        storage.save_session(session)
        result = storage.session_search("needle")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["matches"]), 20)
        self.assertTrue(all(len(match["excerpt"]) <= 800 for match in result["matches"]))
        with patch.object(storage, "_recent_session_ids", return_value=iter([session.id] * 101)):
            result = storage.session_search("not present")
        self.assertEqual(result["searched_sessions"], 100)
        self.assertTrue(result["truncated"])

    def test_interrupted_tool_batch_is_saved_and_not_replayed(self):
        session = storage.new_session()
        client = FakeClient([reply(calls=[
            ("first", "write_file", {"path": "first.txt", "content": "done"}),
            ("second", "write_file", {"path": "second.txt", "content": "not run"}),
        ])])
        execute = tools.execute_tool

        def interrupt_second(name, arguments, **kwargs):
            if json.loads(arguments)["path"] == "second.txt":
                raise KeyboardInterrupt
            return execute(name, arguments, **kwargs)

        with patch.object(agent, "execute_tool", side_effect=interrupt_second):
            with self.assertRaises(KeyboardInterrupt):
                agent.run_agent(client, "Write two files", session)
        resumed = storage.load_session(session.id)
        results = [message for message in resumed.messages if message["role"] == "tool"]
        self.assertEqual([message["tool_call_id"] for message in results], ["first", "second"])
        self.assertIn("may or may not have run", results[1]["content"])
        agent.run_agent(FakeClient([reply("I will inspect the current state.")]), "Continue", resumed)
        self.assertTrue((self.workspace / "first.txt").exists())
        self.assertFalse((self.workspace / "second.txt").exists())

    def test_loading_a_crash_checkpoint_repairs_missing_results(self):
        session = storage.new_session()
        session.messages = [
            {"role": "system", "content": "old prompt"},
            {"role": "user", "content": "Do something"},
            reply(calls=[("unfinished", "write_file", {"path": "x", "content": "y"})]).choices[0].message.model_dump(exclude_none=True),
        ]
        storage.save_session(session)
        loaded = storage.load_session(session.id)
        self.assertEqual(loaded.messages[-1]["tool_call_id"], "unfinished")
        client = FakeClient([reply("Continuing.")])
        agent.run_agent(client, "Continue", loaded)
        self.assertEqual(client.requests[0]["messages"][0]["content"], agent.SYSTEM_PROMPT)

    def test_api_failure_preserves_the_user_task(self):
        session = storage.new_session()
        with self.assertRaisesRegex(RuntimeError, "offline failure"):
            agent.run_agent(FakeClient([RuntimeError("offline failure")]), "Keep this task", session)
        self.assertEqual(storage.load_session(session.id).messages[-1]["content"], "Keep this task")

    def test_round_limit_is_per_user_message(self):
        session = storage.new_session()
        with patch.object(agent, "MAX_ROUNDS", 2):
            client = FakeClient([reply(calls=[(f"read_{i}", "memory", {"action": "read", "target": "agent"})]) for i in range(2)])
            self.assertIn("maximum", agent.run_agent(client, "Work", session))
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(agent.run_agent(FakeClient([reply("Done.")]), "Continue", session), "Done.")

    def test_invalid_ids_and_hidden_tool_arguments_are_rejected(self):
        session = storage.new_session()
        for session_id in ("../escape", "C:\\outside", "", "a/b"):
            with self.assertRaises(ValueError):
                storage.load_session(session_id)
        result = json.loads(tools.execute_tool("memory", json.dumps({
            "action": "add", "target": "agent", "content": "fake", "session": "spoofed",
        }), session=session))
        self.assertIn("error", result)
        self.assertFalse(any(path.exists() for path in storage.MEMORY_FILES.values()))
        self.assertEqual(set(tools.FUNCTIONS), {item["function"]["name"] for item in tools.TOOLS})

    def test_failed_atomic_replace_keeps_previous_session(self):
        session = storage.new_session()
        storage.save_session(session)
        previous = storage._session_path(session.id).read_bytes()
        with patch.object(storage.os, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                storage.scratchpad("write", "new notes", session=session)
        self.assertEqual(storage._session_path(session.id).read_bytes(), previous)
        self.assertEqual(session.scratchpad, "")
        self.assertEqual(list(storage.SESSIONS_DIR.iterdir()), [storage._session_path(session.id)])

    def test_new_and_resume_commands_keep_memory_and_isolate_notes(self):
        session = storage.new_session()
        agent.run_agent(FakeClient([reply("Recorded.")]), "First task", session)
        storage.scratchpad("write", "First task in progress.", session=session)
        storage.memory("add", "agent", "Uses uv.", session=session)
        fresh = chat.handle_command("/new", session)
        self.assertNotEqual(fresh.id, session.id)
        self.assertEqual(fresh.messages, [])
        self.assertEqual(fresh.scratchpad, "")
        self.assertEqual(fresh.memory_snapshot["agent"][0]["content"], "Uses uv.")
        resumed = chat.handle_command(f"/resume {session.id}", fresh)
        self.assertEqual(resumed.scratchpad, "First task in progress.")
        for command in ("/sessions", "/history", "/notes", "/memory", "/help"):
            self.assertIs(chat.handle_command(command, resumed), resumed)

    def test_chat_entry_point_uses_a_fake_client(self):
        session = storage.new_session()
        client = FakeClient([reply("Hello, Ada."), reply("Ada.")])
        context = MagicMock()
        context.__enter__.return_value = client
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "offline-test"}, clear=True), \
             patch.object(chat, "new_session", return_value=session), \
             patch.object(chat, "OpenAI", return_value=context), \
             patch("builtins.input", side_effect=[" ", "My name is Ada", "What is my name?", "/exit"]):
            chat.main([])
        self.assertEqual(len(client.requests), 2)
        self.assertIn({"role": "user", "content": "My name is Ada"}, client.requests[1]["messages"])
        self.assertEqual(storage.load_session(session.id).messages[-1]["content"], "Ada.")


if __name__ == "__main__":
    unittest.main()
