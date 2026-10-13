"""Hermes (CLI and Desktop) through upstream skin facilities only.

* CORE installs one skin file, ~/.hermes/skins/<slug>.yaml, and selects it
  with `display.skin` in ~/.hermes/config.yaml. Nothing else in Hermes is read
  or written.
* The skin carries `customCSS`, which Hermes Desktop injects as an unlayered
  <style> element. That is how text selection (and anything else CSS can
  reach) follows the theme — no source patch, no rebuild, no re-signing.
* Hermes watches `display.skin` and the active skin file, so a running Desktop
  repaints by itself. CORE sends nothing to the application.
* Hermes updates may delete or replace files under ~/.hermes. CORE detects the
  current state every time and repairs only its own skin file.
"""

from __future__ import annotations

import re
from pathlib import Path

from corelib import fileops
from corelib.surfaces.base import (
    ACTIVE, BLOCKED, NA, READY, REPAIRABLE, Integration, Status, Surface, SurfaceError,
)
from corelib.theme import Theme, discover, load

CUSTOM_CSS_MARKER = b"hermes-desktop-custom-css"
DISPLAY_RE = re.compile(r"(?m)^display:[ \t]*(?:#.*)?$")
SKIN_RE = re.compile(r"(?m)^([ \t]+)skin:[ \t]*(\S[^#\n]*?)?[ \t]*(#.*)?$")


def display_skin(text: str | None) -> str | None:
    """The value of `display.skin`, or None when it is not set."""

    if not text:
        return None
    block = _display_block(text)
    if block is None:
        return None
    match = SKIN_RE.search(text, block[0], block[1])
    if not match or match.group(2) is None:
        return None
    return match.group(2).strip().strip("'\"") or None


def _display_block(text: str) -> tuple[int, int] | None:
    match = DISPLAY_RE.search(text)
    if not match:
        return None
    start = match.end()
    following = re.compile(r"(?m)^\S").search(text, start + 1)
    return (start, following.start() if following else len(text))


def set_display_skin(text: str | None, value: str | None) -> str:
    """Set (or with None remove) `display.skin`, touching only that line."""

    text = text or ""
    block = _display_block(text)
    match = SKIN_RE.search(text, block[0], block[1]) if block else None

    if value is None:
        if not match:
            return text
        end = match.end() + (1 if text[match.end() : match.end() + 1] == "\n" else 0)
        return text[: match.start()] + text[end:]

    if match:
        start = match.start(2) if match.group(2) is not None else match.end(1) + len("skin:")
        end = match.end(2) if match.group(2) is not None else start
        prefix = "" if match.group(2) is not None else " "
        return text[:start] + prefix + value + text[end:]

    if block:
        indent = re.match(r"(?m)^([ \t]+)\S", text[block[0] + 1 : block[1]] or "")
        return text[: block[0]] + f"\n{indent.group(1) if indent else '  '}skin: {value}" + text[block[0] :]

    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"display:\n  skin: {value}\n"


class Hermes(Surface):
    key = "hermes"
    label = "Hermes"
    repairable = True

    def available(self, paths):
        if paths.hermes_config.is_file() or paths.hermes_app.exists():
            return (True, "")
        return (False, "Hermes is not installed")

    def skin_path(self, paths, theme: Theme) -> Path:
        return paths.hermes_skins_dir / f"{theme.slug}.yaml"

    def desktop_custom_css(self, paths) -> bool | None:
        """None without Hermes Desktop; otherwise whether it supports customCSS."""

        app = paths.hermes_app
        if not app.exists():
            return None
        resources = app / "Contents/Resources"
        candidates = [resources / "app.asar", *sorted((resources / "app.asar.unpacked").rglob("*.js"))]
        for candidate in candidates:
            try:
                if candidate.is_file() and CUSTOM_CSS_MARKER in candidate.read_bytes():
                    return True
            except OSError:
                continue
        return False

    def wanted(self, ctx, theme) -> str:
        return ctx.built(theme).path("hermes").read_text()

    def status(self, ctx, theme):
        integration = self.integration(ctx, theme)
        if integration.state == NA:
            return Status(NA, integration.reason)
        if integration.state == BLOCKED:  # a vanished theme's skin: applying replaces it
            return Status(READY, integration.reason)
        warnings = []
        if self.desktop_custom_css(ctx.paths) is False:
            warnings.append(
                "this Hermes Desktop predates skin customCSS: colours apply, text selection stays Hermes' own; update Hermes"
            )
        if not self.verify(ctx, theme):
            return Status(ACTIVE, theme.slug, warnings)
        if integration.state == REPAIRABLE:
            return Status(REPAIRABLE, integration.reason, warnings)
        current = display_skin(fileops.read_text(ctx.paths.hermes_config))
        return Status(READY, f"currently {current or 'default'}", warnings)

    def capture(self, ctx, theme):
        if not ctx.paths.hermes_config.is_file():
            raise SurfaceError(f"Hermes is installed but {ctx.paths.hermes_config} does not exist yet; run Hermes once")
        text = fileops.read_text(ctx.paths.hermes_config)
        skin = self.skin_path(ctx.paths, theme)
        return {"skin": display_skin(text), "skin_path": str(skin), "skin_file": fileops.snapshot(skin)}

    def ensure(self, ctx, theme):
        path = self.skin_path(ctx.paths, theme)
        if fileops.read_text(path) == self.wanted(ctx, theme) and not path.is_symlink():
            return []
        if path.is_symlink():
            path.unlink()
        fileops.write_text(path, self.wanted(ctx, theme))
        return [f"installed CORE skin {path.name} (with customCSS)"]

    def apply(self, ctx, theme, captured):
        lines = self.ensure(ctx, theme)
        text = fileops.read_text(ctx.paths.hermes_config) or ""
        updated = set_display_skin(text, theme.slug)
        if updated != text:
            fileops.write_text(ctx.paths.hermes_config, updated)
            lines.append(f"display.skin = {theme.slug}")
        lines.append("Hermes picks up the skin by itself (CLI on next start, Desktop live)")
        return lines

    def verify(self, ctx, theme):
        problems = []
        path = self.skin_path(ctx.paths, theme)
        if fileops.read_text(path) != self.wanted(ctx, theme):
            problems.append(f"skin {path.name} is missing or out of date")
        if display_skin(fileops.read_text(ctx.paths.hermes_config)) != theme.slug:
            problems.append(f"display.skin is not {theme.slug}")
        return problems

    def restore(self, ctx, captured):
        lines = []
        text = fileops.read_text(ctx.paths.hermes_config)
        if text is not None:
            restored = set_display_skin(text, captured["skin"])
            if restored != text:
                fileops.write_text(ctx.paths.hermes_config, restored)
                lines.append(f"display.skin = {captured['skin'] or '(unset)'}")
        result = fileops.restore_snapshot(Path(captured["skin_path"]), captured["skin_file"])
        if result != "unchanged":
            lines.append(f"skin file {result}")
        return lines or ["unchanged"]

    def integration(self, ctx, theme):
        paths = ctx.paths
        installed, reason = self.available(paths)
        if not installed:
            return Integration(NA, reason=reason)

        desktop = self.desktop_custom_css(paths)
        text = fileops.read_text(paths.hermes_config)
        selected = display_skin(text)
        facts = [
            ("Hermes", "installed" if paths.hermes_config.is_file() else "app only, not set up"),
            ("Desktop", "not installed" if desktop is None else "installed"),
        ]
        if desktop is not None:
            facts.append(("customCSS", "supported" if desktop else "not supported by this version"))
        facts.append(("selected skin", selected or "default"))

        if not paths.hermes_config.is_file():
            return Integration(NA, facts, reason="Hermes is installed but not set up yet (start it once)")

        # Which CORE theme does the configuration point at, if any?
        slugs = {}
        for info in discover(paths.root):
            try:
                slugs[load(paths.root, info.id).slug] = info.id
            except Exception:
                continue
        target = theme
        if selected in slugs and (theme is None or selected != theme.slug):
            target = load(paths.root, slugs[selected])
        elif selected and selected.startswith(("core-", "node-")) and selected not in slugs:
            facts.append(("CORE skin", f"{selected} belongs to a theme that no longer exists"))
            return Integration(BLOCKED, facts, reason=f"display.skin names {selected}, whose CORE theme no longer exists; apply another theme")

        if target is None:
            return Integration(READY, facts)

        path = self.skin_path(paths, target)
        current = fileops.read_text(path)
        wanted = self.wanted(ctx, target)
        if current is None:
            skin_state, problem = "missing", f"CORE skin {path.name} is missing"
        elif not re.search(r"(?m)^name:\s*\S", current) or not re.search(r"(?m)^colors:", current):
            skin_state, problem = "malformed", f"CORE skin {path.name} is malformed"
        elif current != wanted:
            skin_state, problem = "stale", f"CORE skin {path.name} is out of date"
        else:
            skin_state, problem = "current", ""
        facts.append(("CORE skin", f"{path.name} ({skin_state})"))

        if problem and selected == target.slug:
            plan = [
                f"Generate the current {target.short_name} Hermes skin",
                f"Install CORE-owned skin {path}",
                "Preserve every other Hermes setting and skin",
                f"Leave display.skin as it is ({selected or 'default'})" if selected != target.slug else f"Keep display.skin = {target.slug}",
                "Verify the skin contents and its customCSS",
                "Running Hermes Desktop reloads the skin by itself",
            ]
            return Integration(REPAIRABLE, facts, plan, reason=problem, theme_id=target.id)

        state = ACTIVE if selected == target.slug and skin_state == "current" else READY
        return Integration(state, facts, theme_id=target.id)
