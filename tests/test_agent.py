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

from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src import agent, chat, config, storage, tools


def reply(content=None, calls=(), finish_reason="stop", usage=None):
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
        "object": "chat.completion", "usage": usage, "choices": [{
            "index": 0, "finish_reason": finish_reason, "message": message,
        }],
    })


def chunk(delta=None, finish_reason=None, *, choices=True, usage=None):
    return ChatCompletionChunk.model_validate({
        "id": "offline", "created": 0, "model": "offline",
        "object": "chat.completion.chunk",
        "usage": usage,
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]
        if choices else [],
    })


class FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def __iter__(self):
        return self.chunks


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
        if kwargs.get("stream") and isinstance(response, ChatCompletion):
            choice = response.choices[0]
            delta = choice.message.model_dump(exclude_none=True)
            for index, call in enumerate(delta.get("tool_calls", [])):
                call["index"] = index
            return FakeStream([chunk(delta), chunk(finish_reason=choice.finish_reason),
                               chunk(choices=False, usage=response.usage)])
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
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
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

    def test_streamed_reasoning_arrives_before_completion_and_is_replayed(self):
        session = storage.new_session()
        seen = []

        def fragments():
            yield chunk({"reasoning": "Let me "})
            self.assertEqual(seen, [(1, "Let me ")])
            yield chunk({"reasoning": "think.", "content": "Hello"})
            yield chunk({"content": ", Ada."})
            yield chunk(finish_reason="stop")
            yield chunk(choices=False)  # Usage-only chunk.

        stream = FakeStream(fragments())
        client = FakeClient([stream, reply("Follow-up.")])
        answer = agent.run_agent(client, "Hello", session, stream=True,
                                 on_reasoning=lambda *args: seen.append(args))
        self.assertEqual(answer, "Hello, Ada.")
        self.assertTrue(stream.closed)
        self.assertEqual(seen, [(1, "Let me "), (1, "think.")])
        loaded = storage.load_session(session.id)
        self.assertEqual(loaded.messages[-1]["reasoning"], "Let me think.")
        agent.run_agent(client, "Continue", loaded)
        self.assertIn(loaded.messages[-3], client.requests[1]["messages"])
        self.assertTrue(client.requests[0]["stream"])
        self.assertFalse(client.requests[1]["stream"])

    def test_invalid_tool_ids_never_execute_and_leave_session_resumable(self):
        for streaming in (False, True):
            for ids in (("same", "same"), ("", "other"), (" \t", "other")):
                with self.subTest(streaming=streaming, ids=ids):
                    session = storage.new_session()
                    calls = [(call_id, "write_file", {"path": f"{i}.txt", "content": "Not written"})
                             for i, call_id in enumerate(ids)]
                    client = FakeClient([reply(calls=calls), reply("Recovered.")])
                    with patch.object(agent, "execute_tool") as execute:
                        with self.assertRaisesRegex(ValueError, "[Tt]ool-call IDs"):
                            agent.run_agent(client, "Write", session, stream=streaming)
                        execute.assert_not_called()
                    loaded = storage.load_session(session.id)
                    self.assertFalse(any(message.get("tool_calls") for message in loaded.messages))
                    self.assertEqual(loaded.messages[-1]["content"], "Write")
                    self.assertEqual(agent.run_agent(client, "Recover", loaded, stream=streaming), "Recovered.")

    def test_streamed_parallel_tool_fragments_wait_for_completion(self):
        session = storage.new_session()
        seen = []

        def fragments():
            yield chunk({"tool_calls": [
                {"index": 1, "id": "second", "function": {"name": "write_", "arguments": '{"path":"b.txt",'}},
                {"index": 0, "id": "first", "function": {"name": "write_file", "arguments": '{"path":"a.txt",'}},
            ]})
            self.assertFalse((self.workspace / "a.txt").exists())
            yield chunk({"tool_calls": [
                {"index": 0, "function": {"arguments": '"content":"A"}'}},
                {"index": 1, "function": {"name": "file", "arguments": '"content":"B"}'}},
            ]})
            self.assertFalse((self.workspace / "b.txt").exists())
            yield chunk(finish_reason="tool_calls")

        client = FakeClient([FakeStream(fragments()), reply("Written.")])
        self.assertEqual(agent.run_agent(client, "Write", session, stream=True,
            on_tool=lambda round_number, name, args, result: seen.append(json.loads(args)["path"])), "Written.")
        self.assertEqual(seen, ["a.txt", "b.txt"])
        self.assertEqual((self.workspace / "b.txt").read_text(), "B")
        self.assertEqual([m["tool_call_id"] for m in client.requests[1]["messages"] if m["role"] == "tool"], ["first", "second"])

    def test_reasoning_details_merge_and_duplicate_text_is_not_displayed(self):
        session = storage.new_session()
        seen = []
        client = FakeClient([FakeStream([
            chunk({"reasoning": "Thinking", "reasoning_details": [
                {"index": 0, "type": "reasoning.text", "text": "Thinking", "signature": "sig"},
            ]}),
            chunk({"reasoning": ".", "reasoning_details": [
                {"index": 0, "type": "reasoning.text", "text": ".", "signature": "nature"},
                {"index": 1, "type": "reasoning.encrypted", "data": "opaque"},
            ]}),
            chunk({"content": "Done"}, "stop"),
        ])])
        agent.run_agent(client, "Work", session, stream=True,
                        on_reasoning=lambda _, text: seen.append(text))
        self.assertEqual("".join(seen), "Thinking.")
        details = storage.load_session(session.id).messages[-1]["reasoning_details"]
        self.assertEqual(details[0]["text"], "Thinking.")
        self.assertEqual(details[0]["signature"], "signature")
        self.assertEqual(details[1]["data"], "opaque")

    def test_unindexed_reasoning_blocks_keep_their_ids_and_replay_separately(self):
        session = storage.new_session()
        seen = []
        client = FakeClient([FakeStream([
            chunk({"reasoning_details": [{"id": "text-1", "type": "reasoning.text", "text": "Plan."}]}),
            chunk({"reasoning_details": [{"id": "encrypted-1", "type": "reasoning.encrypted", "data": "opaque"}]}),
            chunk({"reasoning_details": [{"id": "text-2", "type": "reasoning.text", "text": "Check."}]}),
            chunk({"reasoning_details": [
                {"id": "text-2", "type": "reasoning.text", "text": " Verified."},
                {"id": "text-1", "type": "reasoning.text", "text": " Complete.", "signature": "sig"},
            ]}),
            chunk({"content": "Done"}, "stop"),
        ]), reply("Follow-up")])
        agent.run_agent(client, "Work", session, stream=True,
                        on_reasoning=lambda _, text: seen.append(text))
        expected = [
            {"id": "text-1", "type": "reasoning.text", "text": "Plan. Complete.", "signature": "sig"},
            {"id": "encrypted-1", "type": "reasoning.encrypted", "data": "opaque"},
            {"id": "text-2", "type": "reasoning.text", "text": "Check. Verified."},
        ]
        loaded = storage.load_session(session.id)
        self.assertEqual(loaded.messages[-1]["reasoning_details"], expected)
        self.assertNotIn("opaque", "".join(seen))
        agent.run_agent(client, "Continue", loaded)
        replayed = next(m for m in client.requests[1]["messages"] if "reasoning_details" in m)
        self.assertEqual(replayed["reasoning_details"], expected)

    def test_reasoning_block_identity_can_alternate_between_id_and_index(self):
        session = storage.new_session()
        client = FakeClient([FakeStream([
            chunk({"reasoning_details": [{"id": "plan", "type": "reasoning.text", "text": "A"}]}),
            chunk({"reasoning_details": [{"id": "plan", "index": 2, "type": "reasoning.text", "text": "B"}]}),
            chunk({"reasoning_details": [{"index": 2, "text": "C"}]}),
            chunk({"reasoning_details": [{"id": "plan", "text": "D"}]}),
            chunk({"content": "Done"}, "stop"),
        ])])
        agent.run_agent(client, "Work", session, stream=True)
        self.assertEqual(session.messages[-1]["reasoning_details"], [
            {"id": "plan", "index": 2, "type": "reasoning.text", "text": "ABCD"},
        ])

    def test_indexed_reasoning_blocks_keep_numeric_order(self):
        session = storage.new_session()
        client = FakeClient([FakeStream([
            chunk({"reasoning_details": [{"index": 2, "type": "reasoning.text", "text": "Later"}]}),
            chunk({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "Earlier"}]}),
            chunk({"content": "Done"}, "stop"),
        ])])
        agent.run_agent(client, "Work", session, stream=True)
        self.assertEqual([d["index"] for d in session.messages[-1]["reasoning_details"]], [0, 2])

    def test_late_reasoning_aliases_unify_interleaved_fragments_in_arrival_order(self):
        for interleave in (False, True):
            with self.subTest(interleave=interleave):
                session = storage.new_session()
                fragments = [
                    chunk({"reasoning_details": [{"id": "plan", "type": "reasoning.text", "text": "A", "signature": "s"}]}),
                    chunk({"reasoning_details": [{"index": 2, "type": "reasoning.text", "text": "B", "signature": "i"}]}),
                ]
                if interleave:
                    fragments.append(chunk({"reasoning_details": [{"id": "plan", "text": "C", "signature": "g"}]}))
                fragments.extend([
                    chunk({"reasoning_details": [{"id": "plan", "index": 2, "type": "reasoning.text", "text": "D"}]}),
                    chunk({"reasoning_details": [{"index": 2, "text": "E"}]}),
                    chunk({"reasoning_details": [{"id": "plan", "text": "F"}]}),
                    chunk({"content": "Done"}, "stop"),
                ])
                client = FakeClient([FakeStream(fragments), reply("Follow-up")])
                agent.run_agent(client, "Work", session, stream=True)
                expected = [{"id": "plan", "index": 2, "type": "reasoning.text",
                             "text": "ABCDEF" if interleave else "ABDEF",
                             "signature": "sig" if interleave else "si"}]
                loaded = storage.load_session(session.id)
                self.assertEqual(loaded.messages[-1]["reasoning_details"], expected)
                agent.run_agent(client, "Continue", loaded)
                replayed = next(m for m in client.requests[1]["messages"] if "reasoning_details" in m)
                self.assertEqual(replayed["reasoning_details"], expected)

    def test_fragmented_tool_ids_survive_execution_save_and_replay(self):
        session = storage.new_session()
        client = FakeClient([FakeStream([
            chunk({"tool_calls": [
                {"index": 1, "id": "other_", "function": {"name": "memory", "arguments": '{"action":"read",'}},
                {"index": 0, "id": "call_", "function": {"name": "memory", "arguments": '{"action":"read",'}},
            ]}),
            chunk({"tool_calls": [
                {"index": 0, "id": "123", "function": {"arguments": '"target":"agent"}'}},
                {"index": 1, "id": "456", "function": {"arguments": '"target":"user"}'}},
            ]}),
            chunk(finish_reason="tool_calls"),
        ]), reply("Read both stores.")])
        agent.run_agent(client, "Read memory", session, stream=True)
        loaded = storage.load_session(session.id)
        tool_message = next(m for m in loaded.messages if m.get("tool_calls"))
        self.assertEqual([call["id"] for call in tool_message["tool_calls"]], ["call_123", "other_456"])
        self.assertEqual([m["tool_call_id"] for m in loaded.messages if m["role"] == "tool"],
                         ["call_123", "other_456"])
        replayed = next(m for m in client.requests[1]["messages"] if m.get("tool_calls"))
        self.assertEqual(replayed, tool_message)

    def test_details_only_and_buffered_reasoning_are_visible_and_saved(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                session = storage.new_session()
                response = reply("Done")
                response.choices[0].message.reasoning_details = [
                    {"type": "reasoning.summary", "summary": "A plan."},
                    {"type": "reasoning.encrypted", "data": "hidden"},
                ]
                seen = []
                agent.run_agent(FakeClient([response]), "Work", session, stream=streaming,
                                on_reasoning=lambda _, text: seen.append(text))
                self.assertEqual(seen, ["A plan."])
                self.assertEqual(storage.load_session(session.id).messages[-1]["reasoning_details"],
                                 response.choices[0].message.reasoning_details)

    def test_incomplete_streams_never_execute_partial_tools_and_close_display(self):
        for ending in ("truncated", "exception", "length", "error"):
            with self.subTest(ending=ending):
                session = storage.new_session()
                closed = []

                def fragments():
                    yield chunk({"reasoning": "Working", "tool_calls": [{
                        "index": 0, "id": "partial", "function": {"name": "write_file", "arguments": "{"},
                    }]})
                    if ending == "exception":
                        raise OSError("connection lost")
                    if ending == "length":
                        yield chunk(finish_reason="length")
                    if ending == "error":
                        event = chunk(choices=False)
                        event.error = {"message": "provider failed"}
                        yield event

                stream = FakeStream(fragments())
                with patch.object(agent, "execute_tool") as execute:
                    if ending == "length":
                        self.assertIn("incomplete", agent.run_agent(FakeClient([stream]), "Work", session,
                            stream=True, on_reasoning_end=lambda: closed.append(True)))
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            agent.run_agent(FakeClient([stream]), "Work", session,
                                stream=True, on_reasoning_end=lambda: closed.append(True))
                    execute.assert_not_called()
                self.assertTrue(stream.closed)
                self.assertEqual(closed, [True])
                self.assertFalse(any(m.get("tool_calls") for m in storage.load_session(session.id).messages))

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
        client = FakeClient([reply(calls=[(f"read_{i}", "memory", {"action": "read", "target": "agent"})]) for i in range(2)])
        self.assertIn("maximum", agent.run_agent(client, "Work", session,
                                               settings=config.Settings(max_rounds=2)))
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(agent.run_agent(FakeClient([reply("Done.")]), "Continue", session), "Done.")

    def test_task_budget_counts_input_and_output_across_rounds_and_resets(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                session = storage.new_session()
                client = FakeClient([
                    reply(calls=[("first", "memory", {"action": "read", "target": "agent"})],
                          usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}),
                    reply(calls=[("second", "memory", {"action": "read", "target": "user"})],
                          usage={"prompt_tokens": 140, "completion_tokens": 10, "total_tokens": 150}),
                    reply("Next task.", usage={"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}),
                ])
                settings = config.Settings(max_output_tokens=200, max_task_tokens=250)
                with patch.object(agent, "execute_tool", wraps=tools.execute_tool) as execute:
                    result = agent.run_agent(client, "Work", session, stream=streaming, settings=settings)
                    self.assertEqual(result, "Stopped: reached the task token budget.")
                    self.assertEqual(execute.call_count, 1)
                self.assertEqual([request["max_tokens"] for request in client.requests], [200, 140])
                if streaming:
                    self.assertTrue(all(request["stream_options"]["include_usage"] for request in client.requests))
                saved = storage.load_session(session.id)
                self.assertIn("Not executed", saved.messages[-2]["content"])
                self.assertEqual(saved.messages[-2]["tool_call_id"], "second")
                self.assertEqual(agent.run_agent(client, "Continue", saved, stream=streaming, settings=settings),
                                 "Next task.")
                self.assertEqual(client.requests[2]["max_tokens"], 200)

    def test_task_budget_stops_at_exact_limit_and_allows_completed_answers(self):
        usage = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                settings = config.Settings(max_task_tokens=25)
                response = reply(calls=[("not_run", "memory", {"action": "read", "target": "agent"})], usage=usage)
                client = FakeClient([response])
                with patch.object(agent, "execute_tool") as execute:
                    self.assertIn("task token budget", agent.run_agent(client, "Work", stream=streaming, settings=settings))
                    execute.assert_not_called()
                self.assertEqual(client.requests[0]["max_tokens"], 25)
                self.assertEqual(agent.run_agent(FakeClient([reply("Complete.", usage=usage)]), "Work",
                                                stream=streaming, settings=settings), "Complete.")

    def test_task_budget_requires_usage_before_executing_tools(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                session = storage.new_session()
                client = FakeClient([reply(calls=[("not_run", "memory", {"action": "read", "target": "agent"})])])
                with patch.object(agent, "execute_tool") as execute:
                    with self.assertRaisesRegex(ValueError, "requires provider usage"):
                        agent.run_agent(client, "Work", session, stream=streaming,
                                        settings=config.Settings(max_task_tokens=100))
                    execute.assert_not_called()
                self.assertEqual(storage.load_session(session.id).messages[-1]["content"], "Work")

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
