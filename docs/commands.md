# Commands

`core` with no arguments opens the interactive app. Every command below is what
the app runs underneath.

| Command | Changes your apps? | What it does |
| --- | --- | --- |
| `./install.sh` | no | link `~/.local/bin/core` to this checkout (idempotent; never edits shell files) |
| `core --version` | no | prints `core 0.1.0` |
| `core list` | no | the themes in `themes/` |
| `core status` | no | the active theme and each application's state |
| `core doctor [app…]` | no | read-only diagnosis of every application CORE knows |
| `core build <theme>` | no | compile into `build/<theme>/` (repository only) |
| `core preview <theme> [app…] [--dry-run]` | temporarily | apply, wait for Enter, restore and verify |
| `core apply <theme> [app…] [--dry-run]` | yes | apply and keep; all-or-nothing |
| `core restore` | yes | undo an interrupted apply or preview from its journal |
| `core repair [app] [--dry-run]` | CORE files only | restore CORE-owned files after an app update; `app` is one of `ghostty`, `terminal`, `starship`, `fastfetch`, `vscode`, `hermes`, `obsidian` |
| `core theme new <name> --from <theme> [--name "Display"]` | no | start a theme (one file) |
| `core export <theme> chatgpt [--copy]` | no | ChatGPT's own theme-share string (`--copy` uses `pbcopy`); import it under Settings → Appearance → Import |
| `core optical`, `core shell`, … | yes | shorthand for `core apply <built-in>` |

Application names: `ghostty`, `terminal`, `starship`, `fastfetch`, `vscode`,
`hermes`, `obsidian`, `chatgpt`, `macos`. Without names, "all available
applications" is used: everything installed except ChatGPT (export/import) and
macOS appearance (opt-in: name it, or set `[surfaces] macos = true` in
`~/.config/core/config.toml`).

States: **ACTIVE** (already showing this theme), **READY** (will apply),
**REPAIRABLE** (CORE's own files need repair; apply repairs them first), **N/A**
(not installed — skipped, never an error), **BLOCKED** (cannot be changed safely;
nothing is applied until you fix it or leave that application out).

Exit codes: `0` ok · `1` failed, everything rolled back · `3` attention: something
could not be put back, run `core restore` · `130` interrupted, rolled back.
