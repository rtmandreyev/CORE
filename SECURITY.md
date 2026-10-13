# Security

## Reporting a vulnerability

CORE is a small single-author project and has no security team. If you
find a security-relevant problem, please report it **privately** rather than in a
public issue:

* Preferred: GitHub's private vulnerability reporting on this repository
  (*Security* → *Report a vulnerability*), which keeps the report, the
  discussion and the fix out of public view until a fix exists.
* If that is unavailable, open a public issue that says only that you have a
  security-sensitive report and how to reach you — no details.

There is no security email address, and none is invented here. Please do not
send credentials or private configuration to any address you find in this
repository.

## What CORE does that is security-relevant

CORE edits files that belong to other applications on your machine. That is its
whole purpose, so the honest version of its threat model is short:

* It writes **only** to the locations listed in `docs/architecture.md` (§3),
  and it captures each one before it writes. Every external program it runs
  goes through one module, `corelib/effects.py`.
* Apply and preview are transactional: a journal is written before the first
  change, and failures or interruptions roll back.
* `--dry-run`, `core status` and `core doctor` never write.
* It never asks for `sudo`, never installs packages and never modifies an
  application bundle.
* Its automation is limited to supported mechanisms that macOS gates behind
  Automation permission: Ghostty's `perform action "reload_config"`, Terminal's
  `default settings` / `current settings`, and — only when you select the macOS
  appearance surface — System Events' `dark mode`. It does not inject
  JavaScript or simulate keystrokes.

## Please do not include in a report or an issue

* API keys, tokens, passwords or private keys — anything that looks like
  `ghp_…`, `sk-…`, `AKIA…`, `xox…` or a `BEGIN … PRIVATE KEY` block.
* Your `~/.hermes/config.yaml`, `~/.config/ghostty/config`, VS Code settings,
  Obsidian vault contents, `~/.codex/config.toml` or `~/.config/core/*.json`.
* Screenshots of terminals showing tokens or private file paths.

Redact or paraphrase. A stack trace and the command you ran are almost always
enough to reproduce a problem.

## Supported versions

Only the latest release is supported. Fixes land on the main branch and ship in
the next release.
