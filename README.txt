                                           /$$       /$$$      /$$   /$$$
                                          | $$      /$$_/    /$$$$  |_  $$
  /$$$$$$   /$$$$$$   /$$$$$$  /$$$$$$$  /$$$$$$   /$$/     |_  $$    \  $$
 |____  $$ /$$__  $$ /$$__  $$| $$__  $$|_  $$_/  | $$ /$$$$$$| $$     | $$
  /$$$$$$$| $$  \ $$| $$$$$$$$| $$  \ $$  | $$    | $$|______/| $$     | $$
 /$$__  $$| $$  | $$| $$_____/| $$  | $$  | $$ /$$|  $$       | $$     /$$/
|  $$$$$$$|  $$$$$$$|  $$$$$$$| $$  | $$  |  $$$$/ \  $$$    /$$$$$$ /$$$/
 \_______/ \____  $$ \_______/|__/  |__/   \___/    \___/   |______/|___/
           /$$  \ $$
          |  $$$$$$/
           \______/



Persistent & Agent searchable Session History
Persistent Agent Memory
Agent Scratchpad


Run from the project root (Python 3.14+ and uv):

    uv sync
    uv run -m src.chat

Set OPENROUTER_API_KEY in the root .env file before starting.
For a single task, use: uv run -m src.agent
Run offline tests with: uv run python -m unittest discover -s tests -v

Python source lives in src/. Sessions, memory, and workspace files stay
at the project root.

*Upcoming*
Subagents
RLM
Context Rewriting

