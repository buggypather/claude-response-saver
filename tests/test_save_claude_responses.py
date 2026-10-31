import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from save_claude_responses import save_new  # noqa: E402


def record(kind, role, content, timestamp="2026-10-31T15:00:00.000Z"):
    return json.dumps(
        {"type": kind, "timestamp": timestamp, "message": {"role": role, "content": content}}
    )


class SaveNewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.transcript = root / "session.jsonl"
        self.output = root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, *lines, trailing_newline=True):
        text = "\n".join(lines) + ("\n" if trailing_newline else "")
        with self.transcript.open("a", encoding="utf-8") as file:
            file.write(text)

    def saved(self):
        return sorted(p.name for p in self.output.glob("response_*.md"))

    def test_saves_only_assistant_text(self):
        self.write(
            record("user", "user", "hi"),
            record("assistant", "assistant", [{"type": "thinking", "thinking": "secret"}]),
            record("assistant", "assistant", [{"type": "tool_use", "name": "Bash"}]),
            record("assistant", "assistant", [{"type": "text", "text": "Hello!"}]),
            # Copies of old messages inside other record types must be ignored.
            record("api-request-blob", "assistant", [{"type": "text", "text": "old copy"}]),
        )
        self.assertEqual(save_new(self.transcript, self.output), 1)
        files = self.saved()
        self.assertEqual(files, ["response_0001_2026-10-31.md"])
        self.assertEqual((self.output / files[0]).read_text(encoding="utf-8"), "Hello!\n")

    def test_each_reply_saved_once_across_runs(self):
        self.write(record("assistant", "assistant", [{"type": "text", "text": "one"}]))
        save_new(self.transcript, self.output)
        self.write(record("assistant", "assistant", [{"type": "text", "text": "two"}]))
        self.assertEqual(save_new(self.transcript, self.output), 1)
        self.assertEqual(save_new(self.transcript, self.output), 0)
        self.assertEqual(
            self.saved(), ["response_0001_2026-10-31.md", "response_0002_2026-10-31.md"]
        )

    def test_half_written_line_waits_for_next_run(self):
        self.write(
            record("assistant", "assistant", [{"type": "text", "text": "partial"}]),
            trailing_newline=False,
        )
        self.assertEqual(save_new(self.transcript, self.output), 0)
        self.write("")  # Claude Code finishes the line
        self.assertEqual(save_new(self.transcript, self.output), 1)

    def test_bad_lines_are_skipped(self):
        self.write("not json", record("assistant", "assistant", "plain string reply"))
        self.assertEqual(save_new(self.transcript, self.output), 1)


if __name__ == "__main__":
    unittest.main()
