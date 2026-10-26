import os

from dotenv import load_dotenv
from openai import APIError, OpenAI

from agent import PROJECT_ROOT, run_agent
from tools import WORKSPACE


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env or your environment.")

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    messages: list[dict] = []
    print("Chat with the agent. Type /exit to quit.")

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

                try:
                    answer = run_agent(client, task, messages)
                except APIError as error:
                    print(f"\nAPI error: {error}")
                    continue

                print(f"\nAgent: {answer}")
    except (EOFError, KeyboardInterrupt):
        print("\nChat ended.")


if __name__ == "__main__":
    main()
