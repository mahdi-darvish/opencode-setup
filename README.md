# OpenCode setup

My OpenCode configuration, plugins, and macOS helpers.

## Contents

| Path | What it is |
|---|---|
| `config/AGENTS.md` | Global instructions for every session: which tool to use for PDF, Excel, Word and PowerPoint, and how to report fallbacks. Symlinked from `~/.config/opencode/AGENTS.md`. |
| `config/opencode.json` | Global OpenCode config: the 9router provider with all Claude (`cc/*`) and GPT (`cx/*`) models, and the plugin list. Symlinked from `~/.config/opencode/opencode.json`. |
| `plugins/claude-usage` | OpenCode Desktop plugin: a Claude-style **Usage** panel with context window, 5-hour and weekly plan limits, and the active 9router account. |
| `plugins/snap-to-opencode` | macOS helper: double-tap ⌘ to screenshot the screen and paste it into OpenCode. |
| `macos/local.korouji.snap-to-opencode.plist` | Starts the screenshot helper at login. |
| `mcp/claude-workers/server.py` | MCP bridge to two Claude Code workers, each logged in to its own account (`~/.claude-dev`, `~/.claude-verify`). Keeps one continuing conversation per worker and project, with a 256k auto-compact window. Tools: `assign`, `status`, `list`, `cancel`, `reset`. |
| `config/agents/coordinator.md` | GPT coordinator agent that hands coding to the developer worker and checks to the verifier. Symlinked from `~/.config/opencode/agents`. |
| `mcp/imap-email/server.py` | Read-only IMAP MCP server (standard-library Python, no dependencies). Tools: `list_folders`, `search_emails`, `read_email`, `mailbox_stats`. Cannot send, delete, move, or mark as read. |

## Secrets (not in this repo)

These stay in `~/.config/opencode` and are ignored by Git:

- `9router.key`: 9router API key, used by `opencode.json`
- `9router.password`: 9router dashboard password, used by the Usage panel to show the account name
- `service.json`: OpenCode's local service password
- `email.json`: mail server, port, username, and the path of the password file, used by the email MCP server:
  `{"host": "...", "port": 993, "user": "...", "password_file": "~/.config/opencode/<name>.password"}`
- the mailbox password file that `email.json` points to

## Set up on a new Mac

1. Clone this repo to `~/opencode-setup`.
2. Link the config:
   ```sh
   mkdir -p ~/.config/opencode
   ln -sf ~/opencode-setup/config/opencode.json ~/.config/opencode/opencode.json
   ```
3. Recreate the secret files above in `~/.config/opencode` (`chmod 600`).
4. Build the Usage panel (uses the Bun bundled with OpenCode):
   ```sh
   cd ~/opencode-setup/plugins/claude-usage
   BUN_BE_BUN=1 /Applications/OpenCode.app/Contents/Resources/opencode-cli install
   BUN_BE_BUN=1 /Applications/OpenCode.app/Contents/Resources/opencode-cli x vite build
   ```
5. Build the screenshot helper and start it at login:
   ```sh
   ~/opencode-setup/plugins/snap-to-opencode/build.sh
   cp ~/opencode-setup/macos/local.korouji.snap-to-opencode.plist ~/Library/LaunchAgents/
   ```
   Then grant SnapToOpenCode **Screen Recording**, **Accessibility**, and **Input Monitoring** in System Settings → Privacy & Security, and restart it.

## Other tools

### Watching Claude workers

- Select **Workers** in the side pane’s **+** menu. It opens automatically when a worker starts in the current project.
- A separate Developer and Verifier pill above the composer shows current activity. Click one to inspect that worker.
- The panel shows actual model/account, task history, streaming public replies, expandable tool calls/results, elapsed time, Stop, latest request context and task token/cache totals.
- Each account’s 5-hour and weekly limits come directly from Claude Code `rate_limit_event` messages, not 9router. They update on model responses; while idle, the panel labels the last observation and reset time. Opening the panel makes no AI requests.
- Context is latest request **input**, not accumulated tokens across repeated requests. The 256k value is the auto-compaction budget, not a claim about the model’s actual capacity.
- Activity snapshots live privately in `~/.local/share/claude-workers/jobs/` (mode 600), outside Git. The latest 160 public activity entries per task are displayed; long outputs are shortened. Hidden thinking and raw API requests are never collected. Stop preserves the session and existing file changes.
- `python3 mcp/claude-workers/import_history.py` imports existing saved worker chats for display without copying hidden reasoning or API-request records.
- Tests: `cd mcp/claude-workers && python3 -m unittest -v test_monitor.py`; GUI: run `tsc -p .` and `vite build` in `plugins/claude-usage`.
- Design references: [Claude Code agent view](https://code.claude.com/docs/en/agent-view) and [Cursor background agents](https://cursor.com/docs/background-agent): worker status overview plus inspectable conversation/activity, rather than a single opaque waiting state.

- **9router** runs in Docker (`decolua/9router`) on `http://localhost:20128`. Its accounts and settings live in the `9router-data` Docker volume, not here.
- **OpenSuperWhisper** for voice input (hold right ⌥). Model: large-v3-turbo.
