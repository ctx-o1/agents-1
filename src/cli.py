"""Terminal rendering, input editing, and shared CLI setup."""

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import time
from functools import partial
from pathlib import Path

from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.constrain import Constrain
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from wcwidth import iter_graphemes, wcswidth, wrap

from .config import Settings, load_settings

ACCENT = "38;5;141"
HEADING = "1;38;5;111"
ERROR = "1;31"
COMMANDS = (
    ("/new", "Start a fresh session"),
    ("/sessions", "List saved sessions"),
    ("/resume", "Continue a saved session: /resume <id>"),
    ("/history", "Show recent messages"),
    ("/memory", "Inspect lasting facts and preferences"),
    ("/notes", "Inspect working notes; /notes clear to clear"),
    ("/help", "Show this list"),
    ("/exit", "Save and leave"),
)


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


class _StreamBlock:
    """One append-only stream; hold incomplete escapes and the last grapheme."""

    def __init__(self, terminal, prefix="", wrap_lines=False, style=None):
        self.terminal = terminal
        self.prefix = prefix
        self.wrap_lines = wrap_lines
        self.style = style
        self.escape = ""
        self.pending = ""
        self.column = 0
        self.line_open = False
        self.word = []
        self.word_cells = 0
        self.space_pending = False

    def _flush_word(self, visible):
        if not self.word:
            return
        available = max(1, self.terminal.width - wcswidth(self.prefix) - self.terminal.margin)
        gap = " " if self.space_pending and self.line_open else ""
        if self.line_open and self.column + len(gap) + self.word_cells > available:
            visible.append("\n")
            self.column = 0
            self.line_open = False
            gap = ""
        if gap:
            visible.append(gap)
            self.column += 1
        for cluster in self.word:
            cells = max(0, wcswidth(cluster))
            if self.line_open and self.column + cells > available:
                visible.append("\n")
                self.column = 0
                self.line_open = False
            if not self.line_open:
                visible.append(self.prefix)
                self.line_open = True
            visible.append(cluster)
            self.column += cells
        self.word.clear()
        self.word_cells = 0
        self.space_pending = False

    def write(self, text, *, final=False):
        sanitized = []
        for character in text:
            state = self.escape
            if state == "esc":
                self.escape = {"[": "csi", "]": "osc", "P": "osc", "^": "osc", "_": "osc"}.get(character, "")
                continue
            if state == "csi":
                if "@" <= character <= "~":
                    self.escape = ""
                continue
            if state in {"osc", "osc_esc"}:
                if character == "\x07" or state == "osc_esc" and character == "\\":
                    self.escape = ""
                else:
                    self.escape = "osc_esc" if character == "\x1b" else "osc"
                continue
            if character == "\x1b":
                self.escape = "esc"
                continue
            if character not in "\n\t" and (ord(character) < 32 or 127 <= ord(character) < 160):
                continue
            sanitized.append(" " if character == "\t" and self.wrap_lines else character)

        clusters = list(iter_graphemes(self.pending + "".join(sanitized)))
        self.pending = ""
        if clusters and not final and clusters[-1] != "\n":
            self.pending = clusters.pop()
        visible = []
        for cluster in clusters:
            if self.wrap_lines:
                if cluster == "\n":
                    self._flush_word(visible)
                    visible.append("\n")
                    self.column = 0
                    self.line_open = False
                    self.space_pending = False
                elif cluster.isspace():
                    self._flush_word(visible)
                    self.space_pending = True
                else:
                    cells = max(0, wcswidth(cluster))
                    available = max(1, self.terminal.width - wcswidth(self.prefix) - self.terminal.margin)
                    if self.word_cells + cells > available or len(self.word) >= 256:
                        self._flush_word(visible)
                    self.word.append(cluster)
                    self.word_cells += cells
                continue
            if cluster == "\n":
                visible.append("\n")
                self.column = 0
                self.line_open = False
                continue
            if not self.line_open:
                visible.append(self.prefix)
                self.line_open = True
            visible.append(cluster)
        if self.wrap_lines and (final or self.pending.isspace()):
            self._flush_word(visible)
        rendered = "".join(visible)
        self.terminal.stream.write(self.terminal._style(rendered, self.style) if self.style and rendered else rendered)
        self.terminal.stream.flush()

    def close(self):
        self.write("", final=True)
        if self.line_open:
            self.terminal._line()
        self.terminal._line()


class _MarkdownStream:
    """Refresh one transient draft, then commit its complete body to scrollback."""

    def __init__(self, terminal):
        self.terminal = terminal
        self.fragments = []
        self.live = None
        self.next_refresh = 0.0
        self.closed = False

    def write(self, text):
        if self.closed:
            return
        self.fragments.append(text)
        now = time.monotonic()
        if now < self.next_refresh:
            return
        renderable = Constrain(self.terminal._markdown("".join(self.fragments)), self.terminal.width)
        if self.live is None:
            self.live = Live(renderable, console=self.terminal.console, auto_refresh=False,
                             transient=True, screen=False, redirect_stdout=False,
                             redirect_stderr=False, vertical_overflow="ellipsis")
            self.live.start(refresh=True)
        else:
            self.live.update(renderable, refresh=True)
        self.next_refresh = now + 1 / 12

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.live is not None:
            # Rich's stop performs a last overflow-visible refresh. Empty the
            # draft first so a tall response cannot spill into scrollback twice.
            try:
                self.live.update(Text(""), refresh=True)
            finally:
                self.live.stop()
        self.terminal._body("".join(self.fragments))
        self.terminal._line()


class Terminal:
    def __init__(self, stream=None, color=None):
        self.stream = stream if stream is not None else sys.stdout
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.color = color is not False and self.interactive and "NO_COLOR" not in os.environ and os.getenv("TERM") != "dumb"
        self._width = None
        self.console = Console(file=self.stream, force_terminal=self.interactive,
                               color_system="auto" if self.color else None,
                               highlight=False, markup=False)
        self._reasoning_round = None
        self._reasoning_block = None
        self._text_round = None
        self._text_block = None
        self._active_tool = None

    @property
    def width(self):
        return self._width or max(8, min(shutil.get_terminal_size(fallback=(80, 24)).columns, 120))

    @width.setter
    def width(self, value):
        self._width = value

    @property
    def margin(self):
        return 1 if self.interactive and os.getenv("TERM") != "dumb" else 0

    @property
    def body_indent(self):
        # Total inset, including the outer margin; do not add it a second time.
        return 2

    def clear(self):
        """Clear the interactive viewport at startup, preserving scrollback."""
        if self.margin and sys.stdin.isatty():
            self.console.clear(home=True)

    def _section_heading(self, title, code=HEADING):
        title = clean(title)
        if self.margin:
            marker = "•"
            try:
                marker.encode(getattr(self.stream, "encoding", None) or "utf-8")
            except UnicodeEncodeError:
                marker = "."
            title = " " * self.margin + marker + " " + title
        self._line(self._style(title, code))

    def _markdown(self, value):
        return Padding(Markdown(clean(value), code_theme="ansi_dark", hyperlinks=False),
                       (0, self.margin, 0, self.body_indent))

    def _style(self, text, code="2"):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, text=""):
        self.stream.write(text + "\n")
        self.stream.flush()

    def _one(self, value, limit=None):
        text = " ".join(clean(value).split())
        limit = self.width if limit is None else max(0, limit)
        if wcswidth(text) <= limit:
            return text
        suffix = "." * min(3, limit)
        result, cells = [], 0
        for cluster in iter_graphemes(text):
            size = max(0, wcswidth(cluster))
            if cells + size > limit - len(suffix):
                break
            result.append(cluster)
            cells += size
        return "".join(result) + suffix

    def _body(self, value):
        if self.interactive and os.getenv("TERM") != "dumb":
            self.console.print(self._markdown(value), width=self.width)
            return
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
            wrapped = wrap(line, width=max(1, self.width - 2), subsequent_indent=continuation,
                           break_long_words=True, break_on_hyphens=False)
            for piece in wrapped or [""]:
                self._line("  " + piece)

    def header(self, session_id, model):
        if self.margin and self.width >= 64:
            logo = Text(r"""  __     ___ __
 / / __ <  / \ \
| | /_/ / /  | |
 \_\   /_/  /_/""", style="bold cyan")
            details = Text()
            details.append("agent(-1)", style="bold cyan")
            for label, value in (("model", model), ("session", session_id), ("project", str(Path.cwd()))):
                details.append(f"\n{label:<8}", style="dim")
                details.append(" ".join(clean(value).split()))
            header = Table.grid(padding=(0, 3), expand=True)
            header.add_column(width=18, no_wrap=True)
            header.add_column(ratio=1)
            header.add_row(logo, details)
            self.console.print(Padding(header, (1, self.margin, 1, self.margin)), width=self.width)
            return
        if self.margin:
            self._line()
        self._line(self._style(" " * self.margin + "agent(-1)", HEADING))
        indent = " " * self.body_indent if self.margin else ""
        self._line(self._style(indent + "session  " + " ".join(clean(session_id).split())))
        self._line(self._style(indent + "model    " + " ".join(clean(model).split())))

    def prompt(self):
        symbol = "❯" if self.interactive else ">"
        try:
            symbol.encode(getattr(self.stream, "encoding", None) or "utf-8")
        except (UnicodeEncodeError, LookupError):
            symbol = ">"
        return "\n" + " " * self.margin + self._style(symbol, "1;" + ACCENT) + " "

    def info(self, message):
        text = " ".join(clean(message).split())
        # Keep identifiers and paths copyable; wrap at words without splitting them.
        indent = " " * self.body_indent
        for line in wrap(text, width=self.width, initial_indent=indent, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False) or [""]:
            self._line(self._style(line))

    def status(self, message):
        if self.interactive:
            self.info(message)

    def error(self, message):
        self._line()
        self._section_heading("error", ERROR)
        self._body(message)

    def answer(self, text):
        self.section("", text)

    def text(self, round_number, text):
        """Stream sanitized assistant text without repainting or breaking code lines."""
        if not text:
            return
        self.end_reasoning()
        if self._text_round != round_number:
            self.end_text()
            self._text_round = round_number
            self._text_block = _MarkdownStream(self) if self.interactive and os.getenv("TERM") != "dumb" else _StreamBlock(self)
            self._line()
        self._text_block.write(text)

    def end_text(self):
        if self._text_block is not None:
            self._text_block.close()
        self._text_round = None
        self._text_block = None

    def reasoning(self, round_number, text):
        """Stream complete words with quiet padding and grapheme-safe long words."""
        self.end_text()
        if self._reasoning_round != round_number:
            self.end_reasoning()
            self._reasoning_round = round_number
            self._reasoning_block = _StreamBlock(self, prefix=" " * self.body_indent, wrap_lines=True, style="2;3")
            self._line()
            if self.interactive and os.getenv("TERM") != "dumb":
                self._section_heading(f"thinking  round {round_number}", "2")
            else:
                self._line(f"Reasoning / round {round_number}")
        self._reasoning_block.write(text)

    def end_reasoning(self):
        if self._reasoning_block is not None:
            self._reasoning_block.close()
        self._reasoning_round = None
        self._reasoning_block = None

    def section(self, title, body):
        self.end_text()
        self.end_reasoning()
        self._line()
        if title:
            self._section_heading(title, "1;" + ACCENT if title in {"user", "you"} else HEADING)
        self._body(body or "(empty)")
        self._line()

    def help(self):
        self.section("Commands", "\n".join(f"{command:<12} {description}" for command, description in COMMANDS))

    def sessions(self, items, current_id):
        self._line()
        self._section_heading("Sessions")
        if not items:
            self.info("No saved sessions yet.")
            return
        id_width = max(len(clean(item["id"])) for item in items)
        for item in items:
            session_id = " ".join(clean(item["id"]).split())
            title = item.get("error") or item.get("title") or "(empty)"
            marker = "*" if item["id"] == current_id else " "
            available = max(1, self.width - self.body_indent - id_width - 4)
            self._line(" " * self.body_indent + f"{marker} {session_id:<{id_width}}  {self._one(title, available)}")
        self._line(self._style(" " * self.body_indent + "* current session"))

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
            self._section_heading(f"{clean(target)} memory  {state['characters']}/{state['limit']} characters")
            if not entries:
                self.info("No entries yet.")
            for entry in entries:
                self._line(self._style(" " * self.body_indent + self._one(entry["id"])))
                self._body(entry["content"])

    def _tool_summary(self, round_number, name, arguments, status, detail=""):
        target = arguments.get("path") or arguments.get("action") or arguments.get("glob") or ""
        if name == "memory":
            target = f"{arguments.get('action', '')} {arguments.get('target', '')}"
        line = f"[{round_number}] {status}  {clean(name)}"
        if target:
            line += "  " + self._one(target, 45)
        if detail:
            line += "  " + self._one(detail, 100)
        line = self._one(line, self.width - self.body_indent)
        prefix, _, rest = line.partition(status)
        code = ERROR if status == "error" else "32" if status == "ok" else ACCENT
        self._line(" " * self.body_indent + self._style(prefix) + self._style(status, code) + self._style(rest))

    def tool_started(self, round_number, name, arguments_json):
        self.end_reasoning()
        self.end_text()
        self._active_tool = ((round_number, name, arguments_json), time.monotonic())
        arguments = _json(arguments_json)
        self._tool_summary(round_number, name, arguments, "running")
        if name == "edit_file" and self.interactive and os.getenv("TERM") != "dumb":
            old, new = arguments.get("old_text"), arguments.get("new_text")
            if isinstance(old, str) and isinstance(new, str):
                path = self._one(arguments.get("path", "file"), 80)
                lines = list(difflib.unified_diff(clean(old[:6000]).splitlines(), clean(new[:6000]).splitlines(),
                                                fromfile=path, tofile=path, lineterm=""))
                preview = "\n".join(lines[:40])[:2000]
                if preview:
                    if len(lines) > 40 or len("\n".join(lines)) > 2000 or max(len(old), len(new)) > 6000:
                        preview += "\n[preview truncated]"
                    panel = Panel(Syntax(preview, "diff", theme="ansi_dark", background_color="default"),
                                  title="Requested edit", border_style="bright_black", padding=(0, 1))
                    self.console.print(Padding(panel, (0, self.margin, 0, self.body_indent)), width=self.width)

    def tool(self, round_number, name, arguments_json, result_json, verbose=False):
        arguments = _json(arguments_json)
        result = _json(result_json)
        status = "error" if "error" in result else "ok"
        detail = ""
        if self._active_tool and self._active_tool[0] == (round_number, name, arguments_json):
            elapsed = time.monotonic() - self._active_tool[1]
            detail = (detail + "  " if detail else "") + f"{elapsed:.2f}s"
        self._active_tool = None
        self._tool_summary(round_number, name, arguments, status, detail)
        if "error" in result:
            self._body(self._one(result["error"], 300))
        if verbose:
            detail = clean(result_json)
            self._body(detail[:2000] + ("\n[preview truncated]" if len(detail) > 2000 else ""))


class PromptReader:
    def __init__(self, terminal, commands=None, *, session_factory=None):
        self.terminal = terminal
        self.commands = [command for command, _ in COMMANDS] if commands is None else commands
        self._session_factory = session_factory or PromptSession
        self._session = None

    def read(self):
        if not self.terminal.interactive or not sys.stdin.isatty() or os.getenv("TERM") == "dumb":
            return input(self.terminal.prompt())
        if self._session is None:
            bindings = KeyBindings()

            @bindings.add("enter")
            def submit(event):
                event.current_buffer.validate_and_handle()

            @bindings.add("f2")
            @bindings.add("escape", "enter")
            def newline(event):
                event.current_buffer.insert_text("\n")

            self._session = self._session_factory(
                multiline=True,
                history=InMemoryHistory(),
                completer=WordCompleter(self.commands, sentence=True),
                key_bindings=bindings,
                prompt_continuation=" " * self.terminal.body_indent,
                style=Style.from_dict({"prompt": "bold ansimagenta"}),
                cursor=CursorShape.BLINKING_BEAM,
                color_depth=ColorDepth.DEPTH_1_BIT if not self.terminal.color else None,
                reserve_space_for_menu=0,
            )
        return self._session.prompt([("class:prompt", " " * self.terminal.margin + "> ")])


def add_common_arguments(parser):
    parser.add_argument("--plain", action="store_true", help="Disable terminal colors")
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None,
                        help="Override AGENT_1_STREAM (default: enabled)")
    parser.add_argument("--verbose", action="store_true", help="Show bounded tool-result details")


def load_cli_settings() -> Settings:
    try:
        settings = load_settings()
    except ValueError as error:
        raise SystemExit(f"Configuration: {error}") from error
    if not settings.api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env or your environment.")
    return settings


def terminal_views(plain):
    color = False if plain else None
    terminal, activity = Terminal(color=color), Terminal(stream=sys.stderr, color=color)
    terminal.clear()
    return terminal, activity


def create_client(settings):
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=60.0, max_retries=2)


def run_task(client, task, session, *, settings, terminal, activity, stream=None, verbose=False):
    from .agent import run_agent

    stream = settings.stream if stream is None else stream
    last_round, last_text = None, ""

    def finish_output():
        activity.end_reasoning()
        terminal.end_text()

    def show_text(round_number, text):
        nonlocal last_round, last_text
        activity.end_reasoning()
        if last_round != round_number:
            last_round, last_text = round_number, ""
        last_text += text
        if stream:
            terminal.text(round_number, text)
        else:
            terminal.answer(text)

    def show_reasoning(round_number, text):
        terminal.end_text()
        activity.reasoning(round_number, text)

    def tool_started(round_number, name, arguments):
        nonlocal last_text
        last_text = ""
        finish_output()
        activity.tool_started(round_number, name, arguments)

    activity.status("Working...")
    try:
        answer = run_agent(
            client, task, session, settings=settings, stream=stream,
            on_text=show_text, on_reasoning=show_reasoning, on_reasoning_end=finish_output,
            on_tool_start=tool_started, on_tool=partial(activity.tool, verbose=verbose),
        )
    finally:
        finish_output()
    if answer != last_text:
        terminal.answer(answer)
    return answer
