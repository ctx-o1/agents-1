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
Reasoning streams to stderr by default in a dimmed, indented block per round.
The final answer is formatted on stdout after completion. Use --no-stream on
either command to buffer the response (including reasoning), or --plain to
disable colors. Models that return no reasoning simply show the final answer.
Reasoning and reasoning_details are preserved in saved assistant messages and
replayed on later turns. In Python, run_agent(..., stream=True,
on_reasoning=callback) delivers (round_number, text) fragments to your callback;
run_agent defaults to buffered mode for compatibility with existing callers.
Run offline tests with: uv run python -m unittest discover -s tests -v

Python source lives in src/. Runtime data defaults to .agent-1/ inside the
current working directory for debugging:

    .agent-1/
        sessions/       (JSON history and per-session scratchpads)
        MEMORY.md       (agent memory)
        USER.md         (user preferences)
        workspace/      (files accessible to the agent's file tools)

Set AGENT_1_DATA_DIR in the root .env or your environment to change the location.
Relative paths resolve from the current working directory; ~ expands to your
home directory. For example, AGENT_1_DATA_DIR=~/.agent-1 uses a home store.
Environment variables take precedence over .env. Restart after changing paths.
The root .env stays with the project and is not moved into the data directory.

For existing installs, stop the agent and move sessions/, MEMORY.md, USER.md,
and workspace/ from the project root into the chosen data directory before
restarting. Preserve their names and do not overwrite an existing destination.
Changing AGENT_1_DATA_DIR selects a store; it does not automatically migrate data.

*Upcoming*
Subagents
RLM
Context Rewriting

