# agent (-1)

`agent(-1)` is a new generation coding agent for experimental context rewriting and subagent topologies.


**Current Features**  

- Separate agent and user memory.
- Per-session scratchpad.
- Saved sessions and session search.

*Rendering*

- Streamed reasoning, tool calls and responses.
- Markdown rendering.
- Tool progress, timing, errors, and requested-edit previews.
- Multiline input, paste, input history, and command completion.
- Slash commands.


It's a work in progress, currently missing some foundational agent capabilities like AGENTS.md adherence, MCP support, Token budgets. I plan to add subagents and RLM support first. 


## Install

Requires Python 3.14+ and uv. From the project root:

```sh
uv sync
cp .env.example .env
```

If you already have a `.env`, keep it. Set `OPENROUTER_API_KEY` in `.env`, then start the CLI:

```sh
uv run -m src.chat
```

> [!NOTE]
> Use `--no-stream` to wait for complete responses, or `--stream` to force streaming for one run. 


## Configuration

Edit the root `.env` file. Only the API key is required; other settings have defaults. Environment variables override `.env`. Restart the CLI after making changes.



```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash-1017
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
AGENT_1_MAX_OUTPUT_TOKENS=16384
AGENT_1_MAX_ROUNDS=10
AGENT_1_STREAM=true
AGENT_1_REASONING_ENABLED=true
AGENT_1_MAX_TASK_TOKENS=
AGENT_1_DATA_DIR=.agent-1
```

The output limit includes reasoning and tool-call output on supported providers. It does not change the model's context window. The task token threshold counts repeated input history across calls and is checked after each response, so it can be exceeded.


