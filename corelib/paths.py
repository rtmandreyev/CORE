"""Every location CORE reads or writes, resolved at call time.

Nothing here runs at import: `Paths.from_env()` reads the environment when a
command starts, so loading CORE never captures the real home directory. Tests
build a `Paths` for a temporary home and temporary application directories, and
in test mode `from_env` refuses to point at the real machine at all.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from corelib import effects


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def real_home() -> Path:
    """The account's real home directory, independent of $HOME."""

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


class UnsafePaths(RuntimeError):
    """Test mode was asked to resolve a path on the real machine."""


@dataclass(frozen=True)
class Paths:
    root: Path  # the directory that contains themes/
    build_dir: Path  # compiled output (repository-local, never committed)
    home: Path
    app_dir: Path  # /Applications
    system_app_dir: Path  # /System/Applications

    # ── CORE's own state ──
    @property
    def config_home(self) -> Path:
        return self.home / ".config"

    @property
    def state_dir(self) -> Path:
        return self.config_home / "core"

    @property
    def journal(self) -> Path:
        return self.state_dir / "transaction.json"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def user_config(self) -> Path:
        return self.state_dir / "config.toml"

    @property
    def themes_dir(self) -> Path:
        return self.root / "themes"

    # ── Ghostty ──
    @property
    def ghostty_app(self) -> Path:
        return self.app_dir / "Ghostty.app"

    @property
    def ghostty_bin(self) -> Path:
        return self.ghostty_app / "Contents/MacOS/ghostty"

    @property
    def ghostty_configs(self) -> list[Path]:
        """Ghostty's configuration files in the order Ghostty loads them.

        A later file overrides an earlier one, and inside a file the last
        directive wins.
        """

        support = self.home / "Library/Application Support/com.mitchellh.ghostty"
        xdg = self.config_home / "ghostty"
        return [xdg / "config.ghostty", xdg / "config", support / "config.ghostty", support / "config"]

    @property
    def ghostty_themes_dir(self) -> Path:
        return self.config_home / "ghostty" / "themes"

    # ── macOS Terminal ──
    @property
    def terminal_app(self) -> Path:
        return self.system_app_dir / "Utilities/Terminal.app"

    @property
    def terminal_profiles_dir(self) -> Path:
        return self.state_dir / "terminal"

    # ── Starship / Fastfetch ──
    @property
    def starship_config(self) -> Path:
        return self.config_home / "starship.toml"

    @property
    def fastfetch_config(self) -> Path:
        return self.config_home / "fastfetch" / "config.jsonc"

    # ── VS Code ──
    @property
    def vscode_app(self) -> Path:
        return self.app_dir / "Visual Studio Code.app"

    @property
    def vscode_cli(self) -> Path:
        return self.vscode_app / "Contents/Resources/app/bin/code"

    @property
    def vscode_settings(self) -> Path:
        return self.home / "Library/Application Support/Code/User/settings.json"

    @property
    def vscode_extensions_dir(self) -> Path:
        return self.home / ".vscode/extensions"

    # ── Hermes ──
    @property
    def hermes_home(self) -> Path:
        return self.home / ".hermes"

    @property
    def hermes_config(self) -> Path:
        return self.hermes_home / "config.yaml"

    @property
    def hermes_skins_dir(self) -> Path:
        return self.hermes_home / "skins"

    @property
    def hermes_app(self) -> Path:
        return self.app_dir / "Hermes.app"

    # ── Obsidian ──
    @property
    def obsidian_app(self) -> Path:
        return self.app_dir / "Obsidian.app"

    @property
    def obsidian_cli(self) -> Path:
        return self.obsidian_app / "Contents/MacOS/obsidian-cli"

    @property
    def obsidian_registry(self) -> Path:
        return self.home / "Library/Application Support/obsidian/obsidian.json"

    # ── ChatGPT / Claude (read-only) ──
    @property
    def chatgpt_app(self) -> Path:
        return self.app_dir / "ChatGPT.app"

    @property
    def claude_app(self) -> Path:
        return self.app_dir / "Claude.app"

    @property
    def claude_config(self) -> Path:
        return self.home / "Library/Application Support/Claude/config.json"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Paths":
        env = dict(os.environ if env is None else env)
        test_mode = env.get(effects.TEST_MODE_ENV) == "1"
        home = Path(env.get("HOME") or real_home()).expanduser()

        default_apps = home / "Applications" if test_mode else Path("/Applications")
        default_system = home / "System/Applications" if test_mode else Path("/System/Applications")
        root = Path(env.get("CORE_ROOT") or repo_root()).expanduser().resolve()

        paths = cls(
            root=root,
            build_dir=Path(env.get("CORE_BUILD_DIR") or root / "build").expanduser(),
            home=home,
            app_dir=Path(env.get("CORE_APP_DIR") or default_apps).expanduser(),
            system_app_dir=Path(env.get("CORE_SYSTEM_APP_DIR") or default_system).expanduser(),
        )

        if test_mode:
            paths.assert_sandboxed()

        return paths

    def assert_sandboxed(self) -> None:
        """Refuse any location on the real machine. Called in test mode."""

        problems = []

        if self.home.resolve() == real_home().resolve():
            problems.append(f"HOME is the real home ({self.home})")

        for label, path in (("app_dir", self.app_dir), ("system_app_dir", self.system_app_dir)):
            if str(path).startswith(("/Applications", "/System")):
                problems.append(f"{label} is a real application directory ({path})")

        repo = repo_root().resolve()
        build = self.build_dir.resolve()

        if build == repo or repo in build.parents:
            problems.append(f"build output would land in the real repository ({self.build_dir})")

        if problems:
            raise UnsafePaths("test mode refuses real paths: " + "; ".join(problems))
