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
Assistant prose streams to stdout, and reasoning appears on stderr in an
indented, secondary italic block per round. Tool activity shows running/completed
states and elapsed time. Interactive responses use Rich Markdown, code
highlighting, and tables in both streaming and buffered modes. A streamed draft
updates in place; the complete response enters normal scrollback once finished.
Use --no-stream to wait for complete responses. --plain disables colors; pipes
and dumb terminals retain append-only plain text and preserve raw code lines.
Interactive edit previews are labeled "Requested edit" before execution; they
do not claim a successful change. --verbose shows bounded tool-result details.

Interactive prompts use prompt_toolkit: Enter sends, F2 or Alt+Enter inserts a
newline, and Tab completes slash commands. Multiline paste stays in one draft.
History is kept only in memory for the current process. Non-interactive input
uses the existing line-input fallback. Ctrl+C interrupts; no background agent
runs or full-screen terminal application are introduced.

Preview the UI without credentials or model calls:

    uv run -m examples.terminal_demo --slow
    uv run -m examples.terminal_demo --input

The demo uses fabricated activity and makes no workspace changes. Use it to
check native terminal copy/paste, color contrast, resize, and keyboard behavior.
Reasoning and reasoning_details are preserved in saved assistant messages and
replayed on later turns. In Python, run_agent(..., stream=True,
on_reasoning=callback) delivers (round_number, text) fragments to your callback;
run_agent defaults to buffered mode for compatibility with existing callers.
on_text receives assistant prose, on_tool_start announces a validated tool before
execution, and on_tool reports its persisted result. Input/output formatting is
kept in the shared CLI adapter. Malformed tool IDs are rejected before execution.
Run offline tests with: uv run python -m unittest discover -s tests -v

Runtime settings are loaded from the root .env and validated at startup by
src/config.py. Process environment variables take precedence over .env; missing
optional settings use code defaults. Copy .env.example for the full list:

    OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash-1017
    OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
    AGENT_1_MAX_OUTPUT_TOKENS=16384
    AGENT_1_MAX_ROUNDS=10
    AGENT_1_STREAM=true
    AGENT_1_REASONING_ENABLED=true
    AGENT_1_MAX_TASK_TOKENS=

Restart after changing settings. --stream and --no-stream override AGENT_1_STREAM
for one invocation. Boolean settings accept true/false, yes/no, on/off, or 1/0.
Blank optional numeric/boolean settings use defaults; blank MAX_TASK_TOKENS
disables the aggregate token limit. Model and base URL must not be blank.

MAX_OUTPUT_TOKENS caps generation per model call (reasoning, text, and tool-call
output on supported providers). It does not set the model's context window.
MAX_ROUNDS limits model calls per user task. The optional MAX_TASK_TOKENS limit
counts reported input plus output tokens across calls in that task, including
repeated history. It resets for the next user task. Remaining budget also caps
the next call's output allowance. Because input usage is only known after a
request, one response can exceed the threshold: this is a stop threshold, not a
hard spend cap. Once reached, pending tools are marked unexecuted and no further
model calls are made; a completed final answer is still returned. Providers must
report usage when the limit is enabled; missing usage stops before tool execution.

Python callers can pass settings=load_settings() or an explicit Settings object
to run_agent(). Its stream argument still defaults to False for compatibility;
pass stream=settings.stream to use the configured CLI streaming preference.

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

