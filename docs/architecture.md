# CORE architecture (v0.1)

CORE has one job: **define a visual theme once, then preview or apply it across
the macOS applications that are installed.** This document is the map of how it
does that. It describes the code as it is; if the two disagree, the code is the
bug report.

```
themes/<id>/theme.toml ──► corelib/theme.py ──► corelib/render.py + corelib/templates/*
       (the theme)          (resolve roles)        (shared application templates)
                                                          │
                                                          ▼
                                                build/<id>/**   (corelib/build.py)
                                                          │
          ┌────────────────────────── corelib/transaction.py ─────────────────────────┐
          │ preflight → capture → journal → apply → verify → (preview: wait → restore) │
          └─────────────────────────────────────┬─────────────────────────────────────┘
                                                ▼
                                corelib/surfaces/*  (one per application)
                                                │
                                   corelib/effects.py  (every external effect)
```

## 1. Themes: one canonical file

A theme is exactly one file, `themes/<id>/theme.toml`. Nothing else describes a
theme: there are no profile files and no per-theme templates.

| Table | Holds |
| --- | --- |
| `[theme]` | `id` (= directory name), `name` (display), `mode` (`light`/`dark`), `description`; optional `slug` (file name for Ghostty theme / Hermes skin, default `core-<id>`), `brand` (word in prompts/banners, default `CORE`), `author` |
| `[palette]` | colours, named freely, `#RRGGBB` or a reference to another entry |
| `[roles]` | meaning: `background`, `text`, `selection` are required; the built-ins share 20 roles |
| `[terminal]`, `[terminal.ansi]` | terminal foreground/background/cursor/selection and all 16 ANSI slots (Ghostty and macOS Terminal) |
| `[prompt]` | Starship prompt semantics |
| `[apps.<app>]` | how one application consumes the roles: `vscode`, `hermes`, `obsidian`, `fastfetch`, `ghostty`, `starship`, `chatgpt`, `terminal`, `macos` |

Every value is a palette name, a role name, a literal, or — inside one
`[apps.*]` table — the name of another key of that table.

`corelib/templates/defaults.toml` supplies every `[apps.*]` key a theme does not
set (common defaults plus `[light.*]` / `[dark.*]` layers), so a theme states
only where it differs. OPTICAL's file shows the narrow override mechanism in
use: a few `[apps.obsidian]` `*_var` keys pick which CORE CSS variable a surface
uses, `highlight_alpha` adds alpha, `shadow`/`deep_shadows` choose shadows.

Built-ins keep `brand = "NODE"`, `slug = "node-<id>"`, `author`. `core theme
new` strips those, so a user's theme is never NODE-branded unless they choose it.

`corelib/theme.py` validates on load: id/name/mode, every palette and role
reference resolves (cycles are errors), `[terminal]` keys and all 16 ANSI slots.

## 2. Shared templates and the build

`corelib/templates/` holds one template per consumer (`ghostty.tmpl`,
`starship.toml.tmpl`, `fastfetch.jsonc.tmpl`, `hermes.skin.yaml.tmpl`,
`obsidian.css.tmpl`, `obsidian.manifest.json.tmpl`, `vscode.theme.json.tmpl`).
Template syntax (`corelib/render.py`): `{token}`, `{token.rgb}`,
`{token.rgb_csv}`, `{{#light}}…{{/light}}`, `{{#dark}}…{{/dark}}`,
`{{#if token}}…{{/if}}`, `{{#unless token}}…{{/unless}}`. Shared templates use
qualified tokens (`{terminal.ansi.red}`, `{apps.obsidian.caret}`): bare names
are ambiguous because nested keys alias them.

`corelib/build.py` writes `build/<id>/` (git-ignored, deterministic):

| Output | Consumer |
| --- | --- |
| `ghostty/<slug>` | Ghostty theme file |
| `terminal/CORE <ID>.terminal` | macOS Terminal profile (keyed-archived NSColor, generated in Python) |
| `starship/starship.toml`, `fastfetch/config.jsonc` | copied into place |
| `vscode/theme.json` → `vscode/settings.json` | the theme's colours as scoped VS Code settings |
| `hermes/<slug>.yaml` | Hermes skin, including `customCSS` |
| `obsidian/theme.css`, `obsidian/manifest.json` | the vault theme |
| `chatgpt/import.txt` | `codex-theme-v1:{…}` share string |
| `build/_vscode/core.core-base-0.1.0.vsix` | the one stable VS Code base extension |

Migration note: when CORE moved to shared templates, every built-in was
compared semantically against its previous output (parsed per format; Obsidian
CSS compared as resolved declarations). All consumers are equivalent except
three deliberate changes: a stray `}` in Fastfetch's ANSI escape is gone, the
Obsidian CSS now always defines `--core-blue`, and the Obsidian CSS root rule is
now valid for OPTICAL, AUGMENT and NEON (their old first selector carried stray
text, so Obsidian ignored every theme variable).

## 3. Surfaces

A surface is one application (`corelib/surfaces/base.py` is the contract):

| Method | Writes? | Purpose |
| --- | --- | --- |
| `available(paths)` | no | is the application here? (no → **N/A**, never a failure) |
| `status(ctx, theme)` | no | ACTIVE / READY / REPAIRABLE / N/A / BLOCKED (+ warnings) |
| `capture(ctx, theme)` | no | the exact prior state of what CORE will change |
| `apply(ctx, theme, captured)` | yes | calls `ensure()` first (self-healing), then applies |
| `verify(ctx, theme)` | no | problems; empty = matches |
| `restore(ctx, captured)` | yes | semantic restore of what CORE owns |
| `verify_restored(...)` | no | default: a fresh capture equals the captured state |
| `integration(ctx, theme)` | no | doctor/repair view: facts, state, repair plan, `theme_id` |
| `ensure(ctx, theme)` | yes | repair CORE-owned infrastructure; idempotent |

`kind`: `apply` (in "all"), `export` (ChatGPT), `optin` (macOS appearance),
`info` (Claude).

| Surface | CORE owns | Mechanism | Live refresh |
| --- | --- | --- | --- |
| Ghostty | theme file copy `~/.config/ghostty/themes/<slug>`; the last `theme =` line of the last config file Ghostty loads (creates `~/.config/ghostty/config` if none) | file edits; effective value read with `ghostty +show-config`; persistent apply and repair replace an older CORE's repository symlinks in the themes folder with copies (never during a preview) | AppleScript `perform action "reload_config"` |
| Terminal | profile `CORE <ID>`; default/startup profile | colours are sRGB-tagged keyed-archived NSColors (untagged calibrated RGB renders visibly off). Closed: `defaults write com.apple.Terminal "Window Settings" -dict-add …` + default keys; running: delete a stale CORE profile with AppleScript, open the `.terminal` file (Terminal's import), close the window the import opened, then AppleScript `default settings`/`startup settings`/`current settings`. Installed profiles are compared by decoded colour values (Terminal re-encodes imports). | every open tab switched by AppleScript; restore puts each tab back |
| Starship / Fastfetch | the config file (a copy, never a repo symlink) | file write | next prompt / run |
| VS Code | base extension `core.core-base` (themes "CORE Dark"/"CORE Light"); keys `workbench.colorTheme` and both `[CORE Dark]`/`[CORE Light]` entries of `workbench.colorCustomizations`, `editor.tokenColorCustomizations`, `editor.semanticTokenColorCustomizations` (applying a theme removes the other base's block) | extension via `code --install-extension <vsix>`; settings via `corelib/jsonc.py` minimal-diff edits. VS Code deletes `workbench.colorTheme` when the selected theme's extension disappears; repair treats "unset while CORE's colour blocks remain" as CORE's selection and restores it with the extension | settings are live, including in an already-open window; the first extension install may need one reload |
| Hermes | skin `~/.hermes/skins/<slug>.yaml` (with `customCSS`); `display.skin` | file writes only; Desktop `customCSS` capability detected from the installed bundle | Hermes watches `display.skin` and the skin file |
| Obsidian | per registered+initialized vault: `.obsidian/themes/<CORE - NAME>/`; `cssTheme`, `accentColor`, `theme` in `appearance.json` | file writes; running app via official CLI, **by registry ID, only for vaults whose window is open** | `theme:set`, then `theme` query to verify |
| ChatGPT | nothing | `core export <theme> chatgpt` → paste in Settings → Appearance → Import | — |
| macOS appearance | system light/dark (opt-in only) | System Events `appearance preferences` `dark mode` | immediate |
| Claude | nothing | doctor recommends "Match System" | — |

Restore is **semantic** for files an application owns and rewrites (VS Code
settings keys, Obsidian `appearance.json`, Hermes `display.skin`, Ghostty's
directive): CORE puts back the values it captured and removes what it created,
never fighting formatting. Files CORE owns outright are restored exactly.

Obsidian timing: the app re-reads `appearance.json` ~500 ms after a change and
saves its in-memory copy ~1 s after its own change; CORE waits 0.8 s before
`theme:set` and 1.3 s before querying. CLI readiness is probed with `obsidian
version` (its text distinguishes "not enabled"); the socket alone proves nothing.

## 4. Transactions

`corelib/transaction.py` runs apply and preview:

1. **Plan** (read-only): status of every surface. N/A and export/info are
   skipped; any **BLOCKED** surface stops everything before a write.
2. **Capture** every selected surface.
3. **Journal**: `~/.config/core/transaction.json` (kind, theme, captured states,
   pid) is written before the first change.
4. **Apply** in order; a surface is recorded as touched *before* its apply, so a
   half-applied surface is rolled back too.
5. **Verify** every surface.
6. Apply: remove the journal, write `~/.config/core/state.json` (last applied
   theme — internal, non-visual). Preview: wait for Enter, then restore.

Any `BaseException` after the journal exists — error, verification failure,
Ctrl-C, SIGTERM, SIGHUP — rolls back every touched surface in reverse order
with Ctrl-C/SIGTERM/SIGHUP ignored, trying every surface even if one fails. Exit
codes: 0 ok, 1 failed and rolled back, 3 attention (journal kept), 130
interrupted and rolled back. A journal left by `kill -9` or a failed rollback
blocks further changes until `core restore` undoes it.

## 5. Doctor and repair

`corelib/repair.py`. APPLY selects a theme; REPAIR restores CORE-owned
infrastructure (skin file, theme file, base extension, Terminal profile, vault
theme files) that an app update or anything else removed or damaged; DOCTOR
reports both and never writes.

`core repair [app] [--dry-run]` loads the last applied theme from `state.json`,
asks each repairable surface for its `integration()`, prints facts and a
numbered plan, and (unless dry-run) runs `ensure()` — the same function `apply`
calls — then re-checks; a repair that did not take is never reported healthy.
Repair never changes which theme an application uses.

Hermes integration states: no Hermes → N/A; Hermes without CORE skin selected →
READY; selected CORE skin missing/stale/malformed → REPAIRABLE; `display.skin`
names a CORE skin whose theme no longer exists → BLOCKED; Desktop without
`customCSS` → reported (colours still apply, selection does not).

## 6. Effects and test safety

`corelib/effects.py` is the only way CORE runs an external program. In test mode
(`CORE_TEST_MODE=1`) real effects cannot be constructed; without an installed
`FakeEffects` every effect raises `EffectBlocked`. Subprocess tests pass a JSON
script of canned responses and a call log through `CORE_TEST_EFFECTS`.

`corelib/paths.py` resolves every location at call time (`Paths.from_env()`);
nothing captures `$HOME` at import. In test mode it refuses the real home,
`/Applications`, `/System` and a build directory inside the repository.

`tests/__init__.py` runs before any test module: test mode, a throwaway HOME,
tripwire stubs first on PATH for every program CORE drives, and a before/after
fingerprint of the real configuration CORE manages. A tripwire hit or a changed
real file makes the run exit 2.

## 7. Front ends

`bin/core` is a POSIX `sh` launcher that finds Python ≥ 3.11 (stock macOS has
3.9) and runs `corelib.cli`. `corelib/cli.py` holds every command;
`corelib/tui.py` (bare `core` in a terminal) renders screens as pure functions
and runs actions through the same CLI functions — there is no second
implementation of apply, preview or repair.

## 8. How support was verified

Before v0.1.0 every surface was exercised on a real Mac at the
running-application layer, not only through its config files: Ghostty and Terminal
by asking the terminal for its live colours (OSC 10/11/4) from an already-open
window; VS Code and Hermes Desktop through their DevTools (computed CSS and
page screenshots); Obsidian through its CLI (`theme`, `dev:dom`,
`dev:screenshot`); plus window screenshots. `dev:screenshot` in Obsidian's CLI
writes the file but does not exit, which matters only to test harnesses.

## 9. Not in v0.1

No daemon or background agent, no GUI or menu bar app, no Homebrew package, no
automatic ChatGPT configuration editing, no Claude Desktop internals, no Hermes
source patches or app-bundle changes, no VS Code per-theme extensions, no
Obsidian vault-chooser theming, no design variants.
