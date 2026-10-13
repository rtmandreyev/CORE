"""Compile one canonical theme into every application's format.

    themes/<id>/theme.toml  +  corelib/templates/*  ->  build/<id>/**

Build output is deterministic, repository-local and never committed. Nothing
here touches an application: surfaces copy from `build/` when they apply.
"""

from __future__ import annotations

import io
import json
import plistlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from corelib import render as render_module
from corelib.paths import Paths
from corelib.theme import Theme, ThemeError

VSCODE_EXTENSION_ID = "core.core-base"
VSCODE_EXTENSION_VERSION = "0.1.0"
VSCODE_BASE_THEMES = {"dark": "CORE Dark", "light": "CORE Light"}
CHATGPT_PREFIX = "codex-theme-v1:"

TERMINAL_COLOR_KEYS = {
    "BackgroundColor": ("terminal", "background"),
    "TextColor": ("terminal", "foreground"),
    "TextBoldColor": ("terminal", "foreground"),
    "CursorColor": ("terminal", "cursor"),
    "SelectionColor": ("terminal", "selection_background"),
}
TERMINAL_ANSI_KEYS = (
    "ANSIBlackColor", "ANSIRedColor", "ANSIGreenColor", "ANSIYellowColor",
    "ANSIBlueColor", "ANSIMagentaColor", "ANSICyanColor", "ANSIWhiteColor",
    "ANSIBrightBlackColor", "ANSIBrightRedColor", "ANSIBrightGreenColor", "ANSIBrightYellowColor",
    "ANSIBrightBlueColor", "ANSIBrightMagentaColor", "ANSIBrightCyanColor", "ANSIBrightWhiteColor",
)


class BuildError(Exception):
    pass


@dataclass(frozen=True)
class Built:
    theme: Theme
    root: Path

    def path(self, key: str) -> Path:
        return self.root / OUTPUTS[key](self.theme)


OUTPUTS = {
    "ghostty": lambda t: f"ghostty/{t.slug}",
    "starship": lambda t: "starship/starship.toml",
    "fastfetch": lambda t: "fastfetch/config.jsonc",
    "hermes": lambda t: f"hermes/{t.slug}.yaml",
    "obsidian_css": lambda t: "obsidian/theme.css",
    "obsidian_manifest": lambda t: "obsidian/manifest.json",
    "vscode_theme": lambda t: "vscode/theme.json",
    "vscode_settings": lambda t: "vscode/settings.json",
    "terminal": lambda t: f"terminal/{terminal_profile_name(t)}.terminal",
    "chatgpt": lambda t: "chatgpt/import.txt",
}

TEMPLATES = (
    ("ghostty", "ghostty.tmpl", "ghostty"),
    ("starship", "starship.toml.tmpl", "starship"),
    ("fastfetch", "fastfetch.jsonc.tmpl", "fastfetch"),
    ("hermes", "hermes.skin.yaml.tmpl", "hermes"),
    ("obsidian_css", "obsidian.css.tmpl", "obsidian"),
    ("obsidian_manifest", "obsidian.manifest.json.tmpl", "obsidian"),
    ("vscode_theme", "vscode.theme.json.tmpl", "vscode"),
)


def build(paths: Paths, theme: Theme) -> Built:
    out = Built(theme, paths.build_dir / theme.id)
    texts: dict[str, str] = {}

    for key, template, app in TEMPLATES:
        try:
            texts[key] = render_module.render(template, theme, app=app)
        except ThemeError as exc:
            raise BuildError(f"{theme.id}: {template}: {exc}") from None

    try:
        vscode_theme = json.loads(texts["vscode_theme"])
    except json.JSONDecodeError as exc:
        raise BuildError(f"{theme.id}: generated VS Code theme is not JSON ({exc})") from None

    texts["vscode_settings"] = json.dumps(vscode_settings(theme, vscode_theme), indent=2) + "\n"
    texts["terminal"] = plistlib.dumps(terminal_profile(theme), fmt=plistlib.FMT_XML).decode()
    texts["chatgpt"] = chatgpt_import_string(theme) + "\n"

    for key, text in texts.items():
        target = out.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text() != text:
            target.write_text(text)

    return out


def ensure_built(paths: Paths, theme: Theme) -> Built:
    """Build when any output is missing or older than the theme or templates."""

    out = Built(theme, paths.build_dir / theme.id)
    sources = [theme.path, *render_module.TEMPLATES_DIR.glob("*")]
    newest = max(path.stat().st_mtime for path in sources if path.is_file())

    for key in OUTPUTS:
        target = out.path(key)
        if not target.is_file() or target.stat().st_mtime < newest:
            return build(paths, theme)

    return out


# ── VS Code: one stable base extension, colours delivered through settings ──


def vscode_base_theme(theme: Theme) -> str:
    return VSCODE_BASE_THEMES[theme.mode]


def vscode_settings(theme: Theme, compiled: dict) -> dict:
    """The settings CORE owns for a theme, scoped to its base theme label.

    VS Code applies these keys live. Scoping them to `[CORE Dark]` /
    `[CORE Light]` means they never colour a theme the user picks themselves.
    """

    scope = f"[{vscode_base_theme(theme)}]"
    settings = {
        "workbench.colorTheme": vscode_base_theme(theme),
        "workbench.colorCustomizations": {scope: compiled.get("colors", {})},
        "editor.tokenColorCustomizations": {scope: {"textMateRules": compiled.get("tokenColors", [])}},
    }

    semantic = compiled.get("semanticTokenColors")
    if semantic:
        settings["editor.semanticTokenColorCustomizations"] = {scope: {"enabled": True, "rules": semantic}}

    return settings


def vscode_extension_files() -> dict[str, str]:
    """The base extension's files: two empty base themes CORE colours via settings."""

    themes = []
    files: dict[str, str] = {}

    for mode, label in VSCODE_BASE_THEMES.items():
        filename = f"themes/core-{mode}-color-theme.json"
        themes.append({"label": label, "uiTheme": "vs-dark" if mode == "dark" else "vs", "path": f"./{filename}"})
        files[filename] = json.dumps(
            {"name": label, "type": mode, "semanticHighlighting": True, "colors": {}, "tokenColors": []},
            indent=2,
        ) + "\n"

    package = {
        "name": VSCODE_EXTENSION_ID.split(".", 1)[1],
        "displayName": "CORE",
        "description": "Base themes for CORE. Colours come from your CORE theme through settings.",
        "version": VSCODE_EXTENSION_VERSION,
        "publisher": VSCODE_EXTENSION_ID.split(".", 1)[0],
        "engines": {"vscode": "^1.70.0"},
        "categories": ["Themes"],
        "contributes": {"themes": themes},
    }
    files["package.json"] = json.dumps(package, indent=2) + "\n"
    return files


_VSIX_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>CORE</DisplayName>
    <Description xml:space="preserve">Base themes for CORE.</Description>
    <Categories>Themes</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.70.0" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>
"""

_CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension=".json" ContentType="application/json" /><Default Extension=".vsixmanifest" ContentType="text/xml" /></Types>
"""


def vscode_vsix(paths: Paths) -> Path:
    """Write the base extension as a deterministic .vsix for `code --install-extension`."""

    publisher, name = VSCODE_EXTENSION_ID.split(".", 1)
    target = paths.build_dir / "_vscode" / f"{VSCODE_EXTENSION_ID}-{VSCODE_EXTENSION_VERSION}.vsix"
    buffer = io.BytesIO()
    entries = {
        "extension.vsixmanifest": _VSIX_MANIFEST.format(name=name, version=VSCODE_EXTENSION_VERSION, publisher=publisher),
        "[Content_Types].xml": _CONTENT_TYPES,
        **{f"extension/{path}": text for path, text in vscode_extension_files().items()},
    }

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[path])

    data = buffer.getvalue()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_bytes() != data:
        target.write_bytes(data)
    return target


# ── macOS Terminal ──


def terminal_profile_name(theme: Theme) -> str:
    return f"CORE {theme.id.upper()}"


def archived_color(hex_color: str) -> bytes:
    """An NSKeyedArchiver NSColor in sRGB, the form Terminal stores today.

    The components are tagged with the sRGB colour space (NSID 7). An untagged
    "calibrated RGB" colour is converted by Terminal and renders visibly off
    (OPTICAL's #B9E7F2 text showed as #C5ECF5 on a real Mac).
    """

    red, green, blue = (int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    components = f"{red:.10g} {green:.10g} {blue:.10g}"
    archive = {
        "$archiver": "NSKeyedArchiver",
        "$version": 100000,
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            {
                "NSColorSpace": 1,
                "NSComponents": f"{components} 1".encode(),
                "NSRGB": components.encode() + b"\x00",
                "NSCustomColorSpace": plistlib.UID(2),
                "$class": plistlib.UID(3),
            },
            {"NSID": 7, "$class": plistlib.UID(4)},
            {"$classname": "NSColor", "$classes": ["NSColor", "NSObject"]},
            {"$classname": "NSColorSpace", "$classes": ["NSColorSpace", "NSObject"]},
        ],
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def terminal_profile(theme: Theme) -> dict:
    profile: dict = {
        "name": terminal_profile_name(theme),
        "type": "Window Settings",
        "ProfileCurrentVersion": 2.09,
        "UseBrightBold": bool(theme.app("terminal").get("bright_bold", False)),
    }

    for key, (section, name) in TERMINAL_COLOR_KEYS.items():
        profile[key] = archived_color(theme.color(section, name))

    for key, color in zip(TERMINAL_ANSI_KEYS, theme.ansi()):
        profile[key] = archived_color(color)

    return profile


# ── ChatGPT (Codex app) theme import string ──


def chatgpt_theme(theme: Theme) -> dict:
    app = theme.app("chatgpt")

    def color(key: str) -> str:
        value = theme.resolve_or_literal(str(app.get(key, "")))
        if not value.startswith("#") or len(value) != 7:
            raise BuildError(f"{theme.id}: [apps.chatgpt] {key} must resolve to #RRGGBB, got {value!r}")
        return value.lower()

    try:
        contrast = int(app.get("contrast", 60))
    except (TypeError, ValueError):
        raise BuildError(f"{theme.id}: [apps.chatgpt] contrast must be an integer 0-100") from None

    if not 0 <= contrast <= 100:
        raise BuildError(f"{theme.id}: [apps.chatgpt] contrast must be 0-100")

    return {
        "codeThemeId": str(app.get("code_theme") or "codex"),
        "theme": {
            "accent": color("accent"),
            "accentSource": "custom",
            "contrast": contrast,
            "fonts": {"code": app.get("font_code") or None, "ui": app.get("font_ui") or None},
            "ink": color("ink"),
            "opaqueWindows": bool(app.get("opaque_windows", False)),
            "semanticColors": {
                "diffAdded": color("diff_added"),
                "diffRemoved": color("diff_removed"),
                "skill": color("skill"),
            },
            "surface": color("surface"),
        },
        "variant": theme.mode,
    }


def chatgpt_import_string(theme: Theme) -> str:
    return CHATGPT_PREFIX + json.dumps(chatgpt_theme(theme), separators=(",", ":"), sort_keys=True)
