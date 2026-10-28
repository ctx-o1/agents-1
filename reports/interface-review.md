# Interface review: from terminal log to a modern coding-agent workspace

Reviewed 28 October 2026 at commit `a87bbb9`. This report evaluates the current terminal interface and proposes changes; it does not implement them.

Follow-up decision: the implementation uses Rich + prompt_toolkit, per the user's preference. Textual below is an evaluated alternative from the original review. Persistent input during agent work and full transcript replay remain future work; [CLI.md](../CLI.md) documents the current interface.

## Recommendation

The interface looks basic because it is designed as a line-oriented transcript printer. It has useful formatting and careful stream handling, but it has no interactive presentation model: no editable composer during a run, no persistent status area, no expandable tool records, and no way to revisit a particular change. Changing the palette or adding boxes would improve presentation but leave the main interaction gaps intact.

For the requested modern interface, use **one small, optional Textual frontend over the existing harness**, while keeping the plain CLI for pipes, automation, accessibility, and troubleshooting. Keep the synchronous agent loop initially. Run it in one worker, send small presentation events to the UI, and let the UI own rendering. Do not build a terminal layout engine or migrate the harness to a web stack.

Before that frontend, make two narrowly scoped improvements that benefit both modes: expose assistant text as it streams, and emit tool-start events before execution. These are the highest-value missing pieces.

This is a design recommendation, not a claim that Textual or any agent has an objectively measurable “state-of-the-art” interface. Here, that target means responsive input, readable streaming, inspectable changes, accurate status, and reliable behavior across terminals.

## Evidence and limits

I inspected `src/cli.py`, `src/chat.py`, `src/agent.py`, `src/completions.py`, relevant tool implementations, README, project dependencies, and CLI tests. I ran the existing offline CLI suite: **19 tests passed**. I also captured fabricated `Terminal` output in memory to verify narrow-width and Unicode behavior. Public primary documentation was checked for modern agent interaction patterns and Python UI-library capabilities.

No live model/API testing was performed. No credentials or saved user conversations were inspected. The only file created by this review is this report. I did not inspect the interface in an actual user's terminal or evaluate a screenshot of their current theme; visual judgments below are based on code and deterministic output, with human checks explicitly listed later.

## Why the current interface feels basic

| Finding | Evidence in the current code | User-visible consequence |
|---|---|---|
| Assistant prose is buffered even when streaming is enabled | [completions.py:105](/D:/projects/agents-1/src/completions.py:105) accumulates content; [chat.py:118](/D:/projects/agents-1/src/chat.py:118) prints the returned answer | A user sees live reasoning, then waits for the entire answer. A model returning no visible reasoning can appear inactive until completion. |
| Intermediate assistant commentary is not presented | [agent.py:179](/D:/projects/agents-1/src/agent.py:179) saves assistant messages, but the interface receives only reasoning, completed tools, and the final returned string | “I will inspect the file” or other content accompanying a tool call can be saved without ever appearing in the live transcript. |
| Tools are announced only after they finish | [agent.py:201](/D:/projects/agents-1/src/agent.py:201) executes a tool before the callback at line 215 | There is no visible running operation, elapsed duration, pending state, or clear distinction between network waiting and local work. |
| Input is a synchronous, single-line operation | [chat.py:89](/D:/projects/agents-1/src/chat.py:89), [agent.py:256](/D:/projects/agents-1/src/agent.py:256) use `input(...).strip()` | No multiline composer, completion menu, draft preservation, or deliberate input handling during a run. Pasted multiline text can be treated as several later prompts. Leading/trailing whitespace is stripped. |
| The screen has no retained layout state | [cli.py:48](/D:/projects/agents-1/src/cli.py:48) always appends and flushes lines; startup allocates two independent terminal views at [chat.py:64](/D:/projects/agents-1/src/chat.py:64) | Content cannot collapse, a tool row cannot change from running to done, and the composer/status do not remain visible. |
| Width is captured once and forced to at least 40 columns | [cli.py:38](/D:/projects/agents-1/src/cli.py:38) | Resize is not reflected in existing layout decisions; narrow split panes overflow. A fabricated 30-column terminal selected width 40 and produced a 39-column prose line. |
| Display-width correctness is confined to reasoning | [cli.py:52](/D:/projects/agents-1/src/cli.py:52) truncates by string length; [cli.py:75](/D:/projects/agents-1/src/cli.py:75) uses `textwrap`; reasoning alone uses graphemes/display cells at line 148 | At a configured width of 40, a fabricated final answer containing 36 Chinese characters generated a 74-column body line. File names, summaries, and history have the same class of risk. |
| Markdown is partly decorated, not rendered structurally | [cli.py:57](/D:/projects/agents-1/src/cli.py:57) recognizes fences and selected inline patterns; table lines and code pass through | Code has no language-aware styling, tables are raw pipes, and headings/links/lists do not have a consistent visual system. Preserving raw code is a good invariant, but long code needs a deliberate overflow policy. |
| Tool summaries are useful but too flat | [cli.py:255](/D:/projects/agents-1/src/cli.py:255) prints one status line; `--verbose` prints a bounded raw JSON fragment | Users cannot inspect one tool selectively. A successful file edit looks similar to a memory read; there is no applied-change view. |
| History is a diagnostic preview | [cli.py:220](/D:/projects/agents-1/src/cli.py:220) shows the last 20 messages and caps each preview at 500 characters | It is not a navigable transcript and does not replay saved reasoning into an inspectable presentation. |
| Operational state is absent | [cli.py:85](/D:/projects/agents-1/src/cli.py:85) prints model/session once; [agent.py:132](/D:/projects/agents-1/src/agent.py:132) counts usage only for the optional task budget | No persistent run status, latest input/output usage, cache reads, or elapsed time. There is no established pricing/model-context metadata from which an accurate cost or context percentage could be displayed. |
| Reasoning has no view controls | [cli.py:114](/D:/projects/agents-1/src/cli.py:114) expands every reasoning block into scrollback | Long reasoning can dominate the transcript. This is not an argument to hide it: the user explicitly wants streamed reasoning visible. It needs user-controlled expansion and navigation. |
| Startup is information-heavy but not actionable | [chat.py:78](/D:/projects/agents-1/src/chat.py:78) prints the header and the complete command list on every launch | The screen starts as a help dump rather than a small workspace with discoverable commands. |

The existing implementation also has strengths to preserve: stdout/stderr separation, `NO_COLOR` and dumb-terminal handling, control-character filtering, grapheme-safe reasoning, bounded tool previews, saved session checkpoints, and tests that ensure presentation does not modify canonical content. These are foundations for the next interface, not code to discard wholesale.

## What current agent interfaces demonstrate

These are documented capabilities, not claims that their exact layouts should be copied.

| Reference | Relevant documented behavior | Lesson for this project |
|---|---|---|
| [Pi terminal usage](https://pi.dev/docs/latest/usage) | A persistent editor/status region; multiline composition; expandable tool/thinking records; session/model/context/usage information; input while work is underway | The user should retain control and know what is happening without reading every transcript line. |
| [OpenCode TUI](https://opencode.ai/docs/tui/) | File-reference completion, command discovery, session switching, tool-detail controls, and an external editor | Discoverability and inspection are part of a modern interface, not optional visual decoration. |

The minimum competitive experience for this small harness is therefore: a good composer, two independently readable streams of assistant prose and reasoning, visible tool lifecycle, inspectable file changes, stable scroll position, responsive cancellation, and honest status. Session trees, image handling, remote collaboration, and a general-purpose IDE are not prerequisites.

## The target interaction

Use one main transcript column, a compact header, a fixed composer, and a quiet footer. Avoid a permanent file-tree sidebar until there is evidence it helps this project's workflows.

Illustrative wireframe, not an implementation or a screenshot:

```text
agent(-1)   agents-1 / workspace                 deepseek/...   session 7c42
─────────────────────────────────────────────────────────────────────────
You
  Fix the handling of an empty configuration value.

Agent
  I’m checking the parser and the existing regression tests.

▾ Reasoning · round 1 · streaming
  The empty value is an override boundary. I should check...

▸ Read  src/config.py                               done · 12 ms
▾ Edit  src/config.py                               applied · +3 −1
    - value = int(raw)
    + if not raw:
    +     return default
    + value = int(raw)
▸ Test  focused offline checks                      done · 4 passed

Agent
  Empty values now use the configured default. The checks passed.

┌ Prompt ────────────────────────────────────────────────────────────────┐
│ Add coverage for whitespace-only values too.                            │
└────────────────────────────────────────────────────────────────────────┘
Ready   last input 8.1k · output 620 · cache —   task 2/10 rounds
Enter send · F2 newline · Esc stop · Ctrl+O tools · Ctrl+T reasoning · F1 help
```

The “Test” row is illustrative only: the current declared tools do not provide a shell/test runner. A UI must not display capabilities or successful checks that the harness did not actually execute. Initial cards should cover the current file/search/memory tools; add a test card only with a real corresponding tool/event.

Interaction decisions:

- **Assistant prose:** stream text into the current assistant message as it arrives. Preserve the final Markdown verbatim in session storage. Buffer/coalesce rendering work, not the underlying message.
- **Reasoning:** keep the active reasoning block expanded by default, as requested. Allow users to collapse individual completed blocks or toggle all reasoning. Hiding is view-only; it must not remove reasoning from history or tool continuations.
- **Tool lifecycle:** one stable row/card per tool-call ID, with pending/running/done/error/skipped states. Show the tool and target before execution, duration afterward, and relevant output on expansion. Errors expand automatically; successful reads remain compact.
- **Edits:** show a bounded unified diff associated with an operation. Clearly distinguish requested changes from changes that actually succeeded. A completion message saying “wrote N characters” is insufficient to reconstruct the old version.
- **Scroll:** follow new output only while the user is already at the bottom. Scrolling upward pauses follow mode and shows an unobtrusive “new output” affordance. Expanding a card or resizing should not unexpectedly jump to the end.
- **Composer:** support multiline paste as one draft. Show the first few lines and allow it to grow to a reasonable maximum, then scroll within it. Keep a draft while inspecting history. Start with completion for existing slash commands and sessions; file references require an explicit context-attachment design rather than silently injecting arbitrary files.
- **During a run:** initially allow stop and local view controls, while retaining a draft. Queue/steering can come later. Never introduce concurrent runs that mutate the same session merely to make the input feel responsive.

## Architecture that keeps the agent lean

### Preserve the harness boundary

Keep provider requests, tool execution, persistence, and stop conditions in the harness. Keep terminal libraries, colors, keyboard actions, widgets, and expanded/collapsed state out of `run_agent()`.

Evolve the existing callbacks into **one small optional event callback**, with a compact dataclass or a similarly explicit typed payload. A suitable initial vocabulary is `round_started`, `text_delta`, `reasoning_delta`, `tool_started`, `tool_finished`, `usage`, and `run_finished`/`run_failed`. Stable run/message/tool IDs let a renderer update existing records. This is a suggested contract, not an instruction to build a general event framework.

The canonical transcript remains the existing assistant/tool messages. Presentation state and chunk assembly do not need a second database or a second persisted transcript. Existing callbacks can be adapted during migration; do not retain two permanent event systems.

### One worker and one rendering owner

Let the interactive frontend own the terminal. The agent runs in one worker and forwards ordered events to the UI thread. Only that worker mutates its active `Session`; the UI receives event data rather than concurrently modifying the session's message list.

Textual documents thread workers and main-thread dispatch, so a full conversion of provider and storage code to `async` is unnecessary. Worker threads cannot simply be killed; stop must be cooperative. Check cancellation between stream chunks and before tool execution, acknowledge “stopping” immediately, and report completion only after the worker unwinds. If a network read is blocked, cancellation latency remains bounded by transport behavior/timeouts, not by the UI. An in-flight side effect must never be described as rolled back. [Textual workers](https://textual.textualize.io/guide/workers/)

Use one event bridge and coalesce adjacent text fragments for rendering at approximately 10–20 updates per second. Keep event order and the exact accumulated content. Do not drop a `tool_started`, terminal status, usage record, or final delta to meet the render rate. A bounded presentation queue must merge/backpressure text rather than silently discard content.

### Suggested ownership, not a package explosion

```text
agent.py / completions.py   provider + agent loop; optional event emission
cli.py                     plain transcript adapter and existing fallbacks
tui.py                     one optional Textual app + small view components
```

Start there. Extract `events.py` or individual widgets only when it makes the code easier to read. There is no immediate need for an RPC server, plugin system, browser frontend, event-sourcing layer, or parallel application state stores.

### Diff information must be truthful

For `edit_file`, the old/new text arguments can support a **requested-change** preview. For an **applied-file** diff, capture a bounded preimage before the operation and pair it with the resulting content after success. `write_file` also needs a preimage to distinguish replacement from creation. See [tools.py:37](/D:/projects/agents-1/src/tools.py:37) and [tools.py:46](/D:/projects/agents-1/src/tools.py:46).

Keep this metadata outside model-visible tool results unless it is useful to the model. Do not duplicate complete file bodies into every UI event, load unbounded files, or infer an applied diff by reading a file long afterward when another process may have changed it. A simple operation result with optional display metadata is enough; no file-history subsystem is required for v1.

## Library choice and tradeoffs

| Approach | Strength | Cost/limit | Recommendation |
|---|---|---|---|
| Existing printer, with small improvements | Minimal dependencies; robust pipes and scrollback | Becomes hand-built UI code once collapse, pinned input, focus and resize are added | Retain as the plain frontend. Fix correctness and emit better events here. |
| Rich output + a normal input loop | Markdown/syntax/tables, adaptive console output and modest live status | Formatting and live renderables do not provide an editor, transcript navigation, or application lifecycle | Good short-term polish if a full TUI is postponed. Not the final architecture for all requested interactions. |
| prompt_toolkit, optionally with Rich | Excellent input/history/completion; can remain in normal scrollback | A sophisticated transcript still needs layout/view state; combining independent render loops requires care | Prefer this only if normal terminal scrollback and a strong composer are the actual product goal. |
| **Textual** | One Python framework for editor, layout, collapsibles, rendering, event handling and headless tests | Larger dependency/runtime and more UI-specific state; alternate-screen accessibility/copy behavior needs testing | **Preferred for the complete modern interactive interface.** Keep plain mode available and avoid extra editor/parser dependencies initially. |

Rich's console detects terminal capabilities and current dimensions, and its live display supports refresh/update control. It is a formatting building block, not a replacement for application interaction design. [Rich console](https://rich.readthedocs.io/en/stable/console.html), [Rich Live](https://rich.readthedocs.io/en/stable/live.html)

prompt_toolkit supports reusable prompt sessions, multiline input, completion/history, asynchronous prompts and output coordination. It is a credible lean alternative if the team deliberately chooses a scrollback-oriented product. [prompt_toolkit input guide](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html)

Textual already provides a plain multiline `TextArea`, `Collapsible`, and streaming Markdown updates. The optional syntax-editing extras introduce parser dependencies; they are unnecessary for a prompt composer. Its Markdown stream helper batches frequent appends, so avoid implementing a competing Markdown-refresh scheduler. [TextArea](https://textual.textualize.io/widgets/text_area/), [Collapsible](https://textual.textualize.io/widgets/collapsible/), [streaming Markdown](https://textual.textualize.io/widgets/markdown/#textual.widgets.Markdown.get_stream)

Do not install Textual, prompt_toolkit, and a separate Rich Live renderer as three independent UI systems. Select one interactive owner. Rich objects used through Textual are normal; independently managed cursor/refresh loops are the problem.

## Presentation, accessibility, and platform requirements

**Visual hierarchy.** Use the terminal's font rather than relying on a custom font. Define a small set of semantic styles: normal text, secondary metadata, accent/focus, success, warning, and error. Use one border style and restrained padding. Keep code/file contents selectable and distinguish user, assistant, reasoning, and tool records by labels and spacing. Avoid a panel around every paragraph or a large startup banner. A persistent workspace/model line is more useful than repeated branding.

**Reasoning readability.** The current dim style can be too faint in some themes. Use a readable secondary color; do not rely on faint ANSI text alone for long reasoning. Test light and dark terminal themes. The active reasoning block should be visually secondary while remaining fully readable and visible by default.

**Honest status.** Start with elapsed time, actual phase, model, session, and round count. Usage can show provider-reported input/output/cache values when available. Unknown values should be “—” or explicitly estimated, not zero. A task-token threshold is not context capacity, and accumulated tokens across calls are not the size of the current model context. Do not display a context percentage without known model capacity or a dollar cost without trustworthy pricing metadata.

**Keys.** Provide discoverable controls through a small footer and F1/help. A reasonable initial map is Enter to send, F2 to insert a newline, Esc to stop, Ctrl+O for tool details, and Ctrl+T for reasoning. Shift+Enter can be an additional newline binding where distinguishable. All important actions need slash-command or menu equivalents. Terminal/OS key delivery varies; Textual explicitly warns that an application cannot bind keys the emulator intercepts. [Textual key-delivery guidance](https://textual.textualize.io/FAQ/#why-do-some-key-combinations-never-make-it-to-my-app)

**Plain/redirected output.** Preserve existing stdout as answer content and stderr as activity. Never emit alternate-screen controls, cursor movement, animated progress, or full-screen chrome into redirected output. Keep `--plain` meaning “disable color” for compatibility; if a full TUI is added, use a separate explicit UI-mode setting such as `--ui auto|plain|tui`. Initially choose TUI automatically only for interactive chat with usable TTY input/output. Preserve the one-shot path as the reliable print interface. `NO_COLOR` controls color, not whether the user is allowed to interact. `TERM=dumb` and unsupported consoles should choose plain rendering.

**Accessibility.** Every status must have a text label, not just a color or glyph. Offer a static/no-animation choice and plain scrollback for screen readers. Keep focus visible; ensure every control is reachable by keyboard. Avoid forcing mouse capture or special icon fonts. A screen-reader-friendly full-screen experience requires real testing and must not be assumed from a framework's feature list.

**Windows.** Test native PowerShell in Windows Terminal and the IDE integrated terminal actually used by the project. Do not make Unix-only signal or job-control assumptions. Validate Unicode cell widths, IME/multiline paste, terminal selection, clipboard copy, Esc/Ctrl+C behavior, resize, and crash/exit restoration. Textual documents terminal-native selection differences, including Shift-selection in Windows Terminal; the app should explain relevant bindings rather than assume every terminal behaves alike. [Textual selection guidance](https://textual.textualize.io/FAQ/#how-can-i-select-and-copy-text-in-a-textual-app)

**Untrusted output.** Retain the current escape-sequence filtering before rendering model/tool text. Disable interpreted Rich markup for strings that should be plain data. Treat Markdown links as explicit user actions, and do not auto-execute or auto-open content. UI sanitization must not rewrite the content persisted for the model.

## Prioritized roadmap

Effort estimates are engineering judgment for one developer familiar with this repository, including offline verification. They are not commitments and exclude new shell tools, provider work, or general compaction.

| Stage | Scope | Estimate | Acceptance criteria |
|---|---|---|---|
| **P0: truthful live feedback** | Add assistant-text events and tool-start/finish events; identify run/message/tool records; expose usage independently of budget enforcement | 1–2 days, low–medium complexity | A fabricated prose-only stream displays before completion; intermediate tool commentary is shown; a deliberately slow fake tool has a visible running row; completion/error appears exactly once. Persistence and tool-execution ordering remain unchanged. |
| **P0: plain-output correctness** | Respect actual small widths; use display-cell-aware formatting consistently; refresh width for new output; show concise startup/help hint | 0.5–1 day, low complexity | 30/40/80/120-column fixtures preserve content, labels and code; CJK/emoji summaries do not overflow prose budgets; stdout/stderr and `NO_COLOR` contracts pass. Long code is deliberately preserved, not silently mangled. |
| **P1: optional interactive shell** | One Textual screen, transcript, multiline composer, simple footer, one worker/event bridge, basic stop lifecycle | 2–4 days, medium complexity | Composer stays responsive while fake provider/tool work runs; multiline paste submits once; duplicate runs are prevented; Esc acknowledges stopping without falsely claiming rollback; exceptions restore terminal state; plain mode remains independently usable. |
| **P1: inspection** | Expandable tool/reasoning blocks, bounded file diffs, copy actions, existing slash-command completion and session picker | 2–3 days, medium complexity | Users can inspect one tool without expanding everything; applied/requested/skipped changes are distinguished; reasoning stays visible by default; expanding and resizing do not lose the scroll anchor or draft. |
| **P2: measured polish** | Themes/contrast tuning, honest usage/cache indicators, better narrow layouts, long-transcript performance | 1–3 days, medium complexity | Unknown metrics are labeled; no invented cost/context percentage; 1,000 synthetic blocks and high-frequency deltas remain responsive without losing content; light/dark/no-color cases are reviewed. |

A practical first release is the two P0 items plus the P1 interactive shell. The second P1 inspection stage makes the interface useful for sustained coding work. P2 should be driven by measured use, not by adding features to imitate screenshots.

If the team wants a two-day improvement rather than a full TUI, stop after P0 and use Rich for final Markdown/tool summaries. State the limitation clearly: it will be a polished CLI with normal scrollback, not a pinned-editor interface with expandable historical records. Do not build that temporary path into an alternate long-term framework if Textual work is already scheduled.

## Verification plan without live model calls

Build one deterministic fake-run fixture that emits the same UI events as the harness: delayed connection, split reasoning and prose, partial Markdown fences, two tools, an applied edit, one failed tool, reported usage, and a final answer. No credentials, real project edits, subprocess commands, or network inference are needed.

Verify these behaviors rather than snapshotting only colors:

1. A no-reasoning model still looks alive because prose and waiting states update.
2. Arbitrary chunk boundaries, including inside escape sequences and graphemes, preserve exact final text.
3. Repeated small chunks are coalesced for rendering; the last chunk is flushed on success, failure, and cancellation.
4. Tool-start precedes execution; tool-finish follows the persisted result. A failed display must not cause a tool side effect to be replayed.
5. Stop during a fake stream, between tools, and while a fake tool is running prevents subsequent tool starts. The running operation is honestly shown as stopping/uncertain until it resolves.
6. Multiline paste, undo in the draft, command completion, focus changes, and history inspection never accidentally submit or discard a prompt.
7. A user scrolled upward is not dragged to the bottom by new deltas. Resize at the top/middle/bottom preserves a useful position.
8. Expanded/collapsed controls change only the view, not model context or the canonical transcript.
9. The plain frontend remains useful when stdout, stderr, stdin, or combinations are redirected; no control-sequence leakage occurs.
10. Unknown usage, cache reads, model capacity, and pricing stay unknown; metrics do not accidentally imply a hard budget guarantee.

Textual's headless `App.run_test()` and `Pilot` support keyboard/mouse interaction and chosen terminal sizes; visual snapshots can supplement assertions. Headless tests do not validate actual terminal key negotiation or screen-reader behavior. [Textual testing](https://textual.textualize.io/guide/testing/)

Use a compact size matrix: 30×12, 40×16, 80×24 and 120×40. Add Unicode, a wide table, long unbroken file names, 200-line code, 1,000 collapsed tools, and a high-rate text stream. Measure render/queue behavior against an agreed latency target—e.g. UI feedback within 100 ms in the fake demo—and record results rather than promising frame rates without measurement.

Ask the user to run the offline interactive demo in their real Windows terminals to validate copy/paste, display, key delivery, resize and accessibility. If later provider-specific behavior requires a live run, provide a small user-run recipe and ask them to share the result; do not make the call automatically.

## Explicit non-goals

- No browser/Electron frontend, frontend server, or second language merely for visual polish.
- No bespoke ANSI screen renderer, Markdown parser, text editor, or terminal emulator.
- No simulated context/cost/cache numbers and no claims that a visual indicator enforces a hard spend cap.
- No model-generated tool progress invented between real events.
- No hidden default reasoning contrary to the user's preference.
- No concurrent session mutation, broad async rewrite, general event bus, or replay database for the UI.
- No automatic approval dialogs for every tool; permission policy belongs to an explicit product decision, not to a card style.
- No session branching, embeddings, multi-agent dashboard, file tree, image protocol, or plugin system in the first UI release.

The largest improvement will come from making the agent's real state visible and controllable. A restrained visual system and a capable terminal framework can then make that behavior feel coherent without making the harness itself complicated.
