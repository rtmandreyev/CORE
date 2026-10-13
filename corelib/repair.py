"""`core doctor` (read-only) and `core repair` (CORE-owned state only).

APPLY selects a theme. REPAIR restores CORE's own infrastructure — a skin
file, a theme file, the VS Code base extension, a vault's theme files — when an
application update or anything else removed or damaged it. DOCTOR reports both
without writing.

Repair never changes which theme an application uses and never touches a
setting CORE does not own. It runs exactly the `ensure()` code a normal apply
runs, then re-checks, so a repair that did not work is never reported healthy.
"""

from __future__ import annotations

from typing import Callable

from corelib import surfaces as registry
from corelib.surfaces import BLOCKED, NA, REPAIRABLE, Context, SurfaceError
from corelib.theme import Theme, ThemeError, load
from corelib.transaction import read_journal, read_state

EXIT_OK, EXIT_FAILED = 0, 1


def current_theme(ctx: Context) -> Theme | None:
    theme_id = read_state(ctx.paths).get("theme")
    if not theme_id:
        return None
    try:
        return load(ctx.paths.root, theme_id)
    except ThemeError:
        return None


def doctor(ctx: Context, out: Callable[[str], None] = print, keys: list[str] | None = None) -> int:
    state = read_state(ctx.paths)
    theme = current_theme(ctx)
    out("CORE // DOCTOR   (read-only)")
    out("")
    if state.get("theme") and theme is None:
        out(f"  Active theme   {state['theme']} — this theme no longer exists")
    else:
        out(f"  Active theme   {theme.name if theme else 'none applied by CORE yet'}")
    journal = read_journal(ctx.paths)
    if journal:
        out(f"  Interrupted    a {journal.get('kind', 'transaction')} was interrupted — run `core restore`")
    out("")

    for surface in registry.SURFACES:
        if keys and surface.key not in keys:
            continue
        try:
            integration = surface.integration(ctx, theme)
        except Exception as exc:  # doctor reports, it never crashes on one app
            out(f"  {surface.label}")
            out(f"    could not inspect ({exc})")
            out("")
            continue
        out(f"  {surface.label:<18} {integration.state}" + (f"  — {integration.reason}" if integration.reason else ""))
        for name, value in integration.facts:
            out(f"    {name:<24} {value}")
        out("")
    return EXIT_OK


def repair(
    ctx: Context,
    keys: list[str] | None = None,
    *,
    dry_run: bool = False,
    out: Callable[[str], None] = print,
) -> int:
    for key in keys or []:
        if key not in registry.REPAIR_KEYS:
            raise KeyError(f"nothing to repair for {key!r} (repairable: {', '.join(registry.REPAIR_KEYS)})")

    theme = current_theme(ctx)
    out("CORE // REPAIR" + ("   (dry run)" if dry_run else ""))
    out("")
    failed = False
    changed = False

    for key in keys or registry.REPAIR_KEYS:
        surface = registry.BY_KEY[key]
        integration = surface.integration(ctx, theme)
        if integration.state == NA and not keys:
            continue
        out(f"  {surface.label}")
        for name, value in integration.facts:
            out(f"    {name:<22} {value}")
        out(f"    {'state':<22} {integration.state}" + (f" — {integration.reason}" if integration.reason else ""))

        if integration.state != REPAIRABLE:
            if integration.state == BLOCKED:
                failed = True
            out("")
            continue

        target = theme
        if integration.theme_id and (theme is None or integration.theme_id != theme.id):
            target = load(ctx.paths.root, integration.theme_id)

        out("")
        out("    Plan")
        for number, step in enumerate(integration.plan, 1):
            out(f"      {number}  {step}")
        out("")
        if dry_run:
            continue

        try:
            for line in surface.ensure(ctx, target):
                out(f"    {line}")
            changed = True
        except (SurfaceError, OSError) as exc:
            out(f"    repair failed: {exc}")
            failed = True
            continue

        after = surface.integration(ctx, target)
        if after.state == REPAIRABLE:
            out(f"    still {after.state}: {after.reason}")
            failed = True
        else:
            out(f"    verified: {after.state}")
        out("")

    if dry_run:
        out("No changes made.")
    elif not changed and not failed:
        out("Nothing to repair.")
    return EXIT_FAILED if failed else EXIT_OK
