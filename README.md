# Claude Code response saver

Saves every Claude Code response to its own Markdown file in `claude_resp/` as soon as it arrives.
Files are named `response_0001_2026-10-31.md`, numbered in order, with the reply's date.

## How it works

`save_claude_responses.py` runs as a Claude Code **hook**, set up in `~/.claude/settings.json`:

- **Stop** runs it each time Claude finishes replying. It runs in the background, so it doesn't slow
  Claude down.
- **SessionEnd** runs it once more when a session closes, to catch a reply that was still being
  written to disk.

Claude Code passes the hook the path of the session's transcript (a `.jsonl` file under
`~/.claude/projects`). The script reads the lines added since its last run and saves the text of each
assistant reply. Thinking, tool calls and tool output are skipped. `claude_resp/.state.json` records
how far each transcript has been read, so no reply is saved twice.

Nothing needs to stay running, and there's nothing to start: the hooks work in every new Claude Code
session.

## Hook settings

```json
"hooks": {
  "Stop": [{ "hooks": [{ "type": "command", "async": true, "timeout": 30,
    "command": "python \"C:/Users/av/git/claude-response-saver/save_claude_responses.py\"" }] }],
  "SessionEnd": [{ "hooks": [{ "type": "command", "timeout": 30,
    "command": "python \"C:/Users/av/git/claude-response-saver/save_claude_responses.py\"" }] }]
}
```

Review or turn them off with `/hooks` in Claude Code.

## Saving older conversations

The hooks only save replies from sessions that run while the hooks are installed. To save every reply in
every saved transcript once:

```powershell
python save_claude_responses.py --all
```

Replies that were already saved are skipped. Options, set as environment variables:

- `CLAUDE_PROJECTS`: where transcripts are read from (default `~/.claude/projects`)
- `CLAUDE_RESP_DIR`: where responses are written (default `claude_resp` next to the script)

## Tests

```powershell
python -m unittest discover -s tests
```
