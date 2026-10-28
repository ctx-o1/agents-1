"""A small multiline composer; history remains in memory for this process."""

import os
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style

from .cli import COMMANDS


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
