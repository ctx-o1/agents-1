import json
from pathlib import Path

WORKSPACE = (Path(__file__).resolve().parent / "workspace").resolve()
MAX_TOOL_OUTPUT = 20_000


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


FUNCTIONS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
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
]


def execute_tool(name: str, arguments_json: str) -> str:
    try:
        if name not in FUNCTIONS:
            raise ValueError(f"Unknown tool: {name}")

        arguments = json.loads(arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")
        if not all(isinstance(value, str) for value in arguments.values()):
            raise ValueError("Tool arguments must be strings.")

        result = FUNCTIONS[name](**arguments)
        return json.dumps({"result": result}, ensure_ascii=False)
    except (ValueError, TypeError, OSError) as error:
        return json.dumps({"error": str(error)})


