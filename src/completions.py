"""Normalize buffered and streamed OpenRouter chat completions."""

from openai.types.chat import ChatCompletionMessage


class _ReasoningDetails:
    """Track blocks by stable identity, independently of chunk array positions."""

    def __init__(self):
        self.blocks = []
        self.aliases = {}
        self.sequence = 0

    def add(self, detail, position):
        keys = []
        if detail.get("index") is not None:
            keys.append(("index", detail["index"]))
        if detail.get("id"):
            keys.append(("id", detail["id"]))
        if not keys:
            keys.append(("anonymous", detail.get("type"), position))
        matches = []
        for key in keys:
            if key in self.aliases:
                block = self.aliases[key]
                if not any(block is match for match in matches):
                    matches.append(block)
        if matches:
            saved = min(matches, key=lambda block: block[0][0])
            for other in matches:
                if other is saved:
                    continue
                saved.extend(other)
                self.blocks.remove(other)
                for alias, block in self.aliases.items():
                    if block is other:
                        self.aliases[alias] = saved
        else:
            saved = []
            self.blocks.append(saved)
        for key in keys:
            self.aliases[key] = saved
        saved.append((self.sequence, detail))
        self.sequence += 1

    def ordered(self):
        blocks = []
        for fragments in self.blocks:
            saved = {}
            # Late ID/index associations can join blocks whose deltas interleave.
            for _, detail in sorted(fragments, key=lambda fragment: fragment[0]):
                for name, value in detail.items():
                    if name in {"text", "summary", "signature", "data"} and isinstance(value, str):
                        saved[name] = (saved.get(name) or "") + value
                    elif value is not None:
                        saved[name] = value
            blocks.append(saved)
        if all(isinstance(block.get("index"), int) for block in blocks):
            return sorted(blocks, key=lambda block: block["index"])
        return blocks


def reasoning_text(message):
    text = message.get("reasoning") or message.get("reasoning_content")
    if text:
        return text
    return "".join(
        detail.get("text", "") or detail.get("summary", "")
        for detail in message.get("reasoning_details", []) or []
        if detail.get("type") in {"reasoning.text", "reasoning.summary"}
    )


def complete(client, *, stream=False, on_reasoning=None, on_usage=None, **request):
    if stream and on_usage:
        request["stream_options"] = {"include_usage": True}
    response = client.chat.completions.create(stream=stream, **request)
    if not stream:
        if not response.choices:
            raise ValueError("The model returned no choices.")
        choice = response.choices[0]
        if on_reasoning and (text := reasoning_text(choice.message.model_dump())):
            on_reasoning(text)
        if on_usage:
            on_usage(response.usage)
        return choice.message, choice.finish_reason

    message = {"role": "assistant", "content": None}
    content, reasoning, calls = [], [], {}
    details = _ReasoningDetails()
    finish_reason = None
    usage = None
    with response:
        for chunk in response:
            if error := getattr(chunk, "error", None):
                raise ValueError(f"Model stream failed: {error}")
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                delta = choice.delta.model_dump(exclude_none=True)
                if on_reasoning and (text := reasoning_text(delta)):
                    on_reasoning(text)
                if delta.get("content"):
                    content.append(delta["content"])
                if text := delta.get("reasoning") or delta.get("reasoning_content"):
                    reasoning.append(text)
                for position, detail in enumerate(delta.get("reasoning_details", []) or []):
                    details.add(detail, position)
                for part in choice.delta.tool_calls or []:
                    call = calls.setdefault(part.index, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if part.id:
                        call["id"] += part.id
                    if part.function:
                        for name in ("name", "arguments"):
                            call["function"][name] += getattr(part.function, name) or ""
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
    if finish_reason is None:
        raise ValueError("Model stream ended before completion; no tools were executed.")
    if on_usage:
        on_usage(usage)
    if content:
        message["content"] = "".join(content)
    if reasoning:
        message["reasoning"] = "".join(reasoning)
    if details.blocks:
        message["reasoning_details"] = details.ordered()
    if calls:
        message["tool_calls"] = [calls[key] for key in sorted(calls)]
    return ChatCompletionMessage.model_validate(message), finish_reason
