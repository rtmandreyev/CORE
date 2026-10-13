# Changelog

All notable changes to CORE are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0]

First public release. See [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md).

### Added

- One canonical theme file per theme, compiled through shared application templates.
- Six built-in themes: OPTICAL, SHELL, EMBER, GLACIER, AUGMENT, NEON.
- Automatic theming for Ghostty, macOS Terminal, Starship, Fastfetch, VS Code,
  Hermes (CLI and Desktop) and Obsidian.
- ChatGPT theme export (`core export <theme> chatgpt [--copy]`).
- Opt-in macOS light/dark switching.
- `core preview`, transactional `core apply`, `core restore`.
- `core doctor` and `core repair [app] [--dry-run]`.
- `core theme new`, `core build`, `core list`, `core status`.
- An interactive terminal app (`core`).
- `install.sh`, which links `~/.local/bin/core`.
- A hermetic test suite with a safety boundary that blocks real external effects.
