"""Save each Claude Code response to its own Markdown file as soon as it arrives.

Runs as a Claude Code hook (Stop and SessionEnd): Claude Code passes the session's
transcript path on stdin, and any new assistant text in it is written to claude_resp/.
The first response to each request starts with the request it answers.

    python save_claude_responses.py          # hook mode (reads hook JSON on stdin)
    python save_claude_responses.py --all    # one-off backfill of every transcript
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

SOURCE = Path(os.environ.get("CLAUDE_PROJECTS", Path.home() / ".claude" / "projects"))
OUTPUT = Path(os.environ.get("CLAUDE_RESP_DIR", Path(__file__).with_name("claude_resp")))


def text_from(content) -> str:
    """Join the text blocks of a message; thinking and tool calls are skipped."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


# What Claude Code adds to a typed message: the open file, the selected lines (which can hold
# a whole private file), reminders. It isn't what the person wrote.
ADDED_CONTEXT = re.compile(
    r"<(system-reminder|ide_opened_file|ide_selection|ide_diagnostics|command-message"
    r"|local-command-caveat)>.*?</\1>\s*",
    re.S,
)
COMMAND = re.compile(r"<command-name>(.*?)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)


def parse(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def request_in(record: dict | None) -> str | None:
    """Return what the person typed, if this transcript record is their message."""
    if not record or record.get("type") != "user":
        return None
    # Tool output, skill text, summaries and notices are "user" records too.
    if record.get("toolUseResult") or record.get("isMeta") or record.get("isCompactSummary"):
        return None
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind") != "human":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    ):
        return None
    text = text_from(content)
    command = COMMAND.search(text)
    if command:
        text = f"{command.group(1).strip()} {(command.group(2) or '').strip()}".strip()
    text = ADDED_CONTEXT.sub("", text).strip()
    images = (
        sum(1 for block in content if isinstance(block, dict) and block.get("type") == "image")
        if isinstance(content, list)
        else 0
    )
    if images:
        text += f"\n\n_({images} image{'s' if images > 1 else ''} attached)_"
    return text.strip() or None


def response_in(record: dict | None) -> tuple[str, str] | None:
    """Return (text, date) if this transcript record is an assistant reply with text."""
    # Only real assistant records: other record types carry copies of old messages.
    if not record or record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    text = text_from(message.get("content")).strip()
    if not text:
        return None
    return text, date_of(record.get("timestamp"))


def date_of(timestamp) -> str:
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return moment.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().astimezone().strftime("%Y-%m-%d")


def load_state(output: Path) -> dict:
    try:
        state = json.loads((output / ".state.json").read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_state(output: Path, state: dict) -> None:
    temp = output / ".state.json.tmp"
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(output / ".state.json")


@contextmanager
def locked(output: Path) -> Iterator[None]:
    """Stop two sessions finishing at the same moment from reusing a file number."""
    lock = output / ".lock"
    deadline = time.monotonic() + 10
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
            break
        except FileExistsError:
            # A lock left behind by a crashed run is cleared after a minute.
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("response saver is busy")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def page(text: str, turn: dict) -> str:
    """A response file: the request and the reply, or a reply that continues an earlier one."""
    request, first = turn.get("request"), turn.get("first")
    if not request:
        return text + "\n"
    if first:
        return f"## Claude (continued from [{first}]({first}))\n\n{text}\n"
    return f"## You\n\n{request}\n\n## Claude\n\n{text}\n"


def save_new(transcript: Path, output: Path = OUTPUT) -> int:
    """Write any responses added to the transcript since the last run. Returns the count."""
    output.mkdir(parents=True, exist_ok=True)
    with locked(output):
        state = load_state(output)
        seen = state.setdefault("lines_done", {})
        key = str(transcript.resolve())
        done = int(seen.get(key, 0))
        number = int(state.get("next_number", 1))

        try:
            data = transcript.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return 0
        lines = data.split("\n")
        # The last piece has no newline yet if Claude Code is mid-write; leave it for next time.
        complete = lines[:-1]

        # The request being answered, and the file its first response went to. Kept between
        # runs, as a reply can be saved in a later run than its request was read.
        turns = state.setdefault("turns", {})
        turn = turns.get(key) or {}

        saved = 0
        for line in complete[done:]:
            record = parse(line)
            request = request_in(record)
            if request:
                turn = {"request": request, "first": None}
                continue
            found = response_in(record)
            if found:
                text, date = found
                name = f"response_{number:04d}_{date}.md"
                (output / name).write_text(page(text, turn), encoding="utf-8")
                if turn.get("request") and not turn.get("first"):
                    turn["first"] = name
                number += 1
                saved += 1

        turns[key] = turn
        seen[key] = len(complete)
        state["next_number"] = number
        save_state(output, state)
        return saved


def main() -> None:
    if "--all" in sys.argv[1:]:
        total = sum(save_new(path) for path in sorted(SOURCE.rglob("*.jsonl")))
        print(f"Saved {total} responses to {OUTPUT}")
        return
    hook_input = json.loads(sys.stdin.read() or "{}")
    transcript = hook_input.get("transcript_path")
    if transcript:
        save_new(Path(transcript))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # a hook must never break Claude Code
        print(f"Response saver skipped this reply: {error}", file=sys.stderr)
    sys.exit(0)
