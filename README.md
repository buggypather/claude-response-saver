# Claude Code response saver

Save this as `save_claude_responses.py`. It checks Claude Code JSONL transcripts at midnight and noon, then writes new assistant responses to `claude_resp` with the date in each filename.

```python
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

SOURCE = Path(os.environ.get("CLAUDE_PROJECTS", Path.home() / ".claude" / "projects"))
OUTPUT = Path(__file__).with_name("claude_resp")
STATE = OUTPUT / ".processed.json"


def load_state() -> set[str]:
	try:
		return set(json.loads(STATE.read_text(encoding="utf-8")))
	except (FileNotFoundError, json.JSONDecodeError):
		return set()


def text_from(content) -> str:
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		return "\n".join(
			item if isinstance(item, str) else item.get("text", "")
			for item in content
			if isinstance(item, str)
			or isinstance(item, dict) and item.get("type") == "text"
		)
	return ""


def collect() -> None:
	OUTPUT.mkdir(exist_ok=True)
	processed = load_state()
	number = len(list(OUTPUT.glob("response_*.md"))) + 1

	for path in SOURCE.rglob("*.jsonl"):
		try:
			lines = path.read_text(encoding="utf-8").splitlines()
		except (OSError, UnicodeDecodeError):
			continue
		for line_number, line in enumerate(lines, 1):
			key = f"{path.resolve()}:{line_number}"
			if key in processed:
				continue
			processed.add(key)
			try:
				record = json.loads(line)
				message = record.get("message", record)
				if message.get("role") != "assistant":
					continue
				response = text_from(message.get("content")).strip()
				if not response:
					continue
				date = datetime.now().astimezone().strftime("%Y-%m-%d")
				(OUTPUT / f"response_{number:04d}_{date}.md").write_text(
					response + "\n", encoding="utf-8"
				)
				number += 1
			except (json.JSONDecodeError, AttributeError, OSError):
				continue

	STATE.write_text(json.dumps(sorted(processed), indent=2), encoding="utf-8")


def next_run() -> datetime:
	now = datetime.now().astimezone()
	noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
	midnight = (now + timedelta(days=1)).replace(
		hour=0, minute=0, second=0, microsecond=0
	)
	return noon if now < noon else midnight


if __name__ == "__main__":
	collect()  # also process responses immediately on startup
	while True:
		run_at = next_run()
		time.sleep(max(1, (run_at - datetime.now().astimezone()).total_seconds()))
		collect()
```

Run it from the repository directory:

```powershell
python save_claude_responses.py
```

By default, transcripts are read from `~/.claude/projects`. To use another location:

```powershell
$env:CLAUDE_PROJECTS = "C:\path\to\claude\projects"
python save_claude_responses.py
```
