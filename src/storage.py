"""JSON sessions and Markdown memory; one process should write them at a time."""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DATA_DIR

SESSIONS_DIR = DATA_DIR / "sessions"
MEMORY_FILES = {"agent": DATA_DIR / "MEMORY.md", "user": DATA_DIR / "USER.md"}
MEMORY_LIMITS = {"agent": 2200, "user": 1375}
SCRATCHPAD_LIMIT = 4000
MAX_SESSION_BYTES = 10_000_000
MAX_SEARCH_SESSIONS = 100


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    with path.open("rb") as file:
        raw = file.read(MAX_SESSION_BYTES + 1)
    if len(raw) > MAX_SESSION_BYTES:
        raise ValueError(f"{path.name} exceeds the 10 MB loading limit.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def _session_path(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
        raise ValueError("Invalid session ID.")
    path = (SESSIONS_DIR / f"{session_id}.json").resolve()
    if not path.is_relative_to(SESSIONS_DIR.resolve()):
        raise ValueError("Session path must stay inside sessions/.")
    return path


@dataclass
class Session:
    id: str
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    messages: list[dict] = field(default_factory=list)
    scratchpad: str = ""
    memory_snapshot: dict = field(default_factory=dict)
    version: int = 1


def _validate_memory(target: str, entries) -> None:
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("id"), str)
        or not isinstance(entry.get("content"), str)
        or not entry["id"] or not entry["content"].strip()
        or len(entry["id"]) > 80
        or not isinstance(entry.get("updated_at"), str)
        or len(entry["updated_at"]) > 64
        or not isinstance(entry.get("source_session_id"), str)
        or len(entry["source_session_id"]) > 80
        or set(entry) - {"id", "content", "updated_at", "source_session_id"}
        for entry in entries
    ):
        raise ValueError(f"Invalid {target} memory entries.")
    if len({entry["id"] for entry in entries}) != len(entries):
        raise ValueError(f"Duplicate IDs in {target} memory.")
    if sum(len(entry["content"]) + 1 for entry in entries) > MEMORY_LIMITS[target]:
        raise ValueError(f"{target} memory exceeds its character limit.")


def _read_memory_md(target: str) -> list[dict]:
    path = MEMORY_FILES[target]
    if not path.exists():
        return []
    with path.open("rb") as file:
        raw = file.read(MAX_SESSION_BYTES + 1)
    if len(raw) > MAX_SESSION_BYTES:
        raise ValueError(f"{path.name} exceeds the 10 MB loading limit.")
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
    if text.startswith("# "):
        text = text.partition("\n")[2].strip()
    entries = []
    for block in re.split(r"(?m)^---[ \t]*$", text):
        block = block.strip()
        if not block:
            continue
        first, _, rest = block.partition("\n")
        if first.startswith("<!-- agent-memory"):
            match = re.fullmatch(r"<!-- agent-memory (\{.*\}) -->", first)
            if match is None:
                raise ValueError(f"Malformed entry metadata in {path.name}.")
            metadata = json.loads(match[1])
            if not isinstance(metadata, dict):
                raise ValueError(f"Invalid entry metadata in {path.name}.")
            entry = {**metadata, "content": rest.strip()}
        else:
            # Plain manually added entries get reproducible IDs until next saved.
            entry = {
                "id": hashlib.sha256(block.encode("utf-8")).hexdigest()[:12],
                "content": block, "updated_at": "", "source_session_id": "",
            }
        entries.append(entry)
    _validate_memory(target, entries)
    return entries


def _write_memory_md(target: str, entries: list[dict]) -> None:
    _validate_memory(target, entries)
    blocks = []
    for entry in entries:
        if re.search(r"(?m)^(?:---[ \t]*$|<!-- agent-memory)", entry["content"]):
            raise ValueError("Memory content cannot contain reserved entry separators or metadata lines.")
        metadata = {key: entry[key] for key in ("id", "updated_at", "source_session_id")}
        blocks.append("<!-- agent-memory " + json.dumps(metadata) + " -->\n" + entry["content"].strip())
    title = "Agent memory" if target == "agent" else "User preferences"
    text = f"# {title}\n\n" + "\n\n---\n\n".join(blocks)
    _atomic_text(MEMORY_FILES[target], text.rstrip() + "\n")


def read_memory() -> dict:
    return {target: _read_memory_md(target) for target in MEMORY_LIMITS}


def new_session() -> Session:
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    return Session(id=session_id, memory_snapshot=read_memory())


def save_session(session: Session) -> None:
    session.updated_at = now()
    data = asdict(session)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if len(serialized.encode("utf-8")) > MAX_SESSION_BYTES:
        raise ValueError("Session exceeds 10 MB. Start a new session with /new.")
    _atomic_text(_session_path(session.id), serialized)


def validate_tool_ids(calls: list[dict]) -> None:
    """Validate one assistant's API-shaped tool-call batch before side effects."""
    seen = set()
    for call in calls:
        call_id = call.get("id") if isinstance(call, dict) else None
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("Tool-call IDs must be non-empty strings.")
        if call_id in seen:
            raise ValueError("Duplicate tool-call IDs are not allowed.")
        seen.add(call_id)


def close_pending_tools(messages: list[dict]) -> None:
    """Mark incomplete calls as uncertain, so resuming never replays side effects."""
    repaired = []
    pending = {}

    def close():
        for call_id in pending:
            repaired.append({
                "role": "tool", "tool_call_id": call_id,
                "content": json.dumps({"error": (
                    "Execution was interrupted. This tool may or may not have run. "
                    "Inspect the current state before retrying."
                )}),
            })
        pending.clear()

    for message in messages:
        if message["role"] != "tool":
            close()
        elif message.get("tool_call_id") not in pending:
            raise ValueError("Session contains an unmatched tool result.")
        repaired.append(message)
        if message["role"] == "assistant":
            calls = message.get("tool_calls", [])
            validate_tool_ids(calls)
            for call in calls:
                pending[call["id"]] = call
        elif message["role"] == "tool":
            pending.pop(message["tool_call_id"])
    close()
    messages[:] = repaired


def _read_session(session_id: str) -> Session:
    data = _read_json(_session_path(session_id))
    if data.get("version") != 1 or data.get("id") != session_id:
        raise ValueError("Invalid session ID or version in session file.")
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Session messages must be a list.")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("Invalid session message.")
        if message["role"] == "system" and index != 0:
            raise ValueError("Only the first message may be a system message.")
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ValueError("Session content must be text.")
        if message["role"] in {"system", "user", "tool"} and not isinstance(message.get("content"), str):
            raise ValueError("System, user, and tool messages must contain text.")
        if message["role"] == "tool" and not isinstance(message.get("tool_call_id"), str):
            raise ValueError("Tool results must have a tool_call_id.")
        calls = message.get("tool_calls", [])
        if calls and message["role"] != "assistant":
            raise ValueError("Only assistant messages may request tools.")
        if not isinstance(calls, list) or any(
            not isinstance(call, dict)
            or call.get("type") != "function"
            or not isinstance(call.get("function"), dict)
            or not isinstance(call["function"].get("name"), str)
            or not isinstance(call["function"].get("arguments"), str)
            for call in calls
        ):
            raise ValueError("Invalid saved tool call.")
    scratchpad = data.get("scratchpad", "")
    if not isinstance(scratchpad, str) or len(scratchpad) > SCRATCHPAD_LIMIT:
        raise ValueError("Invalid session scratchpad.")
    close_pending_tools(messages)
    return Session(
        id=session_id, created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""), messages=messages,
        scratchpad=scratchpad,
    )


def load_session(session_id: str) -> Session:
    session = _read_session(session_id)
    session.memory_snapshot = read_memory()
    return session


def _recent_session_ids():
    if SESSIONS_DIR.exists():
        paths = sorted(SESSIONS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths:
            yield path.stem


def list_sessions() -> list[dict]:
    result = []
    for session_id in _recent_session_ids():
        try:
            session = _read_session(session_id)
            title = next((m.get("content", "") for m in session.messages if m["role"] == "user"), "")
            result.append({"id": session.id, "updated_at": session.updated_at, "title": title[:80]})
        except (OSError, ValueError):
            result.append({"id": session_id, "error": "Unreadable or invalid session"})
    return result


def session_search(query: str, session_id: str = "") -> dict:
    if not query.strip():
        raise ValueError("query must not be empty.")
    if session_id:
        _session_path(session_id)
        ids = [session_id]
    else:
        ids = list(_recent_session_ids())
    matches = []
    searched = skipped = 0
    query = query.casefold()
    for saved_id in ids[:MAX_SEARCH_SESSIONS]:
        try:
            saved = _read_session(saved_id)
        except (OSError, ValueError):
            if session_id:
                raise
            skipped += 1
            continue
        searched += 1
        for index, message in enumerate(saved.messages):
            if message["role"] == "system":
                continue
            content = message.get("content") or ""
            position = content.casefold().find(query)
            if position < 0:
                continue
            if len(matches) == 20:
                return {"matches": matches, "truncated": True, "searched_sessions": searched, "skipped_sessions": skipped}
            start = max(0, position - 160)
            matches.append({
                "session_id": saved.id, "message_index": index,
                "role": message["role"], "excerpt": content[start:start + 800],
            })
    return {
        "matches": matches, "truncated": len(ids) > MAX_SEARCH_SESSIONS,
        "searched_sessions": searched, "skipped_sessions": skipped,
    }


def memory(action: str = "read", target: str = "agent", content: str = "", entry_id: str = "", *, session: Session | None = None) -> dict:
    if target not in MEMORY_LIMITS:
        raise ValueError("target must be 'agent' or 'user'.")
    content = content.strip()
    stores = read_memory()
    entries = stores[target]
    if action == "add":
        if not content:
            raise ValueError("content must not be empty.")
        if not any(entry["content"] == content for entry in entries):
            entries.append({"id": uuid4().hex[:12], "content": content, "updated_at": now(), "source_session_id": session.id if session else ""})
    elif action in {"update", "remove"}:
        entry = next((entry for entry in entries if entry["id"] == entry_id), None)
        if entry is None:
            raise ValueError("entry_id does not identify an existing memory.")
        if action == "remove":
            entries.remove(entry)
        else:
            if not content:
                raise ValueError("content must not be empty.")
            entry.update(content=content, updated_at=now(), source_session_id=session.id if session else "")
    elif action != "read":
        raise ValueError("action must be read, add, update, or remove.")
    used = sum(len(entry["content"]) + 1 for entry in entries)
    if used > MEMORY_LIMITS[target]:
        raise ValueError(f"Memory full ({used}/{MEMORY_LIMITS[target]} characters). Shorten or remove entries first.")
    if action != "read":
        _write_memory_md(target, entries)
    return {"target": target, "entries": entries, "characters": used, "limit": MEMORY_LIMITS[target]}


def scratchpad(action: str = "read", content: str = "", *, session: Session | None = None) -> dict:
    if session is None:
        raise ValueError("scratchpad requires an active session.")
    if action not in {"read", "write", "clear"}:
        raise ValueError("action must be read, write, or clear.")
    if len(content) > SCRATCHPAD_LIMIT:
        raise ValueError(f"Scratchpad exceeds {SCRATCHPAD_LIMIT} characters.")
    if action != "read":
        previous = session.scratchpad
        session.scratchpad = content if action == "write" else ""
        try:
            save_session(session)
        except (OSError, ValueError):
            session.scratchpad = previous
            raise
    return {"content": session.scratchpad, "limit": SCRATCHPAD_LIMIT}
