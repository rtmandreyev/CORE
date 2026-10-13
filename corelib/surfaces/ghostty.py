"""Ghostty: a CORE-owned theme file plus one `theme =` directive.

* The theme file is a copy in ~/.config/ghostty/themes/<slug> (never a link
  into the repository).
* Ghostty loads ~/.config/ghostty/{config.ghostty,config} and then
  ~/Library/Application Support/com.mitchellh.ghostty/{config.ghostty,config};
  a later file overrides an earlier one and the last directive in a file wins.
  CORE writes its directive into the last file Ghostty loads, creating
  ~/.config/ghostty/config when none exists.
* `ghostty +show-config` reports the effective value, which is what verify
  trusts when Ghostty is installed.
* A running Ghostty is asked to reload through its AppleScript action API.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from corelib import effects, fileops
from corelib.surfaces.base import (
    ACTIVE, NA, READY, REPAIRABLE, Integration, Status, Surface, exists_any,
)
from corelib.theme import Theme, discover, load

THEME_LINE = re.compile(r"(?m)^[ \t]*theme[ \t]*=[ \t]*(.*?)[ \t]*$")
RELOAD_SCRIPT = (
    'tell application "Ghostty"\n'
    "  set target to focused terminal of selected tab of front window\n"
    '  return perform action "reload_config" on target\n'
    "end tell"
)


def last_theme(text: str | None) -> str | None:
    if not text:
        return None
    found = THEME_LINE.findall(text)
    return found[-1] if found else None


def set_last_theme(text: str, value: str) -> str:
    matches = list(THEME_LINE.finditer(text))
    if matches:
        last = matches[-1]
        return text[: last.start(1)] + value + text[last.end(1) :]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"theme = {value}\n"


def remove_last_theme(text: str) -> str:
    matches = list(THEME_LINE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    end = last.end()
    if text[end : end + 1] == "\n":
        end += 1
    return text[: last.start()] + text[end:]


class Ghostty(Surface):
    key = "ghostty"
    label = "Ghostty"
    repairable = True

    def available(self, paths):
        if exists_any(paths.ghostty_app, *paths.ghostty_configs):
            return (True, "")
        return (False, "Ghostty is not installed")

    def target(self, paths) -> Path:
        existing = [path for path in paths.ghostty_configs if path.is_file()]
        return existing[-1] if existing else paths.ghostty_configs[1]

    def theme_file(self, paths, theme: Theme) -> Path:
        return paths.ghostty_themes_dir / theme.slug

    def effective_theme(self, paths) -> str | None:
        if paths.ghostty_bin.exists():
            result = effects.current().run([str(paths.ghostty_bin), "+show-config"], timeout=15)
            if result.ok:
                value = last_theme(result.stdout)
                return value or None
        value = None
        for path in paths.ghostty_configs:
            found = last_theme(fileops.read_text(path))
            if found is not None:
                value = found
        return value

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Status(NA, reason)
        integration = self.integration(ctx, theme)
        if integration.state == REPAIRABLE:
            return Status(REPAIRABLE, integration.reason)
        if self.verify(ctx, theme):
            current = self.effective_theme(ctx.paths)
            return Status(READY, f"currently {current or 'default'}")
        return Status(ACTIVE, theme.slug)

    def capture(self, ctx, theme):
        target = self.target(ctx.paths)
        theme_path = self.theme_file(ctx.paths, theme)
        return {
            "config_path": str(target),
            "config": fileops.snapshot(target),
            "theme_path": str(theme_path),
            "theme_file": fileops.snapshot(theme_path),
            "slug": theme.slug,
        }

    def legacy_links(self, paths) -> list[Path]:
        """Theme files an older CORE linked into its repository (they break when it moves)."""

        directory = paths.ghostty_themes_dir
        if not directory.is_dir():
            return []
        return [path for path in sorted(directory.iterdir())
                if path.is_symlink() and "/generated/ghostty/" in os.readlink(path)]

    def ensure(self, ctx, theme):
        lines = []
        source = ctx.built(theme).path("ghostty").read_text()
        path = self.theme_file(ctx.paths, theme)
        if path.is_symlink():
            path.unlink()
        if fileops.read_text(path) != source:
            fileops.write_text(path, source)
            lines.append(f"installed theme file {path.name}")
        # Migrate links an older CORE made into its repository: copy the
        # current build of that theme in place of the link.
        slugs = {}
        for info in discover(ctx.paths.root):
            try:
                slugs[load(ctx.paths.root, info.id).slug] = info.id
            except Exception:
                continue
        for link in [] if ctx.preview else self.legacy_links(ctx.paths):  # a preview changes nothing permanently
            if link.name in slugs:
                text = ctx.built(load(ctx.paths.root, slugs[link.name])).path("ghostty").read_text()
                link.unlink()
                fileops.write_text(link, text)
                lines.append(f"replaced repository link {link.name} with a copy")
        return lines

    def apply(self, ctx, theme, captured):
        lines = self.ensure(ctx, theme)
        target = Path(captured["config_path"])
        text = fileops.read_text(target) or ""
        updated = set_last_theme(text, theme.slug)
        if updated != text or not target.exists():
            fileops.write_text(target, updated)
            lines.append(f"theme = {theme.slug} in {target}")
        lines.append(reload(ctx.paths))
        return lines

    def verify(self, ctx, theme):
        problems = []
        built = ctx.built(theme).path("ghostty").read_text()
        if fileops.read_text(self.theme_file(ctx.paths, theme)) != built:
            problems.append(f"theme file {theme.slug} is missing or out of date")
        effective = self.effective_theme(ctx.paths)
        if effective != theme.slug:
            problems.append(f"effective theme is {effective or 'default'}, expected {theme.slug}")
        return problems

    def restore(self, ctx, captured):
        lines = []
        target = Path(captured["config_path"])
        before = captured["config"]
        prior = last_theme(before.get("text")) if before.get("kind") == "file" else None
        text = fileops.read_text(target)

        if text is not None:
            if prior is not None:
                restored = set_last_theme(text, prior)
            elif last_theme(text) == captured["slug"]:
                restored = remove_last_theme(text)
                prior = "the value from Ghostty's other config files"
            else:
                restored = text
            if before.get("kind") == "absent" and not restored.strip():
                target.unlink()
                lines.append(f"removed {target} (CORE created it)")
            elif restored != text:
                fileops.write_text(target, restored)
                lines.append(f"theme restored to {prior}")

        result = fileops.restore_snapshot(Path(captured["theme_path"]), captured["theme_file"])
        if result != "unchanged":
            lines.append(f"theme file {result}")
        lines.append(reload(ctx.paths))
        return lines

    def integration(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Integration(NA, reason=reason)
        effective = self.effective_theme(ctx.paths)
        facts = [("installed", "yes" if ctx.paths.ghostty_app.exists() else "config only"),
                 ("config", str(self.target(ctx.paths))),
                 ("effective theme", effective or "default")]
        legacy = self.legacy_links(ctx.paths)
        if legacy:
            facts.append(("repository links", ", ".join(link.name for link in legacy) + " (migrated on apply/repair)"))
        if theme is None or effective != theme.slug:
            return Integration(READY, facts)
        path = self.theme_file(ctx.paths, theme)
        if fileops.read_text(path) != ctx.built(theme).path("ghostty").read_text():
            facts.append(("theme file", "missing" if not path.exists() else "out of date"))
            return Integration(REPAIRABLE, facts, [f"Write {path} from the current {theme.short_name} build"],
                               reason=f"theme file {path.name} is missing or out of date")
        facts.append(("theme file", "current"))
        return Integration(ACTIVE, facts)


def reload(paths) -> str:
    """Ask a running Ghostty to re-read its configuration. Never raises."""

    try:
        running = effects.process_running(paths.ghostty_bin)
        if not running:
            return "Ghostty is not running; it reads the theme when it starts"
        result = effects.osascript(RELOAD_SCRIPT)
    except effects.EffectBlocked:
        raise
    except Exception as exc:  # a reload failure never changes the outcome
        return f"reload unavailable ({exc}); press Cmd+Shift+, in Ghostty"
    if result.ok and result.stdout.strip().lower() == "true":
        return "Ghostty reloaded"
    reason = "macOS blocked automation of Ghostty" if "-1743" in result.stderr else (result.text or "no confirmation")
    return f"Ghostty did not reload ({reason}); press Cmd+Shift+, in Ghostty"
