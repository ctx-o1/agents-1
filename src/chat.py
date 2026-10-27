import argparse
import os
import sys

from openai import APIError, OpenAI

from .agent import run_agent
from .cli import Terminal
from .storage import (
    Session, list_sessions, load_session, memory, new_session,
    save_session, scratchpad,
)
from .tools import WORKSPACE

def handle_command(command: str, session: Session, terminal: Terminal | None = None) -> Session:
    terminal = terminal or Terminal()
    name, _, argument = command.partition(" ")
    argument = argument.strip()
    if argument and name in {"/new", "/sessions", "/history", "/memory", "/help"}:
        raise ValueError(f"Usage: {name}")
    if name == "/new":
        if session.messages or session.scratchpad:
            save_session(session)
        session = new_session()
        terminal.info(f"New session: {session.id}")
    elif name == "/sessions":
        terminal.sessions(list_sessions(), session.id)
    elif name == "/resume":
        if not argument:
            raise ValueError("Usage: /resume <session-id>")
        loaded = load_session(argument)
        if session.messages or session.scratchpad:
            save_session(session)
        save_session(loaded)
        session = loaded
        terminal.info(f"Resumed session: {session.id}")
    elif name == "/history":
        terminal.history(session.messages)
    elif name == "/memory":
        terminal.memory({target: memory("read", target) for target in ("agent", "user")})
    elif name == "/notes":
        if argument not in {"", "clear"}:
            raise ValueError("Usage: /notes [clear]")
        state = scratchpad("clear" if argument else "read", session=session)
        if argument:
            terminal.info("Scratchpad cleared.")
        else:
            terminal.section("Scratchpad", state["content"] or "(empty)")
    elif name == "/help":
        terminal.help()
    else:
        raise ValueError("Unknown command. Use /help to list commands.")
    return session


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Chat with agent(-1).")
    parser.add_argument("--resume", metavar="ID", help="Resume a saved JSON session")
    parser.add_argument("--plain", action="store_true", help="Disable terminal colors")
    parser.add_argument("--verbose", action="store_true", help="Show bounded tool-result details")
    arguments = parser.parse_args(argv)
    terminal = Terminal(color=False if arguments.plain else None)
    activity = Terminal(stream=sys.stderr, color=False if arguments.plain else None)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env or your environment.")

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        session = load_session(arguments.resume) if arguments.resume else new_session()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not open session: {error}") from error
    activity.header(session.id, os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash-1017"))
    terminal.help()

    try:
        with OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            max_retries=2,
        ) as client:
            while True:
                task = input(terminal.prompt()).strip()
                if task == "/exit":
                    break
                if not task:
                    continue
                if task.startswith("/"):
                    try:
                        session = handle_command(task, session, terminal)
                    except (OSError, ValueError) as error:
                        activity.error(f"Session: {error}")
                    continue

                try:
                    activity.status("Working...")
                    answer = run_agent(
                        client, task, session,
                        on_tool=lambda round_number, name, tool_arguments, result: activity.tool(
                            round_number, name, tool_arguments, result,
                            verbose=arguments.verbose,
                        ),
                    )
                except (APIError, OSError, ValueError) as error:
                    activity.error(str(error))
                    continue

                terminal.answer(answer)
    except (EOFError, KeyboardInterrupt):
        activity.info("Chat ended.")
    finally:
        if session.messages or session.scratchpad:
            try:
                save_session(session)
            except (OSError, ValueError) as error:
                activity.error(f"Could not save session: {error}")


if __name__ == "__main__":
    main()
