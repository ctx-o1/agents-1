import argparse
import os

from dotenv import load_dotenv
from openai import APIError, OpenAI

from agent import PROJECT_ROOT, run_agent
from storage import (
    Session, list_sessions, load_session, memory, new_session,
    save_session, scratchpad,
)
from tools import WORKSPACE

HELP = "/new | /sessions | /resume <id> | /history | /memory | /notes [clear] | /help | /exit"


def handle_command(command: str, session: Session) -> Session:
    name, _, argument = command.partition(" ")
    argument = argument.strip()
    if name == "/new":
        if session.messages or session.scratchpad:
            save_session(session)
        session = new_session()
        print(f"New session: {session.id}")
    elif name == "/sessions":
        sessions = list_sessions()
        for item in sessions:
            print(f"{item['id']}  {item.get('title') or item.get('error') or '(empty)'}")
        if not sessions:
            print("No saved sessions yet.")
    elif name == "/resume":
        if not argument:
            raise ValueError("Usage: /resume <session-id>")
        loaded = load_session(argument)
        if session.messages or session.scratchpad:
            save_session(session)
        save_session(loaded)
        session = loaded
        print(f"Resumed session: {session.id}")
    elif name == "/history":
        for message in session.messages[-20:]:
            if message["role"] == "system":
                continue
            text = message.get("content") or ", ".join(
                call["function"]["name"] for call in message.get("tool_calls", [])
            )
            print(f"{message['role']}: {text[:500]}")
    elif name == "/memory":
        for target in ("agent", "user"):
            state = memory("read", target)
            print(f"{target} ({state['characters']}/{state['limit']} characters):")
            for entry in state["entries"]:
                print(f"  {entry['id']}: {entry['content']}")
    elif name == "/notes":
        if argument not in {"", "clear"}:
            raise ValueError("Usage: /notes [clear]")
        state = scratchpad("clear" if argument else "read", session=session)
        print(state["content"] or "(empty scratchpad)")
    elif name == "/help":
        print(HELP)
    else:
        raise ValueError(f"Unknown command. {HELP}")
    return session


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Chat with agent(-1).")
    parser.add_argument("--resume", metavar="ID", help="Resume a saved JSON session")
    arguments = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env or your environment.")

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        session = load_session(arguments.resume) if arguments.resume else new_session()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not open session: {error}") from error
    print(f"agent(-1) | Session: {session.id}")
    print(HELP)

    try:
        with OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            max_retries=2,
        ) as client:
            while True:
                task = input("\nYou: ").strip()
                if task == "/exit":
                    break
                if not task:
                    continue
                if task.startswith("/"):
                    try:
                        session = handle_command(task, session)
                    except (OSError, ValueError) as error:
                        print(f"Session error: {error}")
                    continue

                try:
                    answer = run_agent(client, task, session)
                except (APIError, OSError, ValueError) as error:
                    print(f"\nAgent error: {error}")
                    continue

                print(f"\nagent(-1): {answer}")
    except (EOFError, KeyboardInterrupt):
        print("\nChat ended.")
    finally:
        if session.messages or session.scratchpad:
            try:
                save_session(session)
            except (OSError, ValueError) as error:
                print(f"Could not save session: {error}")


if __name__ == "__main__":
    main()
