"""macOS Terminal: one CORE profile, selected as default and startup profile.

The profile ("CORE <ID>") carries background, text, bold, cursor, selection and
the 16 ANSI colours, compiled to Terminal's own `.terminal` format.

* Terminal not running: CORE adds exactly one entry to Terminal's
  `Window Settings` with `defaults write … -dict-add` and sets the
  `Default Window Settings` / `Startup Window Settings` keys. Other profiles
  and preferences are never rewritten.
* Terminal running: its preferences live in memory, so CORE uses Terminal's own
  import (opening the .terminal file, which Terminal answers by adding the
  profile and opening one window with it) and then Terminal's AppleScript
  `default settings`, `startup settings` and `current settings` properties.

Restore puts back the default and startup profiles (and, when Terminal is
running, each open tab's profile). The CORE profile itself stays installed:
it is inert, and Terminal offers no supported way to remove one entry.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

from corelib import effects
from corelib.build import terminal_profile, terminal_profile_name
from corelib.surfaces.base import ACTIVE, NA, READY, REPAIRABLE, Integration, Status, Surface, SurfaceError
from corelib.theme import Theme

DOMAIN = "com.apple.Terminal"
DEFAULT_KEY = "Default Window Settings"
STARTUP_KEY = "Startup Window Settings"

_TABS_SCRIPT = '''tell application "Terminal"
  set out to ""
  repeat with w in windows
    set i to 0
    repeat with t in tabs of w
      set i to i + 1
      set out to out & ((id of w) as string) & (character id 9) & (i as string) & (character id 9) & (name of current settings of t) & linefeed
    end repeat
  end repeat
  return (name of default settings) & linefeed & (name of startup settings) & linefeed & out
end tell'''


def decoded_color(data) -> str | None:
    """#RRGGBB from an archived NSColor (sRGB components preferred)."""

    if not isinstance(data, bytes):
        return None
    try:
        archive = plistlib.loads(data)
    except Exception:
        return None
    for item in archive.get("$objects", []):
        if isinstance(item, dict) and ("NSComponents" in item or "NSRGB" in item):
            raw = item.get("NSComponents") or item.get("NSRGB")
            values = [float(part) for part in raw.rstrip(b"\x00").split()[:3]]
            return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in values)
    return None


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Terminal(Surface):
    key = "terminal"
    label = "Terminal"
    repairable = True

    def available(self, paths):
        if paths.terminal_app.exists():
            return (True, "")
        return (False, "macOS Terminal was not found")

    def binary(self, paths) -> Path:
        return paths.terminal_app / "Contents/MacOS/Terminal"

    def running(self, paths) -> bool:
        return effects.process_running(self.binary(paths))

    def prefs(self) -> dict:
        result = effects.current().run(["defaults", "export", DOMAIN, "-"], timeout=15)
        if not result.ok or not result.stdout.strip():
            return {}
        try:
            return plistlib.loads(result.stdout.encode())
        except Exception:
            return {}

    def profile_file(self, ctx, theme: Theme) -> Path:
        return ctx.built(theme).path("terminal")

    def profile_current(self, prefs: dict, theme: Theme) -> bool:
        """Do the installed profile's colours equal the theme's?

        Compared as decoded sRGB values, not archive bytes: Terminal re-encodes
        an imported profile in its own form (with an ICC profile attached).
        """

        installed = (prefs.get("Window Settings") or {}).get(terminal_profile_name(theme))
        if not isinstance(installed, dict):
            return False
        wanted = terminal_profile(theme)
        return all(decoded_color(installed.get(key)) == decoded_color(value)
                   for key, value in wanted.items() if key.endswith("Color"))

    def live_state(self) -> tuple[str, str, list[tuple[str, str, str]]]:
        result = effects.osascript(_TABS_SCRIPT)
        if not result.ok:
            raise SurfaceError(f"Terminal did not answer AppleScript ({result.text or result.returncode})")
        lines = result.stdout.splitlines()
        tabs = []
        for line in lines[2:]:
            parts = line.split("\t")
            if len(parts) == 3:
                tabs.append((parts[0], parts[1], parts[2]))
        return (lines[0].strip() if lines else "", lines[1].strip() if len(lines) > 1 else "", tabs)

    def window_ids(self) -> set[str]:
        result = effects.osascript('tell application "Terminal" to get id of every window')
        if not result.ok:
            return set()
        return {part.strip() for part in result.stdout.split(",") if part.strip().isdigit()}

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Status(NA, reason)
        prefs = self.prefs()
        name = terminal_profile_name(theme)
        if prefs.get(DEFAULT_KEY) == name and self.profile_current(prefs, theme):
            return Status(ACTIVE, name)
        return Status(READY, f"currently {prefs.get(DEFAULT_KEY) or 'Basic'}")

    def capture(self, ctx, theme):
        running = self.running(ctx.paths)
        prefs = self.prefs()
        captured = {
            "running": running,
            "default": prefs.get(DEFAULT_KEY),
            "startup": prefs.get(STARTUP_KEY),
            "profile": terminal_profile_name(theme),
            "tabs": [],
        }
        if running:
            default, startup, tabs = self.live_state()
            captured.update(default=default or captured["default"], startup=startup or captured["startup"], tabs=tabs)
        return captured

    def ensure(self, ctx, theme):
        """Install or refresh the CORE profile (also what `core repair` runs)."""

        prefs = self.prefs()
        if self.profile_current(prefs, theme):
            return []
        name = terminal_profile_name(theme)
        run = effects.current().run

        if self.running(ctx.paths):
            if name in (prefs.get("Window Settings") or {}):
                effects.osascript(f'tell application "Terminal" to delete settings set {_quote(name)}')
            before = self.window_ids()
            result = run(["open", "-a", str(ctx.paths.terminal_app), str(self.profile_file(ctx, theme))], timeout=20)
            if not result.ok:
                raise SurfaceError(f"Terminal did not import the CORE profile ({result.text or result.returncode})")
            effects.current().sleep(1.0)
            # Importing opens a window with the new profile; close the one CORE caused.
            for window_id in self.window_ids() - before:
                effects.osascript(f'tell application "Terminal" to close (first window whose id is {int(window_id)}) saving no')
            return [f"imported profile {name}"]

        fragment = plistlib.dumps(terminal_profile(theme), fmt=plistlib.FMT_XML).decode()
        fragment = fragment[fragment.index("<dict>") : fragment.rindex("</dict>") + len("</dict>")]
        result = run(["defaults", "write", DOMAIN, "Window Settings", "-dict-add", name, fragment], timeout=20)
        if not result.ok:
            raise SurfaceError(f"could not add the CORE profile ({result.text or result.returncode})")
        return [f"installed profile {name}"]

    def select(self, ctx, default: str | None, startup: str | None, tabs: list | None, *, running: bool) -> None:
        run = effects.current().run
        if running:
            script = ['tell application "Terminal"']
            if default:
                script.append(f"  set default settings to settings set {_quote(default)}")
            if startup:
                script.append(f"  set startup settings to settings set {_quote(startup)}")
            for window_id, index, name in tabs or []:
                script.append(
                    f"  try\n    set current settings of tab {int(index)} of (first window whose id is {int(window_id)}) "
                    f"to settings set {_quote(name)}\n  end try"
                )
            script.append("end tell")
            result = effects.osascript("\n".join(script))
            if not result.ok:
                raise SurfaceError(f"Terminal refused the profile change ({result.text or result.returncode})")
            return
        for key, value in ((DEFAULT_KEY, default), (STARTUP_KEY, startup)):
            if value:
                result = run(["defaults", "write", DOMAIN, key, "-string", value], timeout=15)
            else:
                result = run(["defaults", "delete", DOMAIN, key], timeout=15)
            if not result.ok and value:
                raise SurfaceError(f"could not set {key} ({result.text or result.returncode})")

    def apply(self, ctx, theme, captured):
        lines = self.ensure(ctx, theme)
        name = terminal_profile_name(theme)
        running = self.running(ctx.paths)
        tabs = [(window_id, index, name) for window_id, index, _ in captured.get("tabs", [])] if running else None
        self.select(ctx, name, name, tabs, running=running)
        lines.append(f"default and startup profile: {name}" + (f" ({len(tabs)} open tab(s) switched)" if tabs else ""))
        return lines

    def verify(self, ctx, theme):
        name = terminal_profile_name(theme)
        if self.running(ctx.paths):
            default, startup, _tabs = self.live_state()
            problems = [] if default == name else [f"default profile is {default}, expected {name}"]
            return problems
        prefs = self.prefs()
        problems = []
        if not self.profile_current(prefs, theme):
            problems.append(f"profile {name} is missing or out of date")
        if prefs.get(DEFAULT_KEY) != name:
            problems.append(f"default profile is {prefs.get(DEFAULT_KEY) or 'Basic'}, expected {name}")
        return problems

    def restore(self, ctx, captured):
        running = self.running(ctx.paths)
        self.select(ctx, captured.get("default"), captured.get("startup"), captured.get("tabs") if running else None, running=running)
        return [f"default profile restored to {captured.get('default') or 'Basic'}"]

    def verify_restored(self, ctx, theme, captured):
        if self.running(ctx.paths):
            default, _startup, _tabs = self.live_state()
        else:
            default = self.prefs().get(DEFAULT_KEY)
        if (default or None) != (captured.get("default") or None):
            return [f"Terminal: default profile is {default}, expected {captured.get('default')}"]
        return []

    def integration(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Integration(NA, reason=reason)
        prefs = self.prefs()
        profiles = [name for name in (prefs.get("Window Settings") or {}) if name.startswith("CORE ")]
        facts = [("default profile", str(prefs.get(DEFAULT_KEY) or "Basic")),
                 ("CORE profiles", ", ".join(profiles) or "none")]
        if theme is not None and prefs.get(DEFAULT_KEY) == terminal_profile_name(theme) and not self.profile_current(prefs, theme):
            return Integration(REPAIRABLE, facts, [f"Reinstall profile {terminal_profile_name(theme)}"],
                               reason="the selected CORE profile is missing or out of date", theme_id=theme.id)
        active = theme is not None and prefs.get(DEFAULT_KEY) == terminal_profile_name(theme)
        return Integration(ACTIVE if active else READY, facts, theme_id=theme.id if theme else "")
