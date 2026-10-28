import argparse

from openai import APIError

from .cli import (
    PromptReader, Terminal, add_common_arguments, create_client,
    load_cli_settings, run_task, terminal_views,
)
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
    add_common_arguments(parser)
    arguments = parser.parse_args(argv)
    settings = load_cli_settings()
    terminal, activity = terminal_views(arguments.plain)

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        session = load_session(arguments.resume) if arguments.resume else new_session()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not open session: {error}") from error
    activity.header(session.id, settings.model)
    activity.info("Type /help for commands; /exit to leave.")
    reader = PromptReader(terminal)

    try:
        with create_client(settings) as client:
            while True:
                task = reader.read()
                command = task.strip()
                if command == "/exit":
                    break
                if not command:
                    continue
                if command.startswith("/"):
                    try:
                        session = handle_command(command, session, terminal)
                    except (OSError, ValueError) as error:
                        activity.error(f"Session: {error}")
                    continue

                try:
                    run_task(client, task, session, settings=settings, terminal=terminal,
                             activity=activity, stream=arguments.stream, verbose=arguments.verbose)
                except (APIError, OSError, ValueError) as error:
                    activity.error(str(error))
                    continue

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
