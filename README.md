# OpenCode setup

My OpenCode configuration, plugins, and macOS helpers.

## Contents

| Path | What it is |
|---|---|
| `config/opencode.json` | Global OpenCode config: the 9router provider with all Claude (`cc/*`) and GPT (`cx/*`) models, and the plugin list. Symlinked from `~/.config/opencode/opencode.json`. |
| `plugins/claude-usage` | OpenCode Desktop plugin: a Claude-style **Usage** panel with context window, 5-hour and weekly plan limits, and the active 9router account. |
| `plugins/snap-to-opencode` | macOS helper: double-tap ⌘ to screenshot the screen and paste it into OpenCode. |
| `macos/local.korouji.snap-to-opencode.plist` | Starts the screenshot helper at login. |

## Secrets (not in this repo)

These stay in `~/.config/opencode` and are ignored by Git:

- `9router.key`: 9router API key, used by `opencode.json`
- `9router.password`: 9router dashboard password, used by the Usage panel to show the account name
- `service.json`: OpenCode's local service password

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

- **9router** runs in Docker (`decolua/9router`) on `http://localhost:20128`. Its accounts and settings live in the `9router-data` Docker volume, not here.
- **OpenSuperWhisper** for voice input (hold right ⌥). Model: large-v3-turbo.
