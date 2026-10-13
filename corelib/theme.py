"""The canonical theme document: one TOML file per theme.

`themes/<id>/theme.toml` is the only file a theme author edits. It holds:

    [theme]          id, name, mode (light|dark), description, optional slug/author
    [palette]        the colours, named however the author likes
    [roles]          what each colour means (background, text, signal, ...)
    [terminal]       terminal foreground/background/cursor/selection
    [terminal.ansi]  the 16 ANSI slots
    [prompt]         Starship prompt semantics
    [apps.<name>]    how each application consumes the roles

Every value is a palette name, a role name or a literal (`#RRGGBB`, or plain
text for non-colour options). Application templates are shared and live in
`corelib/templates/`; `corelib/templates/defaults.toml` supplies the app keys a
theme does not set, so a theme only states what it wants to differ.
"""

from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
NAME_RULE = "use lowercase letters, digits and dashes, starting with a letter (e.g. 'solar')"
MODES = ("light", "dark")
ANSI_SLOTS = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "bright_black", "bright_red", "bright_green", "bright_yellow",
    "bright_blue", "bright_magenta", "bright_cyan", "bright_white",
)
REQUIRED_ROLES = ("background", "text", "selection")
DEFAULTS_FILE = Path(__file__).resolve().parent / "templates" / "defaults.toml"


class ThemeError(Exception):
    pass


def is_valid_name(value: object) -> bool:
    return isinstance(value, str) and bool(NAME_RE.match(value))


def _merge_defaults(data: dict, defaults: dict) -> dict:
    """Defaults under the theme: a key the theme sets always wins."""

    result = copy.deepcopy(defaults)

    for key, value in data.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(value, result[key])
        else:
            result[key] = copy.deepcopy(value)

    return result


def _load_defaults() -> dict:
    with DEFAULTS_FILE.open("rb") as handle:
        return tomllib.load(handle)


class Theme:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

        try:
            with self.path.open("rb") as handle:
                own = tomllib.load(handle)
        except FileNotFoundError:
            raise ThemeError(f"theme file not found: {self.path}") from None
        except tomllib.TOMLDecodeError as exc:
            raise ThemeError(f"{self.path}: invalid TOML ({exc})") from None

        self.own = own
        meta = own.get("theme")
        if not isinstance(meta, dict):
            raise ThemeError(f"{self.path}: missing [theme] table")

        defaults = _load_defaults()
        mode = str(meta.get("mode") or meta.get("variant") or "").lower()
        app_defaults = _merge_defaults(
            defaults.get(mode, {}).get("apps", {}), defaults.get("apps", {})
        )
        apps = own.get("apps", {}) if isinstance(own.get("apps"), dict) else {}
        # Only the [apps.*] tables take defaults; palette, roles and terminal are
        # the author's alone.
        self.raw = dict(own)
        self.raw["apps"] = _merge_defaults(apps, app_defaults)

        self.meta = meta
        self.palette: dict[str, str] = dict(own.get("palette", {}))
        self.roles: dict[str, str] = dict(own.get("roles", {}))
        self._validate()

    # ── identity ──

    @property
    def id(self) -> str:
        return str(self.meta["id"])

    @property
    def name(self) -> str:
        return str(self.meta["name"])

    @property
    def mode(self) -> str:
        return str(self.meta.get("mode") or self.meta.get("variant")).lower()

    @property
    def description(self) -> str:
        return str(self.meta.get("description", ""))

    @property
    def author(self) -> str:
        return str(self.meta.get("author") or "CORE")

    @property
    def brand(self) -> str:
        """The word printed in prompts and banners (built-ins say NODE)."""

        return str(self.meta.get("brand") or "CORE")

    @property
    def slug(self) -> str:
        """The file-safe name shared by the Ghostty theme and the Hermes skin."""

        return str(self.meta.get("slug") or f"core-{self.id}")

    @property
    def short_name(self) -> str:
        """The display name without a brand prefix (`NODE // SHELL` -> `SHELL`)."""

        return self.name.split("//")[-1].strip() or self.id.upper()

    def app(self, name: str) -> dict:
        value = self.raw.get("apps", {}).get(name, {})
        return value if isinstance(value, dict) else {}

    def app_value(self, app: str, key: str, fallback: str = "") -> str:
        value = self.app(app).get(key, fallback)
        return self.resolve_or_literal(value) if isinstance(value, str) else value

    @property
    def obsidian_name(self) -> str:
        return str(self.app("obsidian").get("vault_theme") or f"CORE - {self.id.upper()}")

    @property
    def obsidian_accent(self) -> str:
        return self.resolve_or_literal(str(self.app("obsidian").get("accent_color") or "signal_emphasis"))

    @property
    def macos_appearance(self) -> str:
        value = str(self.app("macos").get("appearance") or self.mode).lower()
        return value if value in MODES else self.mode

    # ── resolution ──

    def resolve(self, value: str | None, visiting: frozenset[str] = frozenset()) -> str:
        if value is None:
            raise ThemeError("reference is empty")

        value = value.strip()

        if HEX_RE.fullmatch(value):
            return value

        if value in visiting:
            raise ThemeError(f"reference cycle involving {value!r}")

        if value in self.palette:
            return self.resolve(self.palette[value], visiting | {value})

        if value in self.roles:
            return self.resolve(self.roles[value], visiting | {value})

        raise ThemeError(f"unknown colour or reference {value!r}")

    def resolve_or_literal(self, value: str) -> str:
        try:
            return self.resolve(value)
        except ThemeError:
            return value

    def color(self, section: str, key: str) -> str:
        """Resolve `[section] key`, e.g. color('terminal', 'background')."""

        table = self.raw.get(section, {})
        if section.startswith("terminal.ansi"):
            table = self.raw.get("terminal", {}).get("ansi", {})
        if key not in table:
            raise ThemeError(f"[{section}] has no {key!r}")
        return self.resolve(table[key])

    def ansi(self) -> list[str]:
        return [self.color("terminal.ansi", slot) for slot in ANSI_SLOTS]

    # ── validation ──

    def _validate(self) -> None:
        where = self.path

        for key in ("id", "name"):
            if not self.meta.get(key):
                raise ThemeError(f"{where}: [theme] {key} is required")

        if not is_valid_name(self.meta["id"]):
            raise ThemeError(f"{where}: [theme] id {self.meta['id']!r} is invalid: {NAME_RULE}")

        if self.path.parent.name != self.meta["id"] and self.path.name == "theme.toml":
            raise ThemeError(
                f"{where}: [theme] id {self.meta['id']!r} must match its directory "
                f"name {self.path.parent.name!r}"
            )

        if self.mode not in MODES:
            raise ThemeError(f"{where}: [theme] mode must be 'light' or 'dark'")

        if "slug" in self.meta and not is_valid_name(self.meta["slug"]):
            raise ThemeError(f"{where}: [theme] slug is invalid: {NAME_RULE}")

        for name, value in self.palette.items():
            try:
                self.resolve(value, frozenset({name}))
            except ThemeError as exc:
                raise ThemeError(f"{where}: [palette] {name}: {exc}") from None

        for role in REQUIRED_ROLES:
            if role not in self.roles:
                raise ThemeError(f"{where}: [roles] must define {role!r}")

        for role, value in self.roles.items():
            try:
                self.resolve(value)
            except ThemeError as exc:
                raise ThemeError(f"{where}: [roles] {role}: {exc}") from None

        terminal = self.raw.get("terminal", {})
        for key in ("foreground", "background", "cursor", "selection_background", "selection_foreground"):
            if key not in terminal:
                raise ThemeError(f"{where}: [terminal] must define {key!r}")
            self.color("terminal", key)

        ansi = terminal.get("ansi", {})
        missing = [slot for slot in ANSI_SLOTS if slot not in ansi]
        if missing:
            raise ThemeError(f"{where}: [terminal.ansi] is missing {', '.join(missing)}")
        for slot in ANSI_SLOTS:
            try:
                self.color("terminal.ansi", slot)
            except ThemeError as exc:
                raise ThemeError(f"{where}: [terminal.ansi] {slot}: {exc}") from None


@dataclass(frozen=True)
class ThemeInfo:
    id: str
    name: str
    mode: str
    description: str
    path: Path


def theme_path(root: Path, theme_id: str) -> Path:
    if not is_valid_name(theme_id):
        raise ThemeError(f"invalid theme name {theme_id!r}: {NAME_RULE}")
    return root / "themes" / theme_id / "theme.toml"


def load(root: Path, theme_id: str) -> Theme:
    path = theme_path(root, theme_id)

    if not path.is_file():
        known = ", ".join(info.id for info in discover(root)) or "none"
        raise ThemeError(f"unknown theme {theme_id!r} (available: {known})")

    return Theme(path)


def discover(root: Path) -> list[ThemeInfo]:
    """Every theme with a readable canonical file, sorted with built-ins first."""

    found: list[ThemeInfo] = []
    themes_dir = root / "themes"

    if not themes_dir.is_dir():
        return found

    for path in sorted(themes_dir.glob("*/theme.toml")):
        try:
            theme = Theme(path)
        except ThemeError:
            continue
        found.append(ThemeInfo(theme.id, theme.name, theme.mode, theme.description, path))

    order = {name: index for index, name in enumerate(BUILTIN_THEMES)}
    return sorted(found, key=lambda info: (order.get(info.id, len(order)), info.id))


BUILTIN_THEMES = ("optical", "shell", "ember", "glacier", "augment", "neon")
