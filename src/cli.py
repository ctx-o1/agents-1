"""Small terminal views; no cursor animation or application state."""

import json
import os
import re
import shutil
import sys
import textwrap

from wcwidth import iter_graphemes, wcswidth

ACCENT = "38;5;141"
HEADING = "1;38;5;111"
ERROR = "1;31"


def clean(value) -> str:
    """Remove terminal instructions supplied by model, file, or tool text."""
    text = str(value).replace("\r\n", "\n")
    text = re.sub(r"\x1b\][\s\S]*?(?:\x07|\x1b\\|$)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]", "", text)
    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32 and not 127 <= ord(character) < 160)


def _json(value) -> dict:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


class Terminal:
    def __init__(self, stream=None, color=None):
        self.stream = stream if stream is not None else sys.stdout
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.color = color is not False and self.interactive and "NO_COLOR" not in os.environ and os.getenv("TERM") != "dumb"
        self.width = max(40, min(shutil.get_terminal_size(fallback=(80, 24)).columns, 120))
        self._reasoning_round = None
        self._reasoning_column = 0
        self._reasoning_escape = ""
        self._reasoning_pending = ""
        self._reasoning_line_open = False

    def _style(self, text, code="2"):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, text=""):
        self.stream.write(text + "\n")
        self.stream.flush()

    def _one(self, value, limit=None):
        text = " ".join(clean(value).split())
        limit = limit or self.width
        return text if len(text) <= limit else text[:max(0, limit - 3)] + "..."

    def _body(self, value):
        fence = None
        for line in clean(value).splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
            if fence:
                self._line(line)
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                    fence = None
                continue
            if marker:
                fence = (marker[1][0], len(marker[1]))
                self._line(line)
                continue
            if line.startswith((" ", "\t")) or "|" in line:
                self._line(line)
                continue
            bullet = re.match(r"^(?:[-*+] |\d+[.)] )", line)
            continuation = " " * len(bullet[0]) if bullet else ""
            wrapped = textwrap.fill(line, width=self.width - 2, subsequent_indent=continuation, break_long_words=False, break_on_hyphens=False)
            for piece in wrapped.split("\n"):
                if self.color:
                    if re.match(r"^#{1,6} ", piece):
                        piece = self._style(re.sub(r"^#{1,6} ", "", piece), HEADING)
                    else:
                        piece = re.sub(r"\*\*([^*\n]+)\*\*", lambda match: self._style(match[1], "1"), piece)
                        piece = re.sub(r"`([^`\n]+)`", lambda match: self._style(match[1], ACCENT), piece)
                self._line("  " + piece)

    def header(self, session_id, model):
        self._line(self._style("agent(-1)", HEADING))
        self._line(self._style("session  " + " ".join(clean(session_id).split())))
        self._line(self._style("model    " + " ".join(clean(model).split())))

    def prompt(self):
        symbol = "❯" if self.interactive else ">"
        try:
            symbol.encode(getattr(self.stream, "encoding", None) or "utf-8")
        except (UnicodeEncodeError, LookupError):
            symbol = ">"
        return "\n" + self._style(symbol, "1;" + ACCENT) + " "

    def info(self, message):
        text = " ".join(clean(message).split())
        self._line(self._style(textwrap.fill(text, width=self.width, initial_indent="  ", subsequent_indent="  ", break_long_words=False, break_on_hyphens=False)))

    def status(self, message):
        if self.interactive:
            self.info(message)

    def error(self, message):
        self._line()
        self._line(self._style("error", ERROR))
        self._body(message)

    def answer(self, text):
        self.section("agent(-1)", text)

    def reasoning(self, round_number, text):
        """Flush complete graphemes with a quiet gutter and bounded display width."""
        if self._reasoning_round != round_number:
            self.end_reasoning()
            self._reasoning_round = round_number
            self._line()
            self._line(self._style(f"Reasoning / round {round_number}", ACCENT))
        sanitized = []
        for character in text:
            # Escape sequences can span network chunks. Never print their tails.
            state = self._reasoning_escape
            if state == "esc":
                self._reasoning_escape = {"[": "csi", "]": "osc", "P": "osc", "^": "osc", "_": "osc"}.get(character, "")
                continue
            if state == "csi":
                if "@" <= character <= "~":
                    self._reasoning_escape = ""
                continue
            if state in {"osc", "osc_esc"}:
                if character == "\x07" or state == "osc_esc" and character == "\\":
                    self._reasoning_escape = ""
                else:
                    self._reasoning_escape = "osc_esc" if character == "\x1b" else "osc"
                continue
            if character == "\x1b":
                self._reasoning_escape = "esc"
                continue
            if character != "\n" and (ord(character) < 32 or 127 <= ord(character) < 160):
                if character != "\t":
                    continue
                character = " "
            sanitized.append(character)
        self._render_reasoning("".join(sanitized))

    def _render_reasoning(self, text, *, final=False):
        # Hold the last grapheme: a later chunk may extend it with a combining
        # mark, variation selector, or another part of a joined emoji.
        clusters = list(iter_graphemes(self._reasoning_pending + text))
        self._reasoning_pending = ""
        if clusters and not final and clusters[-1] != "\n":
            self._reasoning_pending = clusters.pop()
        visible = []
        for cluster in clusters:
            if cluster == "\n":
                visible.append("\n")
                self._reasoning_column = 0
                self._reasoning_line_open = False
                continue
            cells = max(0, wcswidth(cluster))
            if self._reasoning_line_open and self._reasoning_column + cells > self.width - 4:
                visible.append("\n")
                self._reasoning_column = 0
                self._reasoning_line_open = False
            if not self._reasoning_line_open:
                visible.append("  | ")
                self._reasoning_line_open = True
            visible.append(cluster)
            self._reasoning_column += cells
        self.stream.write(self._style("".join(visible)))
        self.stream.flush()

    def end_reasoning(self):
        if self._reasoning_round is not None:
            self._render_reasoning("", final=True)
            if self._reasoning_line_open:
                self._line()
            self._line()
        self._reasoning_round = None
        self._reasoning_column = 0
        self._reasoning_escape = ""
        self._reasoning_pending = ""
        self._reasoning_line_open = False

    def section(self, title, body):
        self._line()
        self._line(self._style(clean(title), "1;" + ACCENT if title in {"user", "you"} else HEADING))
        self._body(body or "(empty)")
        self._line()

    def help(self):
        self.section("Commands", "\n".join(f"{command:<17} {description}" for command, description in (
            ("/new", "Start a fresh session"),
            ("/sessions", "List saved sessions"),
            ("/resume <id>", "Continue a saved session"),
            ("/history", "Show recent messages"),
            ("/memory", "Inspect lasting facts and preferences"),
            ("/notes", "Inspect working notes"),
            ("/notes clear", "Clear working notes"),
            ("/help", "Show this list"),
            ("/exit", "Save and leave"),
        )))

    def sessions(self, items, current_id):
        self._line()
        self._line(self._style("Sessions", HEADING))
        if not items:
            self.info("No saved sessions yet.")
            return
        id_width = max(len(clean(item["id"])) for item in items)
        for item in items:
            session_id = " ".join(clean(item["id"]).split())
            title = item.get("error") or item.get("title") or "(empty)"
            marker = "*" if item["id"] == current_id else " "
            self._line(f" {marker} {session_id:<{id_width}}  {self._one(title, max(12, self.width - id_width - 5))}")
        self._line(self._style("  * current session"))

    def history(self, messages):
        visible = [message for message in messages if message["role"] != "system"][-20:]
        if not visible:
            self.section("History", "No messages yet.")
        for message in visible:
            body = message.get("content") or ", ".join(call["function"]["name"] for call in message.get("tool_calls", []))
            if message["role"] == "tool":
                result = _json(body)
                value = result.get("error", result.get("result", body))
                if isinstance(value, str):
                    body = value
                elif isinstance(value, dict):
                    if "content" in value:
                        body = str(value["content"])
                    elif "matches" in value:
                        body = f"{len(value['matches'])} history matches" + (" [results truncated]" if value.get("truncated") else "")
                    elif "entries" in value:
                        body = f"{value.get('target', '')} memory: {len(value['entries'])} entries"
                    else:
                        body = "Structured result: " + ", ".join(value)
                else:
                    body = f"{len(value)} items" if isinstance(value, list) else str(value)
            self.section(message["role"], body[:500] + ("\n[preview truncated]" if len(body) > 500 else ""))

    def memory(self, stores):
        for target, state in stores.items():
            entries = state["entries"]
            self._line()
            self._line(self._style(f"{clean(target)} memory  {state['characters']}/{state['limit']} characters", HEADING))
            if not entries:
                self.info("No entries yet.")
            for entry in entries:
                self._line(self._style("  " + self._one(entry["id"])))
                self._body(entry["content"])

    def tool(self, round_number, name, arguments_json, result_json, verbose=False):
        arguments = _json(arguments_json)
        result = _json(result_json)
        target = arguments.get("path") or arguments.get("action") or arguments.get("glob") or ""
        if name == "memory":
            target = f"{arguments.get('action', '')} {arguments.get('target', '')}"
        status = "error" if "error" in result else "ok"
        line = f"[{round_number}] {status}  {clean(name)}"
        if target:
            line += "  " + self._one(target, 45)
        if "error" in result:
            line += ": " + self._one(result["error"], 100)
        prefix, _, detail = self._one(line).partition(status)
        self._line("  " + self._style(prefix) + self._style(status, ERROR if status == "error" else ACCENT) + self._style(detail))
        if verbose:
            detail = clean(result_json)
            self._body(detail[:2000] + ("\n[preview truncated]" if len(detail) > 2000 else ""))
