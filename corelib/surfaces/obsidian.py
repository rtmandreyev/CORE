"""Obsidian: every registered, initialized vault.

Per vault CORE owns `.obsidian/themes/<CORE - NAME>/{manifest.json,theme.css}`
and three keys of `.obsidian/appearance.json`: `cssTheme`, `accentColor` and
the base appearance `theme` (moonstone = light, obsidian = dark).

appearance.json belongs to Obsidian, which rewrites it from memory, so CORE's
restore and verify are semantic: the three keys, not the bytes.

A running Obsidian is driven through its official CLI, and only for vaults
that already have a window open (the registry's `open` flag): the CLI opens a
window for any vault it is pointed at, so a closed vault is never targeted.
Vaults are addressed by their registry ID, never by folder name — several
vaults can share a name. Obsidian reloads appearance.json on a ~500 ms
debounce and saves its own copy ~1 s after a change; CORE waits for both
before asking the running app which theme it shows.
"""

from __future__ import annotations

import json
from pathlib import Path

from corelib import effects, fileops
from corelib.surfaces.base import (
    ACTIVE, BLOCKED, NA, READY, REPAIRABLE, Integration, Status, Surface, SurfaceError,
)
from corelib.theme import Theme

BASE_MODES = {"light": "moonstone", "dark": "obsidian"}
OWNED_KEYS = ("cssTheme", "accentColor", "theme")
WATCH_DELAY = 0.8  # Obsidian re-reads appearance.json 500 ms after a change
SAVE_DELAY = 1.3  # and writes its in-memory config 1 s after its own change


def registry(paths) -> list[dict]:
    """Registered vaults: id, path, open, exists, initialized."""

    try:
        data = json.loads(paths.obsidian_registry.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    vaults = []
    for vault_id, entry in (data.get("vaults") or {}).items():
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        path = Path(entry["path"]).expanduser()
        vaults.append({
            "id": vault_id,
            "path": path,
            "open": bool(entry.get("open")),
            "exists": path.is_dir(),
            "initialized": (path / ".obsidian").is_dir(),
        })
    return vaults


def labels(vaults: list[dict]) -> dict[str, str]:
    """Readable, unambiguous names: `docs` becomes `Work/docs` when shared."""

    names = [vault["path"].name for vault in vaults]
    out = {}
    for vault in vaults:
        name = vault["path"].name
        out[vault["id"]] = f"{vault['path'].parent.name}/{name}" if names.count(name) > 1 else name
    return out


def appearance(vault: Path) -> dict:
    try:
        data = json.loads((vault / ".obsidian/appearance.json").read_text())
    except (FileNotFoundError, OSError):
        return {}
    except json.JSONDecodeError as exc:
        raise SurfaceError(f"{vault}/.obsidian/appearance.json is not valid JSON ({exc})") from None
    return data if isinstance(data, dict) else {}


class Obsidian(Surface):
    key = "obsidian"
    label = "Obsidian"
    repairable = True

    def available(self, paths):
        if not (paths.obsidian_app.exists() or paths.obsidian_registry.exists()):
            return (False, "Obsidian is not installed")
        if not self.vaults(paths):
            return (False, "no initialized Obsidian vault is registered")
        return (True, "")

    def vaults(self, paths) -> list[dict]:
        return [vault for vault in registry(paths) if vault["exists"] and vault["initialized"]]

    # ── the official CLI ──

    def cli(self, paths) -> str | None:
        if paths.obsidian_cli.exists():
            return str(paths.obsidian_cli)
        return effects.current().which("obsidian")

    def cli_state(self, paths) -> tuple[str, str]:
        """(ready|disabled|not-running|absent, explanation)."""

        cli = self.cli(paths)
        if cli is None:
            return ("absent", "Obsidian's command line interface is not installed")
        if not (paths.home / ".obsidian-cli.sock").exists():
            return ("not-running", "Obsidian is not running")
        result = effects.current().run([cli, "version"], cwd=paths.home, timeout=10)
        text = result.text.lower()
        if "not enabled" in text:
            return ("disabled", "Obsidian's CLI is off (Settings → General → Advanced → Command line interface)")
        if "unable to find obsidian" in text:
            return ("not-running", "Obsidian is not running")
        if not result.ok and "vault not found" not in text:
            return ("disabled", f"the Obsidian CLI did not answer ({result.text or result.returncode})")
        return ("ready", "")

    def cli_run(self, paths, vault_id: str, *args: str):
        return effects.current().run([self.cli(paths), f"vault={vault_id}", *args], cwd=paths.home, timeout=10)

    def live_theme(self, paths, vault_id: str) -> str | None:
        result = self.cli_run(paths, vault_id, "theme")
        if not result.ok or "error" in result.text.lower():
            return None
        value = result.stdout.strip()
        return "" if value == "(default)" else value

    def select_live(self, paths, vault_id: str, name: str) -> str:
        sleep = effects.current().sleep
        for delay in (0.0, 0.6, 1.5, 2.5):
            if delay:
                sleep(delay)
            result = self.cli_run(paths, vault_id, "theme:set", f"name={name}")
            text = result.text
            if "not found" in text.lower():
                continue
            if result.ok and "error" not in text.lower():
                return ""
            return text or f"exit {result.returncode}"
        return f"Obsidian has not listed {name or 'the default theme'} yet"

    # ── per-vault state ──

    def theme_dir(self, vault: Path, theme_name: str) -> Path:
        return vault / ".obsidian/themes" / theme_name

    def desired(self, theme: Theme) -> dict:
        return {"cssTheme": theme.obsidian_name, "accentColor": theme.obsidian_accent, "theme": BASE_MODES[theme.mode]}

    def matches(self, data: dict, desired: dict) -> bool:
        return all(str(data.get(key, "")).lower() == str(value).lower() for key, value in desired.items())

    def files_current(self, ctx, theme, vault: Path) -> bool:
        directory = self.theme_dir(vault, theme.obsidian_name)
        built = ctx.built(theme)
        return (fileops.read_text(directory / "theme.css") == built.path("obsidian_css").read_text()
                and fileops.read_text(directory / "manifest.json") == built.path("obsidian_manifest").read_text())

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Status(NA, reason)
        try:
            problems = self.verify_files(ctx, theme)
        except SurfaceError as exc:
            return Status(BLOCKED, str(exc))
        vaults = self.vaults(ctx.paths)
        if not problems:
            return Status(ACTIVE, f"{theme.obsidian_name} in {len(vaults)} vault(s)")
        return Status(READY, f"{len(problems)} of {len(vaults)} vault(s) not showing {theme.obsidian_name}")

    def capture(self, ctx, theme):
        entries = []
        for vault in self.vaults(ctx.paths):
            data = appearance(vault["path"])  # raises when the file is not JSON
            directory = self.theme_dir(vault["path"], theme.obsidian_name)
            entries.append({
                "id": vault["id"],
                "path": str(vault["path"]),
                "open": vault["open"],
                "keys": {key: data[key] for key in OWNED_KEYS if key in data},
                "appearance_existed": (vault["path"] / ".obsidian/appearance.json").exists(),
                "theme_dir": str(directory),
                "theme_dir_existed": directory.is_dir(),
                "files": {name: fileops.snapshot(directory / name) for name in ("manifest.json", "theme.css")},
            })
        return {"theme_name": theme.obsidian_name, "vaults": entries}

    def write_vault(self, ctx, theme, vault: Path) -> bool:
        changed = False
        directory = self.theme_dir(vault, theme.obsidian_name)
        built = ctx.built(theme)
        for name, key in (("manifest.json", "obsidian_manifest"), ("theme.css", "obsidian_css")):
            text = built.path(key).read_text()
            if fileops.read_text(directory / name) != text:
                fileops.write_text(directory / name, text)
                changed = True
        data = appearance(vault)
        desired = self.desired(theme)
        if not self.matches(data, desired):
            data.update(desired)
            fileops.write_text(vault / ".obsidian/appearance.json", json.dumps(data, indent=2) + "\n")
            changed = True
        return changed

    def ensure(self, ctx, theme):
        """Repair: put the theme files back into vaults that select the theme."""

        repaired = []
        names = labels(registry(ctx.paths))
        for vault in self.vaults(ctx.paths):
            data = appearance(vault["path"])
            if data.get("cssTheme") == theme.obsidian_name and not self.files_current(ctx, theme, vault["path"]):
                directory = self.theme_dir(vault["path"], theme.obsidian_name)
                built = ctx.built(theme)
                fileops.write_text(directory / "manifest.json", built.path("obsidian_manifest").read_text())
                fileops.write_text(directory / "theme.css", built.path("obsidian_css").read_text())
                repaired.append(f"{names[vault['id']]}: reinstalled {theme.obsidian_name}")
        return repaired

    def apply(self, ctx, theme, captured):
        lines = self.ensure(ctx, theme)
        names = labels(registry(ctx.paths))
        open_vaults = []
        for entry in captured["vaults"]:
            changed = self.write_vault(ctx, theme, Path(entry["path"]))
            lines.append(f"{names.get(entry['id'], Path(entry['path']).name)}: {'themed' if changed else 'already current'}")
            if entry["open"]:
                open_vaults.append(entry)
        lines.extend(self.refresh(ctx, open_vaults, theme.obsidian_name, names))
        return lines

    def refresh(self, ctx, open_vaults: list[dict], name: str, names: dict) -> list[str]:
        if not open_vaults:
            return ["no vault window is open; vaults show the theme when they are opened"]
        state, why = self.cli_state(ctx.paths)
        if state != "ready":
            return [f"open vaults update when reopened ({why})"]
        effects.current().sleep(WATCH_DELAY)
        lines = []
        for entry in open_vaults:
            problem = self.select_live(ctx.paths, entry["id"], name)
            label = names.get(entry["id"], entry["id"])
            lines.append(f"{label}: {'switched live' if not problem else 'live switch failed: ' + problem}")
        effects.current().sleep(SAVE_DELAY)
        return lines

    def verify_files(self, ctx, theme) -> list[str]:
        problems = []
        names = labels(registry(ctx.paths))
        desired = self.desired(theme)
        for vault in self.vaults(ctx.paths):
            label = names[vault["id"]]
            if not self.matches(appearance(vault["path"]), desired):
                problems.append(f"{label}: appearance.json does not select {theme.obsidian_name}")
            elif not self.files_current(ctx, theme, vault["path"]):
                problems.append(f"{label}: theme files are missing or out of date")
        return problems

    def verify(self, ctx, theme):
        problems = self.verify_files(ctx, theme)
        names = labels(registry(ctx.paths))
        if self.cli_state(ctx.paths)[0] == "ready":
            for vault in self.vaults(ctx.paths):
                if vault["open"]:
                    live = self.live_theme(ctx.paths, vault["id"])
                    if live is not None and live != theme.obsidian_name:
                        problems.append(f"{names[vault['id']]}: the running app shows {live or 'the default theme'}")
        return problems

    def restore(self, ctx, captured):
        lines = []
        names = labels(registry(ctx.paths))
        open_vaults = []
        for entry in captured["vaults"]:
            vault = Path(entry["path"])
            label = names.get(entry["id"], vault.name)
            if not (vault / ".obsidian").is_dir():
                lines.append(f"{label}: no longer a vault, nothing to restore")
                continue
            data = appearance(vault)
            restored = {key: value for key, value in data.items() if key not in OWNED_KEYS}
            restored.update(entry["keys"])
            if {k: data.get(k) for k in OWNED_KEYS} != {k: restored.get(k) for k in OWNED_KEYS}:
                if not entry["appearance_existed"] and not restored:
                    (vault / ".obsidian/appearance.json").unlink(missing_ok=True)
                else:
                    fileops.write_text(vault / ".obsidian/appearance.json", json.dumps(restored, indent=2) + "\n")
            directory = Path(entry["theme_dir"])
            for name, state in entry["files"].items():
                fileops.restore_snapshot(directory / name, state)
            if not entry["theme_dir_existed"] and directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
            lines.append(f"{label}: restored {entry['keys'].get('cssTheme') or 'default theme'}")
            if entry["open"]:
                open_vaults.append(dict(entry, previous=entry["keys"].get("cssTheme", "")))

        if open_vaults and self.cli_state(ctx.paths)[0] == "ready":
            effects.current().sleep(WATCH_DELAY)
            for entry in open_vaults:
                self.select_live(ctx.paths, entry["id"], entry["previous"])
            effects.current().sleep(SAVE_DELAY)
            # Obsidian saves its whole config from memory; put CORE's keys back
            # if that save raced the restore.
            for entry in open_vaults:
                vault = Path(entry["path"])
                data = appearance(vault)
                if {k: data.get(k) for k in OWNED_KEYS} != {k: entry["keys"].get(k) for k in OWNED_KEYS}:
                    kept = {key: value for key, value in data.items() if key not in OWNED_KEYS}
                    kept.update(entry["keys"])
                    fileops.write_text(vault / ".obsidian/appearance.json", json.dumps(kept, indent=2) + "\n")
        return lines

    def verify_restored(self, ctx, theme, captured) -> list[str]:
        problems = []
        for entry in captured["vaults"]:
            vault = Path(entry["path"])
            if not (vault / ".obsidian").is_dir():
                continue
            data = appearance(vault)
            if {k: data.get(k) for k in OWNED_KEYS} != {k: entry["keys"].get(k) for k in OWNED_KEYS}:
                problems.append(f"{vault.name}: appearance keys differ from before")
        return problems

    def integration(self, ctx, theme):
        paths = ctx.paths
        if not (paths.obsidian_app.exists() or paths.obsidian_registry.exists()):
            return Integration(NA, reason="Obsidian is not installed")
        everything = registry(paths)
        names = labels(everything)
        state, why = self.cli_state(paths)
        facts = [
            ("registered vaults", str(len(everything))),
            ("CLI", {"ready": "enabled", "disabled": "off", "not-running": "app not running", "absent": "not installed"}[state]),
        ]
        repair = []
        for vault in everything:
            label = names[vault["id"]]
            if not vault["exists"]:
                facts.append((label, "registered but missing on disk"))
                continue
            if not vault["initialized"]:
                facts.append((label, "not initialized yet"))
                continue
            try:
                data = appearance(vault["path"])
            except SurfaceError as exc:
                facts.append((label, f"unreadable appearance.json ({exc})"))
                continue
            selected = data.get("cssTheme") or "default"
            note = f"{selected}{' · open' if vault['open'] else ''} · id {vault['id']}"
            if theme is not None and data.get("cssTheme") == theme.obsidian_name and not self.files_current(ctx, theme, vault["path"]):
                note += " · theme files missing or out of date"
                repair.append(f"Reinstall {theme.obsidian_name} into {label}")
            facts.append((label, note))
        if not self.vaults(paths):
            return Integration(NA, facts, reason="no initialized vault is registered")
        if repair:
            return Integration(REPAIRABLE, facts, repair, reason="CORE theme files are missing from vaults that select them",
                               theme_id=theme.id)
        uses = theme is not None and all(appearance(v["path"]).get("cssTheme") == theme.obsidian_name for v in self.vaults(paths))
        return Integration(ACTIVE if uses else READY, facts, theme_id=theme.id if theme else "")
