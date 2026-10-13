"""Fixtures: a disposable machine per test.

`Sandbox` owns a temporary theme root (a copy of every themes/*/theme.toml),
a temporary home with fake /Applications and /System/Applications, a build
directory, and a FakeEffects that records every external call. Helpers
"install" applications so any combination of apps can be tested.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from corelib import effects
from corelib.build import VSCODE_EXTENSION_ID, VSCODE_EXTENSION_VERSION
from corelib.paths import Paths
from corelib.surfaces import Context

REPO = Path(__file__).resolve().parent.parent
BUILTINS = ("optical", "shell", "ember", "glacier", "augment", "neon")


class Sandbox:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="core-sandbox-"))
        self.root = self.tmp / "root"
        self.home = self.tmp / "home"
        self.apps = self.home / "Applications"
        self.system = self.home / "System/Applications"
        for path in (self.root / "themes", self.home, self.apps, self.system):
            path.mkdir(parents=True)
        for theme in BUILTINS:
            (self.root / "themes" / theme).mkdir()
            shutil.copy(REPO / "themes" / theme / "theme.toml", self.root / "themes" / theme / "theme.toml")
        self.paths = Paths(root=self.root, build_dir=self.tmp / "build", home=self.home,
                           app_dir=self.apps, system_app_dir=self.system)
        self.paths.assert_sandboxed()
        self.fake = effects.FakeEffects(responses=[], which_map={})
        self.respond(["ps"], stdout="")
        self.respond(["defaults", "read", "-g", "AppleInterfaceStyle"], returncode=1)
        effects.install(self.fake)

    def close(self) -> None:
        effects.install(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── effects ──

    def respond(self, match, *, stdout="", stderr="", returncode=0, contains=None, writes=None, once=False, first=False):
        entry = {"match": list(match), "stdout": stdout, "stderr": stderr, "returncode": returncode}
        if contains:
            entry["contains"] = contains
        if writes:
            entry["writes"] = {str(k): v for k, v in writes.items()}
        if once:
            entry["once"] = True
        if first:
            self.fake.responses.insert(0, entry)
        else:
            self.fake.responses.append(entry)

    def running(self, *binaries: Path) -> None:
        self.respond(["ps"], stdout="\n".join(str(b) for b in binaries) + "\n", first=True)

    def ctx(self) -> Context:
        return Context(self.paths)

    def env(self) -> dict[str, str]:
        script = self.tmp / "effects.json"
        script.write_text(json.dumps({"log": str(self.tmp / "effects.log"), "responses": self.fake.responses,
                                      "which": self.fake.which_map}))
        env = dict(os.environ)
        env.update(CORE_TEST_MODE="1", HOME=str(self.home), CORE_ROOT=str(self.root),
                   CORE_BUILD_DIR=str(self.paths.build_dir), CORE_APP_DIR=str(self.apps),
                   CORE_SYSTEM_APP_DIR=str(self.system), CORE_TEST_EFFECTS=str(script),
                   PYTHONPATH=str(REPO), NO_COLOR="1")
        return env

    def subprocess_calls(self) -> list[list[str]]:
        log = self.tmp / "effects.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def run(self, *args: str, stdin: str | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
        """`core <args>` as a separate process; stdin is never the terminal."""

        return subprocess.run(
            [sys.executable, "-m", "corelib.cli", *args], cwd=self.tmp, env=self.env(),
            input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )

    def cli(self, *args: str, wait=None) -> tuple[int, str]:
        """`core <args>` in-process (the installed FakeEffects observe it)."""

        from corelib import cli

        buffer = io.StringIO()
        old = {k: os.environ.get(k) for k in ("HOME", "CORE_ROOT", "CORE_BUILD_DIR", "CORE_APP_DIR", "CORE_SYSTEM_APP_DIR")}
        os.environ.update(HOME=str(self.home), CORE_ROOT=str(self.root), CORE_BUILD_DIR=str(self.paths.build_dir),
                          CORE_APP_DIR=str(self.apps), CORE_SYSTEM_APP_DIR=str(self.system))
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                if wait is not None:
                    parsed = cli.parser().parse_args(list(args))
                    code = cli.run_transaction(parsed.theme, parsed.apps, preview=True, dry_run=False,
                                               out=print, wait=wait)
                else:
                    code = cli.main(list(args))
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return code, buffer.getvalue()

    # ── applications ──

    def ghostty(self, config: str | None = None, *, location: str = "xdg") -> Path:
        (self.apps / "Ghostty.app/Contents/MacOS").mkdir(parents=True, exist_ok=True)
        if location == "xdg":
            path = self.home / ".config/ghostty/config"
        else:
            path = self.home / "Library/Application Support/com.mitchellh.ghostty/config.ghostty"
        if config is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config)
        return path

    def terminal(self, *, default: str = "Basic", profiles: dict | None = None, running: bool = False) -> dict:
        """Install Terminal; `defaults` for com.apple.Terminal is simulated statefully."""

        (self.system / "Utilities/Terminal.app/Contents/MacOS").mkdir(parents=True, exist_ok=True)
        prefs = {"Default Window Settings": default, "Startup Window Settings": default,
                 "Window Settings": profiles or {"Basic": {"name": "Basic"}}}
        self.terminal_prefs = prefs

        def handle(argv, _input):
            if argv[:1] != ["defaults"] or "com.apple.Terminal" not in argv:
                return None
            if argv[1] == "export":
                return effects.Result(0, plistlib.dumps(prefs, fmt=plistlib.FMT_XML).decode())
            if argv[1] == "write" and argv[4:5] == ["-dict-add"]:
                document = '<?xml version="1.0"?><plist version="1.0">' + argv[6] + "</plist>"
                prefs.setdefault(argv[3], {})[argv[5]] = plistlib.loads(document.encode())
                return effects.Result(0)
            if argv[1] == "write" and argv[4:5] == ["-string"]:
                prefs[argv[3]] = argv[5]
                return effects.Result(0)
            if argv[1] == "delete":
                prefs.pop(argv[3], None)
                return effects.Result(0)
            return effects.Result(1, "", "unsupported defaults call in test")

        self.fake.handlers.append(handle)
        if running:
            self.running(self.system / "Utilities/Terminal.app/Contents/MacOS/Terminal")
        return prefs

    def starship(self, config: str | None = None) -> Path:
        self.fake.which_map["starship"] = "/opt/homebrew/bin/starship"
        path = self.home / ".config/starship.toml"
        if config is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config)
        return path

    def fastfetch(self, config: str | None = None) -> Path:
        self.fake.which_map["fastfetch"] = "/opt/homebrew/bin/fastfetch"
        path = self.home / ".config/fastfetch/config.jsonc"
        if config is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config)
        return path

    def vscode(self, settings: str | None = None, *, extension: bool = False, cli: bool = True) -> Path:
        app = self.apps / "Visual Studio Code.app"
        (app / "Contents/Resources/app/bin").mkdir(parents=True, exist_ok=True)
        if cli:
            (app / "Contents/Resources/app/bin/code").write_text("")
        path = self.home / "Library/Application Support/Code/User/settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if settings is not None:
            path.write_text(settings)
        extensions = self.home / ".vscode/extensions"
        folder = f"{VSCODE_EXTENSION_ID}-{VSCODE_EXTENSION_VERSION}"
        registry = json.dumps([{"identifier": {"id": VSCODE_EXTENSION_ID}, "version": VSCODE_EXTENSION_VERSION,
                                "relativeLocation": folder}])
        if extension:
            (extensions / folder).mkdir(parents=True, exist_ok=True)
            (extensions / "extensions.json").write_text(registry)
        # VS Code's own CLI is what registers the extension.
        self.respond(["code", "--install-extension"], stdout="Extension 'core.core-base' was successfully installed.",
                     writes={extensions / "extensions.json": registry, extensions / folder / "package.json": "{}"})
        return path

    def hermes(self, config: str = "model: x\ndisplay:\n  skin: default\n  compact: true\n", *,
               desktop: bool = True, custom_css: bool = True) -> Path:
        path = self.home / ".hermes/config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config)
        if desktop:
            assets = self.apps / "Hermes.app/Contents/Resources/app.asar.unpacked/dist/assets"
            assets.mkdir(parents=True, exist_ok=True)
            marker = "const cssId = 'hermes-desktop-custom-css'" if custom_css else "const x = 1"
            (assets / "context-ABC.js").write_text(marker)
        return path

    def obsidian(self, vaults: dict[str, dict] | None = None) -> dict[str, Path]:
        """vaults: {id: {"path": relative, "open": bool, "appearance": dict|None, "init": bool}}"""

        (self.apps / "Obsidian.app/Contents/MacOS").mkdir(parents=True, exist_ok=True)
        registry, made = {}, {}
        for vault_id, spec in (vaults or {}).items():
            path = self.home / spec["path"]
            path.mkdir(parents=True, exist_ok=True)
            if spec.get("init", True):
                (path / ".obsidian").mkdir(exist_ok=True)
                if spec.get("appearance") is not None:
                    (path / ".obsidian/appearance.json").write_text(json.dumps(spec["appearance"], indent=2))
            registry[vault_id] = {"path": str(path), "ts": 1}
            if spec.get("open"):
                registry[vault_id]["open"] = True
            made[vault_id] = path
        reg = self.home / "Library/Application Support/obsidian/obsidian.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"vaults": registry}))
        return made

    def obsidian_cli(self, *, enabled: bool = True) -> None:
        cli = self.apps / "Obsidian.app/Contents/MacOS/obsidian-cli"
        cli.write_text("")
        (self.home / ".obsidian-cli.sock").write_text("")
        if not enabled:
            self.respond(["obsidian-cli"], stdout="Command line interface is not enabled. Please turn it on in Settings > General > Advanced.")
            return
        self.respond(["obsidian-cli", "version"], stdout="1.13.7")

    def chatgpt(self) -> None:
        (self.apps / "ChatGPT.app").mkdir(parents=True, exist_ok=True)

    def claude(self) -> None:
        (self.apps / "Claude.app").mkdir(parents=True, exist_ok=True)

    def tree(self) -> dict[str, bytes]:
        """Every file under the sandbox home (for read-only assertions)."""

        found = {}
        for path in sorted(self.home.rglob("*")):
            if path.is_file() or path.is_symlink():
                found[str(path.relative_to(self.home))] = path.read_bytes() if path.is_file() else b"->" + os.readlink(path).encode()
        return found


class SandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.close)
