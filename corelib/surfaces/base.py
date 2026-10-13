"""The contract every surface implements.

A surface is one application CORE can theme. Every surface answers, without
writing anything:

    available(paths)            is the application on this machine?
    status(ctx, theme)          ACTIVE / READY / REPAIRABLE / N/A / BLOCKED
    capture(ctx, theme)         the exact prior state of what CORE will change

and, when asked to change something:

    apply(ctx, theme, captured) change the application (calls `ensure` first)
    verify(ctx, theme)          problems, empty when the application matches
    restore(ctx, captured)      put back what was captured (semantically)

Surfaces with CORE-owned infrastructure (a skin file, a base extension, a theme
file) also implement the repair contract:

    integration(ctx, theme)     state + facts + the repair plan
    ensure(ctx, theme)          repair CORE-owned state; idempotent

`apply` calls `ensure` itself, so a normal apply self-heals with exactly the
code `core repair` runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from corelib.build import Built, ensure_built
from corelib.paths import Paths
from corelib.theme import Theme

ACTIVE = "ACTIVE"
READY = "READY"
REPAIRABLE = "REPAIRABLE"
NA = "N/A"
BLOCKED = "BLOCKED"
EXPORT = "EXPORT"
OPT_IN = "OPT-IN"
INFO = "INFO"


class SurfaceError(Exception):
    """A surface cannot safely do what was asked."""


@dataclass
class Status:
    state: str
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Integration:
    """What `core doctor` / `core repair` know about CORE-owned infrastructure."""

    state: str  # ACTIVE, READY, REPAIRABLE, N/A, BLOCKED
    facts: list[tuple[str, str]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    reason: str = ""
    theme_id: str = ""  # the CORE theme whose infrastructure this describes


@dataclass
class Context:
    paths: Paths
    report: Callable[[str], None] = lambda line: None
    preview: bool = False
    _built: dict[str, Built] = field(default_factory=dict)

    def built(self, theme: Theme) -> Built:
        if theme.id not in self._built:
            self._built[theme.id] = ensure_built(self.paths, theme)
        return self._built[theme.id]


class Surface:
    key = ""
    label = ""
    # apply: themed by apply/preview; export: produces an artifact the user
    # imports; optin: only when the user selects it; info: doctor only.
    kind = "apply"
    repairable = False

    def available(self, paths: Paths) -> tuple[bool, str]:
        raise NotImplementedError

    def status(self, ctx: Context, theme: Theme) -> Status:
        raise NotImplementedError

    def capture(self, ctx: Context, theme: Theme) -> dict:
        raise NotImplementedError

    def apply(self, ctx: Context, theme: Theme, captured: dict) -> list[str]:
        raise NotImplementedError

    def verify(self, ctx: Context, theme: Theme) -> list[str]:
        raise NotImplementedError

    def restore(self, ctx: Context, captured: dict) -> list[str]:
        raise NotImplementedError

    def verify_restored(self, ctx: Context, theme: Theme, captured: dict) -> list[str]:
        """After a restore: does a fresh capture equal the original one?"""

        if self.capture(ctx, theme) != captured:
            return [f"{self.label}: state differs from what was captured"]
        return []

    def integration(self, ctx: Context, theme: Theme | None) -> Integration:
        installed, reason = self.available(ctx.paths)
        return Integration(NA if not installed else READY, reason=reason if not installed else "")

    def ensure(self, ctx: Context, theme: Theme) -> list[str]:
        return []

    def doctor(self, ctx: Context, theme: Theme | None) -> list[tuple[str, str]]:
        return self.integration(ctx, theme).facts


def exists_any(*paths: Path) -> bool:
    return any(Path(path).exists() for path in paths)
