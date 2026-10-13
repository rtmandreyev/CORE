# CORE agent rules

## Mission

CORE v0.1 has one purpose: **define a universal visual theme once, then preview
or apply it across the supported macOS applications that are installed.** Keep
it small, reliable and reversible. `docs/architecture.md` is the map; read it
before changing code.

## Architecture rules (verified behaviour — preserve them)

* **One canonical theme file.** A theme is `themes/<id>/theme.toml` and nothing
  else. No profile files, no per-theme templates, no Python edits to add a theme.
* **Shared templates.** Application templates live only in `corelib/templates/`.
  A theme that needs to differ gets a key with a default in
  `corelib/templates/defaults.toml`; never fork a template. Use qualified tokens.
* **Built-in output is pinned.** The six built-ins were migrated with a semantic
  old-vs-new comparison (see architecture §2). A template change that alters a
  built-in's appearance is a design change, not a refactor — say so.
* **Missing applications are N/A**, never failures. BLOCKED stops an apply
  before any write.
* **Transactions.** Apply and preview capture first, journal before the first
  write, roll back on any `BaseException`, try every surface during rollback,
  and keep the journal when anything could not be restored (`core restore`).
* **Repair restores CORE-owned infrastructure only** and uses the same
  `ensure()` that apply uses. It never changes which theme an app uses.
* **Doctor, status and `--dry-run` never write.**
* **One front end implementation.** The interactive app calls the CLI functions;
  never add a second apply/preview/repair path.
* **Supported mechanisms only.** No app-bundle changes, no source patches, no
  injected JavaScript, no synthetic keystrokes, no undocumented databases. The
  Hermes patch/rebuild mechanism was removed deliberately; do not bring it back.

## Effects and paths

* Every external program goes through `corelib.effects.current()`. Never call
  `subprocess` directly from a surface.
* Every location comes from `corelib.paths.Paths`; nothing may read `$HOME` or
  `Path.home()` at import time.

## Tests

* Run: `python3 -m unittest discover -s tests -t .` and report the real count
  and result. It is safe on a real Mac: `tests/__init__.py` enforces test mode,
  a throwaway HOME, tripwire PATH stubs and a real-file fingerprint; a violation
  exits 2. Never weaken that file to make a test pass.
* Write tests with `tests.support.Sandbox`. A test that needs an external effect
  installs a fake response; it never reaches a real program.

## Hard safety boundary — a user's Mac is not a test environment

Without explicit permission in the current conversation, agents must NOT:

* run a real (non-dry-run) `core apply`, `core preview`, `core repair` or
  `core restore`, or change live application settings any other way;
* run `code --install-extension/--uninstall-extension`, the Obsidian CLI,
  `defaults write`, or AppleScript that changes an application;
* modify `~/.hermes`, VS Code settings or extensions, Obsidian vaults, Ghostty or
  Terminal configuration, `~/.codex`, or macOS settings;
* install or uninstall applications or packages, use `sudo`, or replace anything
  in `/Applications`;
* run destructive git commands, rewrite history, push, or publish.

Allowed without asking: reading anything; `core doctor`, `core status`,
`core list`, `core build`, `core theme new`, `core export`, and any `--dry-run`;
running the test suite; editing files inside this repository for the task.

If a task needs to cross the boundary, stop and state the exact action and why.

## Git

Commit only when asked. Never push without explicit permission. Never force-push
or rewrite history. Never discard uncommitted work you did not create.

## Licensing

CORE is MIT-licensed (`LICENSE`). Do not add code or assets under other terms
without saying so in the change.

## Documentation

Docs describe the code as it is. When behaviour changes, change
`docs/architecture.md` (and `docs/commands.md` / `docs/themes.md` when they are
affected) in the same task. Do not add numbers you did not measure.
