"""Test-suite safety boundary. Runs before any test module imports CORE.

1. Test mode: corelib.effects refuses every real external effect, and
   Paths.from_env refuses the real home, /Applications and the real repository.
2. HOME points at a throwaway directory for the whole run.
3. PATH starts with tripwire stubs for every program CORE could drive
   (osascript, open, obsidian, code, defaults, pbcopy, ps, ghostty). A stub
   only records that it was reached and fails.
4. The real configuration CORE manages is fingerprinted (stat only, never
   opened for writing) before and after the run.

If a tripwire fired or a real file changed, the run exits non-zero.
"""

from __future__ import annotations

import atexit
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
SANDBOX = Path(tempfile.mkdtemp(prefix="core-tests-"))
TRIPWIRE = SANDBOX / "tripwire.log"
STUBS = SANDBOX / "stubs"
STUBBED = ("osascript", "open", "obsidian", "code", "defaults", "pbcopy", "ps", "ghostty", "starship", "fastfetch")

WATCHED = [
    ".config/ghostty", ".config/ghostty/config", ".config/ghostty/themes", ".config/starship.toml",
    ".config/fastfetch/config.jsonc", ".config/core", "Library/Application Support/Code/User/settings.json",
    ".vscode/extensions", ".vscode/extensions/extensions.json", ".hermes/config.yaml", ".hermes/skins",
    "Library/Application Support/obsidian/obsidian.json", "Library/Preferences/com.apple.Terminal.plist",
    ".codex/config.toml", "Library/Application Support/com.mitchellh.ghostty",
]


def fingerprint() -> dict[str, tuple]:
    prints = {}
    for relative in WATCHED:
        path = REAL_HOME / relative
        try:
            info = path.lstat()
            prints[relative] = (info.st_mtime_ns, info.st_size, info.st_mode)
        except FileNotFoundError:
            prints[relative] = None
    return prints


BEFORE = fingerprint()

STUBS.mkdir()
for name in STUBBED:
    stub = STUBS / name
    stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{TRIPWIRE}"\nexit 99\n')
    stub.chmod(0o755)

os.environ["CORE_TEST_MODE"] = "1"
os.environ["HOME"] = str(SANDBOX / "home")
os.environ["PATH"] = f"{STUBS}:{os.path.dirname(sys.executable)}:/usr/bin:/bin"
for variable in ("CORE_ROOT", "CORE_BUILD_DIR", "CORE_APP_DIR", "CORE_SYSTEM_APP_DIR", "CORE_TEST_EFFECTS"):
    os.environ.pop(variable, None)
(SANDBOX / "home").mkdir()


def _check() -> None:
    problems = []
    if TRIPWIRE.exists() and TRIPWIRE.read_text().strip():
        problems.append("a real program was reached:\n" + TRIPWIRE.read_text())
    after = fingerprint()
    changed = [name for name in WATCHED if BEFORE[name] != after[name]]
    if changed:
        problems.append("real files changed during the run: " + ", ".join(changed))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    if problems:
        sys.stderr.write("\nCORE TEST SAFETY BOUNDARY VIOLATED\n" + "\n".join(problems) + "\n")
        os._exit(2)


atexit.register(_check)
