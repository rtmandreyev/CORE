"""Apply and preview as one all-or-nothing transaction.

    preflight -> capture -> journal -> apply -> verify -> (preview: wait) -> done

* Preflight is read-only. A BLOCKED surface stops everything before any write.
* The journal (~/.config/core/transaction.json) is written before the first
  change and holds every captured state, so an interrupted run — Ctrl-C,
  SIGTERM, a closed terminal, even `kill -9` — can always be undone with
  `core restore`.
* Any failure or interruption after the first write rolls back every surface
  touched so far, in reverse order. Rollback ignores further Ctrl-C, tries
  every surface even when one fails, and keeps the journal when anything could
  not be put back.
* A persistent apply that verifies removes the journal and records the theme
  in ~/.config/core/state.json. A preview restores when the user presses
  Enter (or on any interruption) and verifies the restoration.
"""

from __future__ import annotations

import json
import os
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from corelib import fileops
from corelib import surfaces as registry
from corelib.paths import Paths
from corelib.surfaces import BLOCKED, NA, Context, SurfaceError
from corelib.theme import Theme, load

EXIT_OK, EXIT_FAILED, EXIT_ATTENTION, EXIT_INTERRUPTED = 0, 1, 3, 130


class Interrupted(BaseException):
    """SIGTERM / SIGHUP during a transaction (treated like Ctrl-C)."""


@dataclass
class Plan:
    theme: Theme
    selected: list[str]
    skipped: list[tuple[str, str]] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)
    rows: list[tuple[str, str, str, list[str]]] = field(default_factory=list)  # key, state, detail, warnings


# ── journal and state ──


def read_journal(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.journal.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return {"corrupt": True}


def write_journal(paths: Paths, payload: dict) -> None:
    fileops.write_text(paths.journal, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def remove_journal(paths: Paths) -> None:
    paths.journal.unlink(missing_ok=True)


def journal_owner_alive(journal: dict) -> bool:
    pid = journal.get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_state(paths: Paths) -> dict:
    try:
        return json.loads(paths.state_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_state(paths: Paths, theme: Theme, keys: list[str]) -> None:
    previous = read_state(paths)
    if previous.get("theme") == theme.id:  # applying one more app keeps the others
        keys = list(dict.fromkeys([*previous.get("surfaces", []), *keys]))
    fileops.write_text(paths.state_file, json.dumps({
        "theme": theme.id,
        "surfaces": keys,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2) + "\n")


def user_opt_ins(paths: Paths) -> set[str]:
    """Surfaces the user turned on for "all": ~/.config/core/config.toml."""

    import tomllib

    try:
        data = tomllib.loads(paths.user_config.read_text())
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return set()
    enabled = data.get("surfaces", {})
    return {key for key, value in enabled.items() if value is True and key in registry.SELECTABLE_KEYS}


# ── planning (read-only) ──


def plan(ctx: Context, theme: Theme, requested: list[str] | None) -> Plan:
    explicit = bool(requested) and requested != ["all"]
    if explicit:
        for key in requested:
            registry.get(key)  # raises with the list of valid names
        keys = list(dict.fromkeys(requested))
    else:
        opted = user_opt_ins(ctx.paths)
        keys = [surface.key for surface in registry.SURFACES
                if surface.kind in ("apply", "export", "info") or surface.key in opted]
        keys += [key for key in registry.BY_KEY if registry.BY_KEY[key].kind == "optin" and key not in keys]

    result = Plan(theme, [])
    for key in keys:
        surface = registry.BY_KEY[key]
        try:
            status = surface.status(ctx, theme)
        except Exception as exc:  # one application's surprise must not hide the others
            status = registry.Status(BLOCKED, f"could not inspect ({exc})")
        result.rows.append((key, status.state, status.detail, status.warnings))
        if surface.kind == "export":
            result.skipped.append((key, "export/import: use `core export` and import it in the app"))
        elif surface.kind == "info":
            result.skipped.append((key, status.detail))
        elif surface.kind == "optin" and not explicit and key not in user_opt_ins(ctx.paths):
            result.rows[-1] = (key, registry.OPT_IN, f"not managed — `core apply {theme.id} {key}` to include it", status.warnings)
            result.skipped.append((key, "opt-in"))
        elif status.state == NA:
            result.skipped.append((key, status.detail))
        elif status.state == BLOCKED:
            result.blocked.append((key, status.detail))
        else:
            result.selected.append(key)
    return result


# ── signals ──


@contextmanager
def interruptible():
    """SIGTERM and SIGHUP behave like Ctrl-C while a transaction runs."""

    def raise_interrupted(signum, _frame):
        raise Interrupted(f"signal {signum}")

    previous = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, raise_interrupted)
        except (ValueError, OSError):
            pass
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


@contextmanager
def shielded():
    """Rollback must finish: ignore Ctrl-C / SIGTERM / SIGHUP while it runs."""

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, signal.SIG_IGN)
        except (ValueError, OSError):
            pass
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


# ── execution ──


def restore_all(ctx: Context, theme: Theme, keys: list[str], states: dict, out: Callable[[str], None]) -> list[str]:
    """Restore every key (reverse order). Returns failures; never stops early."""

    failures = []
    for key in reversed(keys):
        surface = registry.BY_KEY[key]
        try:
            for line in surface.restore(ctx, states[key]):
                out(f"  {surface.label:<16} {line}")
        except Exception as exc:  # every surface gets its chance
            failures.append(f"{surface.label}: restore failed ({exc})")
    for key in keys:
        surface = registry.BY_KEY[key]
        try:
            failures.extend(surface.verify_restored(ctx, theme, states[key]))
        except Exception as exc:
            failures.append(f"{surface.label}: could not verify the restore ({exc})")
    return failures


def execute(
    ctx: Context,
    theme: Theme,
    the_plan: Plan,
    *,
    preview: bool,
    out: Callable[[str], None] = print,
    wait: Callable[[], object] | None = None,
) -> int:
    paths = ctx.paths
    ctx.preview = preview
    keys = list(the_plan.selected)

    states: dict[str, dict] = {}
    for key in keys:
        surface = registry.BY_KEY[key]
        try:
            states[key] = surface.capture(ctx, theme)
        except SurfaceError as exc:
            out(f"  {surface.label}: {exc}")
            out("No changes made.")
            return EXIT_FAILED

    write_journal(paths, {
        "kind": "preview" if preview else "apply",
        "theme": theme.id,
        "surfaces": keys,
        "states": states,
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })

    touched: list[str] = []
    failure: BaseException | None = None

    with interruptible():
        try:
            for key in keys:
                surface = registry.BY_KEY[key]
                touched.append(key)  # before apply: a half-applied surface is restored too
                lines = surface.apply(ctx, theme, states[key])
                out(f"  {surface.label:<16} {lines[0] if lines else 'applied'}")
                for line in lines[1:]:
                    out(f"  {'':<16} {line}")
            problems = []
            for key in keys:
                problems.extend(f"{registry.BY_KEY[key].label}: {p}" for p in registry.BY_KEY[key].verify(ctx, theme))
            if problems:
                raise SurfaceError("verification failed: " + "; ".join(problems))
            if preview:
                out("")
                out(f"  Previewing {theme.name}. Press Enter to restore your previous setup.")
                (wait or input)()
        except BaseException as exc:  # Ctrl-C, signals, errors: all roll back
            failure = exc

    if failure is not None and not (preview and isinstance(failure, (KeyboardInterrupt, Interrupted, EOFError))
                                    and len(touched) == len(keys)):
        interrupted = isinstance(failure, (KeyboardInterrupt, Interrupted))
        out("")
        out("  Interrupted — rolling back." if interrupted else f"  Failed: {failure}")
        with shielded():
            failures = restore_all(ctx, theme, touched, states, out)
        return _finish(paths, failures, EXIT_INTERRUPTED if interrupted else EXIT_FAILED, out, rolled_back=True)

    if preview:
        out("")
        out("  Restoring your previous setup…")
        with shielded():
            failures = restore_all(ctx, theme, keys, states, out)
        return _finish(paths, failures, EXIT_OK, out, rolled_back=False)

    remove_journal(paths)
    write_state(paths, theme, keys)
    out("")
    out(f"  {theme.name} applied and verified.")
    return EXIT_OK


def _finish(paths: Paths, failures: list[str], code: int, out, *, rolled_back: bool) -> int:
    if failures:
        out("")
        out("  ATTENTION — these could not be put back:")
        for line in failures:
            out(f"    - {line}")
        out(f"  The journal is kept at {paths.journal}; run `core restore` to try again.")
        return EXIT_ATTENTION
    remove_journal(paths)
    out("")
    out("  Rolled back: everything is as it was before." if rolled_back else "  Restored and verified.")
    return code


def recover(ctx: Context, out: Callable[[str], None] = print) -> int:
    """`core restore`: undo an interrupted apply or preview from its journal."""

    journal = read_journal(ctx.paths)
    if journal is None:
        out("Nothing to restore: no interrupted apply or preview.")
        return EXIT_OK
    if journal.get("corrupt"):
        out(f"The journal at {ctx.paths.journal} is unreadable; inspect it by hand.")
        return EXIT_ATTENTION
    if journal_owner_alive(journal):
        out(f"Another CORE process (pid {journal['pid']}) is still running this {journal.get('kind')}.")
        return EXIT_FAILED
    theme = load(ctx.paths.root, journal["theme"])
    out(f"Restoring the interrupted {journal.get('kind')} of {theme.name}:")
    with shielded():
        failures = restore_all(ctx, theme, journal["surfaces"], journal["states"], out)
    return _finish(ctx.paths, failures, EXIT_OK, out, rolled_back=True)
