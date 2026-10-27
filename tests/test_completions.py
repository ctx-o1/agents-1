"""Exercise the real SDK's SSE parser without making network requests."""

import json
import unittest

import httpx2 as httpx
from openai import OpenAI

from src.completions import complete


class CompletionTransportTests(unittest.TestCase):
    def test_sdk_preserves_openrouter_reasoning_extensions_in_both_modes(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                message = {
                    "role": "assistant", "content": "The answer.",
                    "reasoning": "Thinking aloud.",
                    "reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "Thinking aloud."}],
                }

                def respond(request):
                    self.assertEqual(json.loads(request.content)["stream"], streaming)
                    envelope = {"id": "test", "created": 0, "model": "test"}
                    if streaming:
                        events = [
                            {**envelope, "object": "chat.completion.chunk", "choices": [
                                {"index": 0, "delta": message, "finish_reason": None},
                            ]},
                            {**envelope, "object": "chat.completion.chunk", "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"},
                            ]},
                        ]
                        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
                        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                              text=body + "data: [DONE]\n\n")
                    return httpx.Response(200, json={
                        **envelope, "object": "chat.completion", "choices": [
                            {"index": 0, "message": message, "finish_reason": "stop"},
                        ],
                    })

                seen = []
                with OpenAI(api_key="offline", base_url="https://offline.invalid/v1",
                            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
                    result, finish = complete(client, stream=streaming, model="test",
                                              messages=[], on_reasoning=seen.append)
                self.assertEqual(finish, "stop")
                self.assertEqual(result.content, "The answer.")
                self.assertEqual(result.reasoning_details, message["reasoning_details"])
                self.assertEqual(seen, ["Thinking aloud."])
