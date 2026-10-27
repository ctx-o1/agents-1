import json
import os
from pathlib import Path

from .storage import PROJECT_ROOT, Session, memory, scratchpad, session_search

WORKSPACE = PROJECT_ROOT / "workspace"
MAX_TOOL_OUTPUT = 20_000
MAX_SEARCH_FILE_SIZE = 1_000_000


def workspace_path(path: str) -> Path:
    target = (WORKSPACE / path).resolve()
    if not target.is_relative_to(WORKSPACE):
        raise ValueError("Path must stay inside the workspace.")
    return target


def list_files(path: str = ".") -> str:
    directory = workspace_path(path)
    entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    return "\n".join(
        f"{entry.name}{'/' if entry.is_dir() else ''}"
        for entry in entries[:200]
    ) or "(empty directory)"


def read_file(path: str) -> str:
    with workspace_path(path).open(encoding="utf-8") as file:
        text = file.read(MAX_TOOL_OUTPUT + 1)
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + "\n[Output truncated]"
    return text


def write_file(path: str, content: str) -> str:
    if len(content) > MAX_TOOL_OUTPUT:
        raise ValueError("File content is too large.")
    target = workspace_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}."


def edit_file(path: str, old_text: str, new_text: str) -> str:
    if not old_text:
        raise ValueError("old_text must not be empty.")
    target = workspace_path(path)
    with target.open(encoding="utf-8") as file:
        content = file.read(MAX_TOOL_OUTPUT + 1)
    if len(content) > MAX_TOOL_OUTPUT:
        raise ValueError("File is too large for edit_file.")
    count = content.count(old_text)
    if count != 1:
        raise ValueError(f"Expected exactly one occurrence of old_text; found {count}.")
    updated = content.replace(old_text, new_text, 1)
    return write_file(path, updated)


def _workspace_files(path: str):
    directory = workspace_path(path)
    if not directory.is_dir():
        raise ValueError("path must name an existing workspace directory.")
    for root, directories, files in os.walk(directory, followlinks=False):
        # Do not traverse directory links, including Windows junctions.
        directories[:] = sorted(
            name for name in directories
            if not (Path(root) / name).is_symlink()
            and not (Path(root) / name).is_junction()
        )
        for name in sorted(files):
            candidate = Path(root) / name
            if candidate.resolve().is_relative_to(WORKSPACE) and candidate.is_file():
                yield candidate


def _format_results(results) -> str:
    lines = []
    size = 0
    for line in results:
        if len(lines) >= 200 or size + len(line) + 1 > MAX_TOOL_OUTPUT:
            return "\n".join(lines) + "\n[Results truncated; narrow your search]"
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines) or "(no matches)"


def search_files(pattern: str, path: str = ".") -> str:
    if not pattern:
        raise ValueError("pattern must not be empty.")

    def matches():
        for candidate in _workspace_files(path):
            try:
                if candidate.stat().st_size > MAX_SEARCH_FILE_SIZE:
                    continue
                with candidate.open(encoding="utf-8") as file:
                    content = file.read(MAX_SEARCH_FILE_SIZE + 1)
            except (OSError, UnicodeError):
                continue
            if len(content) > MAX_SEARCH_FILE_SIZE or "\x00" in content:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if pattern in line:
                    relative = candidate.relative_to(WORKSPACE).as_posix()
                    yield f"{relative}:{number}: {line[:500]}"

    return _format_results(matches())


def find_files(glob: str, path: str = ".") -> str:
    if not glob or Path(glob).anchor or ".." in Path(glob).parts:
        raise ValueError("glob must be a non-empty relative pattern without '..'.")
    directory = workspace_path(path)
    return _format_results(
        candidate.relative_to(WORKSPACE).as_posix()
        for candidate in _workspace_files(path)
        if candidate.relative_to(directory).full_match(glob)
    )


FUNCTIONS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "search_files": search_files,
    "find_files": find_files,
    "session_search": session_search,
    "memory": memory,
    "scratchpad": scratchpad,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List entries in a workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace exactly one occurrence of old_text in a UTF-8 workspace file. "
                "Fails if the text is missing, ambiguous, or the file exceeds 20,000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Recursively search workspace UTF-8 files for a case-sensitive literal string. "
                "Returns workspace paths, line numbers, and line previews. Skips binary, "
                "unreadable, and files larger than 1 MB. Returns at most 200 matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "Find workspace files matching a glob relative to path (default '.'). "
                "Use '**/*.py' for recursive matching or '*.py' for immediate files. "
                "Returns at most 200 workspace-relative paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["glob"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "session_search",
            "description": (
                "Search saved user, assistant, and tool messages for a case-insensitive "
                "literal query. Searches the 100 most recently updated sessions, or one "
                "session_id. Returns at most 20 excerpts with source IDs and message indexes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": (
                "Read or curate durable memory shared across this project's sessions. "
                "target='agent' holds verified project/environment facts (2200 characters); "
                "target='user' holds stated preferences (1375 characters). Add content, "
                "or update/remove an exact entry_id returned by read. Keep one fact per "
                "entry. Updates replace the entry's entire content. A full store rejects writes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "add", "update", "remove"]},
                    "target": {"type": "string", "enum": ["agent", "user"]},
                    "content": {"type": "string"},
                    "entry_id": {"type": "string"},
                },
                "required": ["action", "target"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scratchpad",
            "description": (
                "Read, write, or clear the active session's working notes (4000 characters). "
                "Write replaces the complete scratchpad. Record task goals, constraints, "
                "verified findings, progress, and next steps. It is saved with the session "
                "and included in the next model request. Use memory for durable facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "clear"]},
                    "content": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_tool(name: str, arguments_json: str, *, session: Session | None = None) -> str:
    try:
        if name not in FUNCTIONS:
            raise ValueError(f"Unknown tool: {name}")

        arguments = json.loads(arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")
        if not all(isinstance(value, str) for value in arguments.values()):
            raise ValueError("Tool arguments must be strings.")
        schema = next(tool["function"]["parameters"] for tool in TOOLS if tool["function"]["name"] == name)
        if arguments.keys() - schema["properties"].keys():
            raise ValueError("Unexpected tool arguments.")
        if set(schema.get("required", [])) - arguments.keys():
            raise ValueError("Missing required tool arguments.")

        if name in {"memory", "scratchpad"}:
            result = FUNCTIONS[name](**arguments, session=session)
        else:
            result = FUNCTIONS[name](**arguments)
        return json.dumps({"result": result}, ensure_ascii=False)
    except (ValueError, TypeError, OSError) as error:
        return json.dumps({"error": str(error)})


