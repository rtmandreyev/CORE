"""Surfaces CORE supports without applying anything on its own.

* ChatGPT (the Codex desktop app): CORE produces the app's own theme-share
  string (`codex-theme-v1:…`); the user pastes it into Settings → Appearance →
  Import. CORE never edits ChatGPT's configuration.
* macOS appearance: optional light/dark switching through System Events. Never
  part of "all applications" unless the user turned it on.
* Claude Desktop: its appearance (colour mode, interface font, code theme) is
  kept by the claude.ai interface itself, with no supported external control.
  With its colour mode set to "Match System" it follows macOS, which CORE's
  opt-in macOS appearance surface can switch. CORE only reports this.
"""

from __future__ import annotations

import json

from corelib import effects, fileops
from corelib.surfaces.base import (
    ACTIVE, EXPORT, INFO, NA, OPT_IN, Integration, Status, Surface, SurfaceError,
)


class ChatGPT(Surface):
    key = "chatgpt"
    label = "ChatGPT"
    kind = "export"

    def available(self, paths):
        if paths.chatgpt_app.exists():
            return (True, "")
        return (False, "ChatGPT is not installed")

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Status(NA, reason)
        return Status(EXPORT, f"import the {theme.mode} theme string in ChatGPT → Settings → Appearance")

    def import_string(self, ctx, theme) -> str:
        return ctx.built(theme).path("chatgpt").read_text().strip()

    def integration(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Integration(NA, reason=reason)
        return Integration(EXPORT, [("installed", "yes"), ("theme import", "Settings → Appearance → Import"),
                                    ("CORE edits ChatGPT config", "never")])


class MacOS(Surface):
    key = "macos"
    label = "macOS appearance"
    kind = "optin"

    def available(self, paths):
        return (True, "")

    def current(self) -> str:
        result = effects.current().run(["defaults", "read", "-g", "AppleInterfaceStyle"], timeout=10)
        return "dark" if result.ok and result.stdout.strip().lower() == "dark" else "light"

    def set(self, mode: str) -> None:
        flag = "true" if mode == "dark" else "false"
        result = effects.osascript(
            f'tell application "System Events" to tell appearance preferences to set dark mode to {flag}'
        )
        if not result.ok:
            raise SurfaceError(
                "macOS refused the appearance change "
                f"({result.text or result.returncode}); allow automation of System Events in System Settings"
            )

    def status(self, ctx, theme):
        if self.current() == theme.macos_appearance:
            return Status(ACTIVE, theme.macos_appearance)
        return Status(OPT_IN, f"switch macOS to {theme.macos_appearance} (currently {self.current()})")

    def capture(self, ctx, theme):
        return {"appearance": self.current()}

    def apply(self, ctx, theme, captured):
        if self.current() == theme.macos_appearance:
            return [f"macOS already {theme.macos_appearance}"]
        self.set(theme.macos_appearance)
        return [f"macOS appearance: {theme.macos_appearance}"]

    def verify(self, ctx, theme):
        current = self.current()
        return [] if current == theme.macos_appearance else [f"macOS is {current}"]

    def restore(self, ctx, captured):
        if self.current() != captured["appearance"]:
            self.set(captured["appearance"])
        return [f"macOS appearance: {captured['appearance']}"]

    def integration(self, ctx, theme):
        return Integration(OPT_IN, [("appearance", self.current()),
                                    ("managed by CORE", "only when you select it")])


class Claude(Surface):
    key = "claude"
    label = "Claude"
    kind = "info"

    def available(self, paths):
        if paths.claude_app.exists():
            return (True, "")
        return (False, "Claude is not installed")

    def status(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        return Status(INFO if installed else NA, "set its colour mode to Match System to follow macOS" if installed else reason)

    def integration(self, ctx, theme):
        installed, reason = self.available(ctx.paths)
        if not installed:
            return Integration(NA, reason=reason)
        mode = "unknown"
        try:
            mode = str(json.loads(fileops.read_text(ctx.paths.claude_config) or "{}").get("userThemeMode") or "unknown")
        except json.JSONDecodeError:
            pass
        facts = [("installed", "yes"),
                 ("colour mode (last seen)", mode),
                 ("font and code theme", "set inside Claude; no supported external control"),
                 ("recommendation", "Settings → Appearance → Color mode: Match System, then `core apply <theme> macos`")]
        return Integration(INFO, facts)
