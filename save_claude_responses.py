"""Save each Claude Code response to its own Markdown file as soon as it arrives.

Runs as a Claude Code hook (Stop and SessionEnd): Claude Code passes the session's
transcript path on stdin, and any new assistant text in it is written to claude_resp/.

    python save_claude_responses.py          # hook mode (reads hook JSON on stdin)
    python save_claude_responses.py --all    # one-off backfill of every transcript
"""

from __future__ import annotations

import json
import os
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


def response_in(line: str) -> tuple[str, str] | None:
    """Return (text, date) if this transcript line is an assistant reply with text."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    # Only real assistant records: other record types carry copies of old messages.
    if not isinstance(record, dict) or record.get("type") != "assistant":
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

        saved = 0
        for line in complete[done:]:
            found = response_in(line)
            if found:
                text, date = found
                (output / f"response_{number:04d}_{date}.md").write_text(
                    text + "\n", encoding="utf-8"
                )
                number += 1
                saved += 1

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
