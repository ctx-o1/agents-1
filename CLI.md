# CLI

- Streamed or buffered Markdown responses, with code highlighting and tables.
- A separate, visually distinct thinking stream.
- A clean startup screen with a two-column header and padded, dot-marked sections.
- Tool progress, timings, errors, and requested-edit diffs.
- A multiline prompt with paste support and in-memory input history.
- Slash commands with Tab completion.
- Unicode-aware wrapping and terminal resize support.
- Plain, no-color, and redirected output modes.
- An offline interactive demo.

Rich renders output. prompt_toolkit edits input. The agent loop owns model calls,
tools, budgets, and saved sessions.

| Library | What it does here |
| --- | --- |
| **Rich** | Renders response Markdown, code, tables, and requested-edit previews. `Live` refreshes the active response draft; the complete response is printed once into normal scrollback. |
| **prompt_toolkit** | Provides multiline input, bracketed paste, in-memory history, slash-command completion, key bindings, and the editable cursor. |
| **wcwidth** | Measures terminal display cells and graphemes for Unicode-safe wrapping and truncation. |

## Code ownership

- [src/cli.py](src/cli.py): `Terminal` rendering, `PromptReader` input editing,
  shared flags/client setup, and `run_task()` presentation. Coordinates
  thinking, response, and tool output without duplicates.
- [src/chat.py](src/chat.py): chat and slash-command handling.
- [src/agent.py](src/agent.py): agent loop and single-task entry point.
- [src/completions.py](src/completions.py): provider stream assembly; returns
  message, finish reason, and usage. Presentation does not alter saved messages.

Input editing and model execution happen sequentially. prompt_toolkit owns the
terminal while editing; Rich owns active response rendering during a run.

## Behavior

- Interactive startup clears the viewport while preserving terminal scrollback.
- Section labels have a left margin and a dot; content is indented underneath.
- Outer margins are one column; the total body inset is two columns.
- Responses show their body without a section label or dot marker.
- The header stacks on narrow terminals. There is no fixed bottom toolbar.
- Font size and zoom are controlled by the terminal, not the application.
- Responses go to stdout; thinking and tool activity go to stderr.
- Thinking uses secondary italic text and indentation. Responses render Markdown
  in both streaming and buffered mode on interactive terminals.
- Pipes and dumb terminals receive append-only plain text. `--plain` and
  `NO_COLOR` disable colors. Prompt history stays in memory for the current process.
- Enter sends; F2 or Alt+Enter inserts a newline; Tab completes chat commands.
- `--no-stream` buffers responses; `--verbose` shows bounded tool-result details.
- Tall live drafts may show an ellipsis; the complete response enters scrollback
  when the stream finishes. Edit previews describe requested changes, not success.

## Current limitation

Resuming a session restores its saved messages, reasoning, and tool results as
model context, but does not replay the full transcript on screen. `/history`
shows the last 20 messages with previews capped at 500 characters each.

## Run

```powershell
uv run -m src.chat
uv run -m src.agent --no-stream
uv run -m examples.terminal_demo --slow --input
```

The demo needs no credentials and makes no model/API calls or workspace changes.
Runtime options and defaults are documented in [.env.example](.env.example) and
[src/config.py](src/config.py).
