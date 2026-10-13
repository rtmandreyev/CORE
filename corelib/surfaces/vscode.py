"""VS Code: one stable base extension, colours through settings.

* CORE's base extension (`core.core-base`) contributes two empty themes,
  "CORE Dark" and "CORE Light". It is installed once, through VS Code's own
  CLI (`code --install-extension`), and never copied or deleted by hand.
* A CORE theme is a handful of settings scoped to its base theme:
  `workbench.colorCustomizations["[CORE Dark]"]` and the token/semantic token
  equivalents, plus `workbench.colorTheme`. VS Code applies settings live, so
  switching or previewing a theme needs no reload; only the first install of
  the base extension does.
* CORE owns exactly those keys. Restore puts each one back (or removes it) and
  leaves every other byte of settings.json alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from corelib import effects, fileops, jsonc
from corelib.build import (
    VSCODE_BASE_THEMES, VSCODE_EXTENSION_ID, VSCODE_EXTENSION_VERSION, vscode_base_theme, vscode_vsix,
)
from corelib.surfaces.base import (
    ACTIVE, BLOCKED, NA, READY, REPAIRABLE, Integration, Status, Surface, SurfaceError, exists_any,
)
from corelib.theme import Theme

CUSTOMIZATION_KEYS = (
    "workbench.colorCustomizations",
    "editor.tokenColorCustomizations",
    "editor.semanticTokenColorCustomizations",
)
RELOAD_NOTE = "VS Code: run 'Developer: Reload Window' once so it loads the newly installed CORE base themes"


def owned_paths(theme: Theme | None = None) -> list[list[str]]:
    """Every settings path CORE owns: the theme selection and both base scopes."""

    scopes = [f"[{label}]" for label in VSCODE_BASE_THEMES.values()]
    return [["workbench.colorTheme"], *[[key, scope] for key in CUSTOMIZATION_KEYS for scope in scopes]]


class VSCode(Surface):
    key = "vscode"
    label = "VS Code"
    repairable = True

    def available(self, paths):
        if exists_any(paths.vscode_app, paths.vscode_settings.parent):
            return (True, "")
        return (False, "VS Code is not installed")

    def cli(self, paths) -> str | None:
        if paths.vscode_cli.exists():
            return str(paths.vscode_cli)
        return effects.current().which("code")

    def extension_state(self, paths) -> tuple[str, str]:
        """(installed|missing|broken, detail) from VS Code's own registry."""

        registry = paths.vscode_extensions_dir / "extensions.json"
        try:
            entries = json.loads(registry.read_text())
        except FileNotFoundError:
            entries = []
        except (json.JSONDecodeError, OSError):
            return ("broken", f"{registry} is unreadable")

        for entry in entries if isinstance(entries, list) else []:
            identifier = (entry.get("identifier") or {}).get("id", "")
            if identifier.lower() != VSCODE_EXTENSION_ID:
                continue
            folder = entry.get("relativeLocation") or Path(str((entry.get("location") or {}).get("path", ""))).name
            if not folder or not (paths.vscode_extensions_dir / folder).is_dir():
                return ("broken", f"registered but its folder {folder or '?'} is missing")
            if entry.get("version") != VSCODE_EXTENSION_VERSION:
                return ("broken", f"version {entry.get('version')} installed, {VSCODE_EXTENSION_VERSION} expected")
            return ("installed", folder)

        return ("missing", "not installed")

    def dangling(self, paths) -> list[str]:
        """Registry entries whose folders are gone (any extension)."""

        try:
            entries = json.loads((paths.vscode_extensions_dir / "extensions.json").read_text())
        except Exception:
            return []
        found = []
        for entry in entries if isinstance(entries, list) else []:
            folder = entry.get("relativeLocation")
            if folder and not (paths.vscode_extensions_dir / folder).is_dir():
                found.append((entry.get("identifier") or {}).get("id", folder))
        return found

    def settings_text(self, paths) -> str | None:
        return fileops.read_text(paths.vscode_settings)

    def wanted(self, ctx, theme) -> dict:
        return json.loads(ctx.built(theme).path("vscode_settings").read_text())

    def blocker(self, ctx) -> str:
        try:
            jsonc.load(self.settings_text(ctx.paths))
            if self.settings_text(ctx.paths):
                jsonc.parse(self.settings_text(ctx.paths))
        except jsonc.JsoncError as exc:
            return f"settings.json cannot be edited safely ({exc})"
        state, _ = self.extension_state(ctx.paths)
        if state != "installed" and self.cli(ctx.paths) is None:
            return "the CORE base extension is not installed and VS Code's `code` command was not found"
        return ""

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Status(NA, reason)
        blocked = self.blocker(ctx)
        if blocked:
            return Status(BLOCKED, blocked)
        warnings = []
        if jsonc.get_path(self.settings_text(ctx.paths), ["window.autoDetectColorScheme"]) is True:
            warnings.append("window.autoDetectColorScheme is on, so VS Code ignores workbench.colorTheme")
        state, detail = self.extension_state(ctx.paths)
        if state == "broken":
            return Status(REPAIRABLE, f"CORE base extension {detail}", warnings)
        if state == "missing":
            return Status(READY, "installs the CORE base extension (one window reload, once)", warnings)
        if self.verify(ctx, theme):
            current = jsonc.get_path(self.settings_text(ctx.paths), ["workbench.colorTheme"])
            return Status(READY, f"currently {current if isinstance(current, str) else 'default'}", warnings)
        return Status(ACTIVE, vscode_base_theme(theme), warnings)

    def capture(self, ctx, theme):
        blocked = self.blocker(ctx)
        if blocked:
            raise SurfaceError(blocked)
        text = self.settings_text(ctx.paths)
        prior = []
        for path in owned_paths(theme):
            value = jsonc.get_path(text, path)
            parent = jsonc.get_path(text, path[:1]) if len(path) > 1 else None
            prior.append({
                "path": path,
                "present": not jsonc.missing(value),
                "value": None if jsonc.missing(value) else value,
                "parent_present": len(path) == 1 or not jsonc.missing(parent),
            })
        return {"file_existed": text is not None, "keys": prior}

    def ensure(self, ctx, theme):
        state, detail = self.extension_state(ctx.paths)
        if state == "installed":
            return []
        cli = self.cli(ctx.paths)
        if cli is None:
            raise SurfaceError("VS Code's `code` command was not found; cannot install the CORE base extension")
        vsix = vscode_vsix(ctx.paths)
        result = effects.current().run([cli, "--install-extension", str(vsix), "--force"], timeout=120)
        if not result.ok:
            raise SurfaceError(f"`code --install-extension` failed: {result.text or result.returncode}")
        lines = ["installed the CORE base extension (core.core-base)", RELOAD_NOTE]
        # Re-select CORE's base theme if VS Code reset the selection while the
        # extension was missing (CORE's colour blocks prove it was CORE's).
        text = self.settings_text(ctx.paths)
        if theme is not None and jsonc.missing(jsonc.get_path(text, ["workbench.colorTheme"])):
            scope = f"[{vscode_base_theme(theme)}]"
            if not jsonc.missing(jsonc.get_path(text, ["workbench.colorCustomizations", scope])):
                fileops.write_text(ctx.paths.vscode_settings,
                                   jsonc.set_path(text, ["workbench.colorTheme"], vscode_base_theme(theme)))
                lines.append(f"workbench.colorTheme = {vscode_base_theme(theme)} again")
        return lines

    def apply(self, ctx, theme, captured):
        lines = self.ensure(ctx, theme)
        text = self.settings_text(ctx.paths)
        wanted = self.wanted(ctx, theme)
        scope = f"[{vscode_base_theme(theme)}]"
        updated = text
        for path in owned_paths(theme):
            if len(path) == 1:
                updated = jsonc.set_path(updated, path, wanted[path[0]])
            elif path[1] == scope and path[0] in wanted:
                updated = jsonc.set_path(updated, path, wanted[path[0]][scope])
            else:
                # The other base theme's block (or a key this theme does not
                # set) is CORE's stale state: remove it, and an emptied parent.
                updated = jsonc.delete_path(updated, path)
                if jsonc.get_path(updated, path[:1]) == {}:
                    updated = jsonc.delete_path(updated, path[:1])
        if updated != text:
            fileops.write_text(ctx.paths.vscode_settings, updated)
            lines.append(f"workbench.colorTheme = {vscode_base_theme(theme)} with {theme.short_name} colours (live)")
        else:
            lines.append("settings already current")
        return lines

    def verify(self, ctx, theme):
        problems = []
        text = self.settings_text(ctx.paths)
        wanted = self.wanted(ctx, theme)
        scope = f"[{vscode_base_theme(theme)}]"
        for path in owned_paths(theme):
            if len(path) > 1 and path[1] != scope:
                continue
            expected = wanted[path[0]] if len(path) == 1 else wanted.get(path[0], {}).get(scope)
            if expected is None:
                continue
            try:
                actual = jsonc.get_path(text, path)
            except jsonc.JsoncError as exc:
                return [f"settings.json is unreadable ({exc})"]
            if jsonc.missing(actual) or actual != expected:
                problems.append(f"{'.'.join(path)} does not match {theme.short_name}")
        state, detail = self.extension_state(ctx.paths)
        if state != "installed":
            problems.append(f"CORE base extension {detail}")
        return problems

    def restore(self, ctx, captured):
        text = self.settings_text(ctx.paths)
        if text is None:
            return ["settings.json is gone; nothing to restore"]
        updated = text
        for item in captured["keys"]:
            path = item["path"]
            if item["present"]:
                updated = jsonc.set_path(updated, path, item["value"])
            else:
                updated = jsonc.delete_path(updated, path)
                parent = jsonc.get_path(updated, path[:1]) if len(path) > 1 else None
                if len(path) > 1 and not item["parent_present"] and parent == {}:
                    updated = jsonc.delete_path(updated, path[:1])
        # A parent CORE emptied must exist again if it existed before.
        for item in captured["keys"]:
            path = item["path"]
            if len(path) > 1 and item["parent_present"] and jsonc.missing(jsonc.get_path(updated, path[:1])):
                updated = jsonc.set_path(updated, path[:1], {})
        if not captured["file_existed"] and jsonc.load(updated) == {}:
            ctx.paths.vscode_settings.unlink()
            return ["removed settings.json (CORE created it)"]
        if updated != text:
            fileops.write_text(ctx.paths.vscode_settings, updated)
            return ["CORE settings restored (live)"]
        return ["settings unchanged"]

    def integration(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Integration(NA, reason=reason)
        state, detail = self.extension_state(ctx.paths)
        text = self.settings_text(ctx.paths)
        try:
            selected = jsonc.get_path(text, ["workbench.colorTheme"])
            customized = [
                label for label in VSCODE_BASE_THEMES.values()
                if not jsonc.missing(jsonc.get_path(text, ["workbench.colorCustomizations", f"[{label}]"]))
            ]
            readable = "yes"
        except jsonc.JsoncError as exc:
            selected, customized, readable = None, [], f"no ({exc})"
        dangling = self.dangling(ctx.paths)
        facts = [
            ("installed", "yes" if ctx.paths.vscode_app.exists() else "settings only"),
            ("code command", self.cli(ctx.paths) or "not found"),
            ("CORE base extension", detail if state != "installed" else f"installed ({detail})"),
            ("selected theme", selected if isinstance(selected, str) else "default"),
            ("CORE colours present for", ", ".join(customized) or "none"),
            ("settings.json readable", readable),
            ("registry consistent", "yes" if not dangling else f"no — {len(dangling)} entr{'y' if len(dangling) == 1 else 'ies'} without a folder: {', '.join(dangling)}"),
        ]
        if readable != "yes":
            return Integration(BLOCKED, facts, reason="settings.json cannot be parsed")
        uses_core = isinstance(selected, str) and selected in VSCODE_BASE_THEMES.values()
        # VS Code removes `workbench.colorTheme` when the selected theme's
        # extension disappears. CORE's colour blocks still being there means
        # CORE's selection was reset, not changed by the user.
        reset = jsonc.missing(selected) if readable == "yes" else False
        reset = reset and bool(customized)
        if reset:
            facts.append(("selection", "reset by VS Code (CORE's base theme went missing)"))
        if state != "installed" and (uses_core or reset or state == "broken"):
            if self.cli(ctx.paths) is None:
                return Integration(BLOCKED, facts, reason="`code` command not found")
            plan = ["Install the CORE base extension with `code --install-extension`"]
            if reset:
                plan.append(f"Select {'/'.join(customized)} again (only CORE's own selection)")
            plan.append("Reload VS Code windows once")
            return Integration(REPAIRABLE, facts, plan, reason=f"base extension {detail}",
                               theme_id=theme.id if theme else "")
        if state != "installed":
            return Integration(READY, facts, reason="the base extension is installed on first apply")
        return Integration(ACTIVE if uses_core else READY, facts)
