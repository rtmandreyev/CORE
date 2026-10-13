# Contributing to CORE

Thanks for looking. CORE is a small, single-author project on macOS, and
this file is short on purpose.

## Setup

```sh
git clone https://github.com/rtmandreyev/CORE.git ~/core
~/core/install.sh
core doctor
```

Python 3.11 or newer (`tomllib`); the launcher finds one even when
`/usr/bin/python3` is 3.9. **No third-party packages** — please keep it that way.

## Running the tests

```sh
cd ~/core
python3 -m py_compile corelib/*.py corelib/surfaces/*.py
python3 -m unittest discover -s tests -t .
```

The suite is safe to run on your own Mac. `tests/__init__.py` turns on test
mode before anything else loads: every external program CORE could run is
blocked unless a test installs a fake, `HOME` is a throwaway directory, tripwire
stubs come first on `PATH`, and your real configuration is fingerprinted before
and after. A violation makes the run exit 2.

Write tests against `tests.support.Sandbox`: it gives you a temporary theme
root, home and `/Applications`, helpers that "install" each application, and a
`FakeEffects` that records every external call.

## Theme authoring

Themes are data: one file, `themes/<name>/theme.toml` (see `docs/themes.md`).
Adding a theme must never require a Python edit, a profile file or a template
copy. If it does, that is a bug.

## Adding an application

An application is one module in `corelib/surfaces/` implementing the contract
in `corelib/surfaces/base.py`: `available`, `status`, `capture`, `apply`,
`verify`, `restore`, and, if CORE installs files the app could lose,
`integration` and `ensure` for doctor and repair. Register it in
`corelib/surfaces/__init__.py`, add a shared template and its defaults if it
consumes colours, and test it with `Sandbox`.

Use only mechanisms the application supports: its config files, its CLI, its
documented scripting interface. No bundle patching, injected scripts, simulated
keystrokes or undocumented databases. If no supported mechanism exists, the
honest answer is "not supported".

## Changing an application template

Templates live in `corelib/templates/` and are shared by every theme. Use
qualified tokens (`{terminal.ansi.red}`, `{apps.obsidian.caret}`). When a theme
needs to differ, add a key with a default in `corelib/templates/defaults.toml`
and set it in that theme's file — never fork a template.

## Safety expectations for changes

* Run external programs only through `corelib.effects`; resolve paths only
  through `corelib.paths.Paths`.
* Capture before you write; restore semantically what an application owns.
* A missing application is N/A. Only an installed-but-unsafe application is
  BLOCKED, and it blocks before anything is written.
* `doctor`, `status` and `--dry-run` never write.
* Use argument arrays for subprocesses, never `shell=True`.

## Pull requests

Keep them small and focused, describe how you verified the change, and paste
the test summary line. Behaviour changes update `docs/` in the same PR.

## Reporting

Bug reports and feature requests are welcome through GitHub issues. Please
include your macOS version, `core --version`, `core doctor` output and the
exact command — **with credentials, tokens, private paths and personal
configuration redacted**. Security-sensitive reports: see [SECURITY.md](SECURITY.md).
