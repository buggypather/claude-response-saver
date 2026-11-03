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


def reply(text):
    return record("assistant", "assistant", [{"type": "text", "text": text}])


def human(content):
    """A message the person typed, as Claude Code records it."""
    return json.dumps(
        {
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-10-31T15:00:00.000Z",
            "message": {"role": "user", "content": content},
        }
    )


def tool_result(text):
    return json.dumps(
        {
            "type": "user",
            "toolUseResult": {"stdout": text},
            "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
        }
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

    def read(self, name):
        return (self.output / name).read_text(encoding="utf-8")

    def test_saves_only_assistant_text(self):
        self.write(
            record("user", "user", "hi"),
            record("assistant", "assistant", [{"type": "thinking", "thinking": "secret"}]),
            record("assistant", "assistant", [{"type": "tool_use", "name": "Bash"}]),
            reply("Hello!"),
            # Copies of old messages inside other record types must be ignored.
            record("api-request-blob", "assistant", [{"type": "text", "text": "old copy"}]),
        )
        self.assertEqual(save_new(self.transcript, self.output), 1)
        files = self.saved()
        self.assertEqual(files, ["response_0001_2026-10-31.md"])
        self.assertEqual(self.read(files[0]), "## You\n\nhi\n\n## Claude\n\nHello!\n")

    def test_each_reply_saved_once_across_runs(self):
        self.write(reply("one"))
        save_new(self.transcript, self.output)
        self.write(reply("two"))
        self.assertEqual(save_new(self.transcript, self.output), 1)
        self.assertEqual(save_new(self.transcript, self.output), 0)
        self.assertEqual(
            self.saved(), ["response_0001_2026-10-31.md", "response_0002_2026-10-31.md"]
        )
        # With no request before it, a reply is saved as it is.
        self.assertEqual(self.read("response_0001_2026-10-31.md"), "one\n")

    def test_half_written_line_waits_for_next_run(self):
        self.write(reply("partial"), trailing_newline=False)
        self.assertEqual(save_new(self.transcript, self.output), 0)
        self.write("")  # Claude Code finishes the line
        self.assertEqual(save_new(self.transcript, self.output), 1)

    def test_bad_lines_are_skipped(self):
        self.write("not json", record("assistant", "assistant", "plain string reply"))
        self.assertEqual(save_new(self.transcript, self.output), 1)

    def test_later_replies_in_a_turn_point_to_the_first(self):
        self.write(
            human("Fix the bug"),
            reply("Looking."),
            tool_result("ok"),
            reply("Fixed."),
            human("Thanks"),
            reply("You're welcome."),
        )
        self.assertEqual(save_new(self.transcript, self.output), 3)
        self.assertEqual(
            self.read("response_0001_2026-10-31.md"),
            "## You\n\nFix the bug\n\n## Claude\n\nLooking.\n",
        )
        self.assertEqual(
            self.read("response_0002_2026-10-31.md"),
            "## Claude (continued from [response_0001_2026-10-31.md](response_0001_2026-10-31.md))"
            "\n\nFixed.\n",
        )
        self.assertEqual(
            self.read("response_0003_2026-10-31.md"),
            "## You\n\nThanks\n\n## Claude\n\nYou're welcome.\n",
        )

    def test_request_read_in_one_run_is_kept_for_the_reply_in_the_next(self):
        self.write(human("Question?"))
        self.assertEqual(save_new(self.transcript, self.output), 0)
        self.write(reply("Answer."))
        save_new(self.transcript, self.output)
        self.assertEqual(
            self.read("response_0001_2026-10-31.md"), "## You\n\nQuestion?\n\n## Claude\n\nAnswer.\n"
        )

    def test_only_what_the_person_typed_is_a_request(self):
        self.write(
            human(
                [
                    {
                        "type": "text",
                        "text": "<ide_selection>The user selected lines from todo.md:\nprivate"
                        "</ide_selection>",
                    },
                    {"type": "text", "text": "<ide_opened_file>a.md</ide_opened_file>Make it pink"},
                    {"type": "image", "source": {}},
                ]
            ),
            # Skill text, notices and summaries are "user" records too, but not requests.
            json.dumps({"type": "user", "isMeta": True, "message": {"content": "skill text"}}),
            json.dumps(
                {
                    "type": "user",
                    "origin": {"kind": "task-notification"},
                    "message": {"content": "task done"},
                }
            ),
            json.dumps({"type": "user", "isCompactSummary": True, "message": {"content": "summary"}}),
            reply("Done."),
        )
        save_new(self.transcript, self.output)
        text = self.read("response_0001_2026-10-31.md")
        self.assertEqual(
            text, "## You\n\nMake it pink\n\n_(1 image attached)_\n\n## Claude\n\nDone.\n"
        )
        self.assertNotIn("private", text)

    def test_slash_commands_show_as_typed(self):
        self.write(
            human(
                "<command-message>review</command-message>\n"
                "<command-name>/review</command-name>\n<command-args>42</command-args>"
            ),
            reply("Reviewing."),
        )
        save_new(self.transcript, self.output)
        self.assertTrue(self.read("response_0001_2026-10-31.md").startswith("## You\n\n/review 42\n"))


if __name__ == "__main__":
    unittest.main()
