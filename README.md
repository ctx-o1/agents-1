# agent(-1)

`agent(-1)` is a new gen experimental agent that can take a step back*

## Run

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
From this project folder, using PowerShell:

```powershell
uv sync
# On a fresh checkout, create .env once:
Copy-Item .env.example .env
```

Add your OpenRouter key to `.env`:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash-1017
```

Run the chat loop or a single task:

```powershell
uv run chat.py
# Or:
uv run agent.py
```

Both entry points load the project's `.env` automatically. Existing environment
variables take precedence. `.env`.  




Each user message runs the agent loop with the shared conversation history, including previous assistant and tool messages. History is saved automatically. `agent.py` runs one task,
saves its session, and exits.

## Sessions, memory, and scratchpad

Sessions use JSON and durable notes use Markdown, all through Python's standard
library. No database or extra dependencies are required.

```text
sessions/<id>.json  Conversation, tool calls/results, scratchpad, memory snapshot
MEMORY.md           Durable project and environment facts
USER.md             Recurring user preferences
```

These paths are ignored by Git. Session and memory updates use a temporary file
and atomic replacement. The chat prints its session ID; resume from the CLI
with `uv run chat.py --resume <id>` or use these commands:

| Command | Behavior |
|---|---|
| `/new` | Start a fresh transcript and scratchpad; load current durable memory |
| `/sessions` | List saved sessions and their first user message |
| `/resume <id>` | Resume the transcript and scratchpad; reload durable memory |
| `/history` | Show the last 20 transcript messages, with bounded previews |
| `/memory` | Inspect both durable stores, entry IDs, and capacity |
| `/notes` | Inspect the current session's scratchpad |
| `/notes clear` | Clear and save the scratchpad |
| `/help` | Show commands |
| `/exit` | Leave the chat |

The model can use three state tools:

| Tool | Behavior |
|---|---|
| `session_search(query, session_id="")` | Search saved messages, returning exact excerpts, roles, session IDs, and message indexes |
| `memory(action, target, content="", entry_id="")` | Read, add, update, or remove durable entries in the `agent` or `user` store |
| `scratchpad(action, content="")` | Read, write, or clear session working notes |

Memory is bounded: 2,200 characters for agent facts and 1,375 for user
preferences, including entry separators. Entries record their update time and
source session. Updates replace one complete entry by ID; full stores reject
writes. The prompt encourages verified, reusable facts and corrections while
keeping temporary task progress in the scratchpad. 


The Markdown files are the source of truth. Edit their visible text directly;
generated HTML comments preserve entry IDs, timestamps, and source session IDs.
To add an entry manually, separate it from other entries with a line containing
`---`. A plain entry receives a stable ID when read and metadata on its next
tool-managed save. The separator and generated metadata lines are reserved;
they cannot be embedded inside a single memory entry. Manual edits are validated
against the same capacity limits and appear on a live memory read or next session.


Memory is captured at session creation or resume. Mid-session changes persist
immediately and are returned by the memory tool; its `read` action retrieves
the live store. The scratchpad holds up to 4,000 characters and is refreshed in
every model request. It records goals, constraints, checked findings, and next
steps. A new session starts with an empty scratchpad.

Saved context is sent as a separately labeled data message after the system
instructions. It is not appended repeatedly to the transcript or promoted into
new system instructions. The `SYSTEM_PROMPT` constant in `agent.py` defines
agent(-1)'s behavior and guidance for memory, working notes, and evidence-based
recall. These instructions are model-agnostic and follow the declared tool
contracts. Current instructions are reapplied when a saved session is continued.

Successful live memory results supersede the startup snapshot. Temporary
instructions stay in session context; recurring preferences can become durable
memory. Removing a memory entry does not erase older session transcripts.


