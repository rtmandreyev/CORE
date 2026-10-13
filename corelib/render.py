"""Shared-template rendering.

A template says HOW an application consumes the theme; the theme says WHAT the
roles look like. Template syntax:

    {token}                       a value from the theme (see `scope`)
    {token.rgb} / {token.rgb_csv} "R;G;B" / "R, G, B" forms of a colour token
    {{#light}}...{{/light}}       emitted only for a light theme
    {{#dark}}...{{/dark}}         emitted only for a dark theme
    {{#if token}}...{{/if}}       emitted only when `token` is non-empty

Shared templates use fully qualified tokens (`{terminal.ansi.red}`,
`{apps.obsidian.canvas}`); bare names are kept for compatibility and resolve
with application mappings taking precedence.
"""

from __future__ import annotations

import re
from pathlib import Path

from corelib.theme import Theme, ThemeError

TOKEN_RE = re.compile(r"(?<!\$)\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")
MODE_BLOCK_RE = re.compile(r"\{\{#(light|dark)\}\}(.*?)\{\{/\1\}\}", re.S)
IF_BLOCK_RE = re.compile(r"\{\{#(if|unless) ([a-zA-Z_][a-zA-Z0-9_.]*)\}\}(.*?)\{\{/\1\}\}", re.S)
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_FLAT_SECTIONS = ("palette", "roles", "prompt", "terminal")


def scope(theme: Theme, app: str | None = None, literals: dict[str, str] | None = None) -> dict[str, str]:
    values: dict[str, str] = {
        "theme_id": theme.id,
        "theme_name": theme.name,
        "theme_short_name": theme.short_name,
        "theme_short": theme.short_name,
        "theme_description": theme.description,
        "theme_author": theme.author,
        "theme_brand": theme.brand,
        "mode": theme.mode,
        "slug": theme.slug,
        "obsidian_theme": theme.obsidian_name,
    }

    def add(name: str, value: object) -> None:
        if isinstance(value, bool):
            values[name] = "true" if value else "false"
            return
        if isinstance(value, (int, float)):
            values[name] = str(value)
            return
        if not isinstance(value, str):
            return
        try:
            values[name] = theme.resolve(value)
        except ThemeError:
            values[name] = value

    for section in _FLAT_SECTIONS:
        table = theme.raw.get(section, {})
        if not isinstance(table, dict):
            continue
        for key, value in table.items():
            add(key, value)
            if key in values:
                values[f"{section}.{key}"] = values[key]

    for section, table in theme.raw.items():
        if not isinstance(table, dict):
            continue
        for key, nested in table.items():
            if not isinstance(nested, dict):
                continue
            for nested_key, nested_value in nested.items():
                add(nested_key, nested_value)
                values[f"{section}.{key}.{nested_key}"] = values.get(nested_key, "")

    for key, value in (literals or {}).items():
        values[key] = value

    if app:
        _add_app(values, theme, app, add)

    # Every app table is addressable by its qualified name from any template.
    for other in theme.raw.get("apps", {}):
        if other != app:
            qualified: dict[str, str] = {}
            _add_app(qualified, theme, other, lambda k, v: _store(qualified, theme, k, v))
            values.update({k: v for k, v in qualified.items() if k.startswith("apps.")})

    for name, value in list(values.items()):
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
            parts = [str(int(value[index : index + 2], 16)) for index in (1, 3, 5)]
            values[f"{name}.rgb"] = ";".join(parts)
            values[f"{name}.rgb_csv"] = ", ".join(parts)

    return values


def _store(target: dict[str, str], theme: Theme, name: str, value: object) -> None:
    if isinstance(value, bool):
        target[name] = "true" if value else "false"
    elif isinstance(value, (int, float)):
        target[name] = str(value)
    elif isinstance(value, str):
        target[name] = theme.resolve_or_literal(value)


def _add_app(values: dict[str, str], theme: Theme, app: str, add) -> None:
    """Add `[apps.<app>]`, letting a key name another key of the same table."""

    table = theme.app(app)

    for key, value in table.items():
        add(key, value)
        values[f"apps.{app}.{key}"] = values.get(key, "")

    for _ in range(4):  # references may chain a few levels deep
        for key, value in table.items():
            current = values.get(f"apps.{app}.{key}", "")
            if isinstance(value, str) and current == value and value in table and value != key:
                resolved = values.get(f"apps.{app}.{value}", "")
                if resolved and resolved != value:
                    values[f"apps.{app}.{key}"] = resolved
                    values[key] = resolved


def render_text(text: str, theme: Theme, *, app: str | None = None, literals: dict[str, str] | None = None) -> str:
    tokens = scope(theme, app, literals)

    text = MODE_BLOCK_RE.sub(lambda m: m.group(2) if m.group(1) == theme.mode else "", text)
    text = IF_BLOCK_RE.sub(
        lambda m: m.group(3) if bool(tokens.get(m.group(2))) == (m.group(1) == "if") else "", text
    )

    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token in tokens:
            return tokens[token]
        raise ThemeError(f"unknown template token {{{token}}}")

    return TOKEN_RE.sub(replace, text)


def render(template: str, theme: Theme, *, app: str | None = None, literals: dict[str, str] | None = None) -> str:
    return render_text((TEMPLATES_DIR / template).read_text(), theme, app=app, literals=literals)
