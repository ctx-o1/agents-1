"""Offline UI preview: no credentials, model calls, or workspace writes."""

import argparse
import json
import sys
import time

from rich.padding import Padding
from rich.text import Text

from src.cli import Terminal, clean
from src.prompt import PromptReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plain", action="store_true", help="Disable terminal colors")
    parser.add_argument("--slow", action="store_true", help="Pause briefly between sample fragments")
    parser.add_argument("--input", action="store_true", help="Try the multiline editor after the preview")
    args = parser.parse_args()
    color = False if args.plain else None
    answer = Terminal(color=color)
    activity = Terminal(sys.stderr, color=color)
    answer.clear()
    activity.header("offline-demo", "mock/model")
    activity.info("Offline preview. All activity below is fabricated; no files are changed.")

    for fragment in (
        "I will inspect the existing defaults before changing the configuration. ",
        "The output limit belongs in one place so that chat and single-task runs behave consistently.\n\n",
        "Then I will show a small requested edit and check that the surrounding behavior remains clear. ",
        "This is sample reasoning for the interface preview, with no model running behind it.\n",
    ):
        activity.reasoning(1, fragment)
        if args.slow:
            time.sleep(0.3)
    activity.end_reasoning()
    for fragment in ("The default belongs in **configuration**. ", "Here is the proposed change to `example.py`."):
        answer.text(1, fragment)
        if args.slow:
            time.sleep(0.3)
    answer.end_text()

    arguments = json.dumps({"path": "example.py", "old_text": "limit = 4096", "new_text": "limit = 16384"})
    activity.tool_started(1, "edit_file", arguments)
    if args.slow:
        time.sleep(0.4)
    activity.tool(1, "edit_file", arguments, json.dumps({"result": "Simulated result only."}))
    answer.answer(
        "### Preview complete\n\n"
        "This demonstrates **streamed prose**, visible reasoning, and tool activity.\n\n"
        "```python\nlimit = 16384\n```\n\n"
        "| Mode | Behavior |\n| --- | --- |\n"
        "| Streamed | Live Markdown draft, then committed scrollback |\n"
        "| Buffered | Rendered Markdown and syntax highlighting |\n\n"
        "The edit above was **simulated**. Nothing was written."
    )
    if args.input:
        activity.info("Try multiline paste or F2; Enter submits. Ctrl+D exits.")
        try:
            draft = PromptReader(answer).read()
        except (EOFError, KeyboardInterrupt):
            return
        answer.info("Draft preview (not sent):")
        answer.console.print(Padding(Text(clean(draft)), (0, answer.margin, 0, answer.body_indent)), width=answer.width)


if __name__ == "__main__":
    main()
