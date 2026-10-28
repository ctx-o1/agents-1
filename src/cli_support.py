"""Shared CLI setup and one-turn presentation; no provider state in the views."""

import argparse
import sys
from functools import partial

from openai import OpenAI

from .cli import Terminal
from .config import Settings, load_settings


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
