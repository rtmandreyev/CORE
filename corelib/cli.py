"""The `core` command.

    core                                  the interactive app (in a terminal)
    core list | status | doctor
    core build <theme>
    core preview <theme> [app...] [--dry-run]
    core apply <theme> [app...] [--dry-run]
    core restore                          undo an interrupted apply/preview
    core repair [app] [--dry-run]
    core theme new <name> --from <theme> [--name "Display Name"]
    core export <theme> chatgpt [--copy]

The interactive app calls these same functions; there is no second
implementation of apply, preview or repair.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Callable

from corelib import effects, repair as repair_module, transaction
from corelib import surfaces as registry
from corelib.build import BuildError, build, vscode_vsix
from corelib.paths import Paths
from corelib.surfaces import Context
from corelib.theme import BUILTIN_THEMES, NAME_RULE, Theme, ThemeError, discover, is_valid_name, load, theme_path
from corelib.version import VERSION

EXIT_OK, EXIT_FAILED, EXIT_ATTENTION = 0, 1, 3

STYLE = {"ACTIVE": "32", "READY": "36", "REPAIRABLE": "33", "BLOCKED": "31", "N/A": "2", "EXPORT": "35", "OPT-IN": "35", "INFO": "2"}


def paint(text: str, code: str, stream=None) -> str:
    stream = stream or sys.stdout
    if not code or os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return text
    return f"\033[{code}m{text}\033[0m"


def state_cell(state: str) -> str:
    return paint(f"{state:<11}", STYLE.get(state, ""))


class CliError(Exception):
    pass


def context(out: Callable[[str], None] = print) -> Context:
    return Context(Paths.from_env(), report=out)


def require_no_journal(ctx: Context, out) -> None:
    journal = transaction.read_journal(ctx.paths)
    if journal is None:
        return
    if transaction.journal_owner_alive(journal):
        raise CliError(f"another CORE run (pid {journal.get('pid')}) is in progress")
    out(f"An earlier {journal.get('kind', 'run')} of {journal.get('theme', '?')} was interrupted.")
    out("Run `core restore` to put everything back first. No changes made.")
    raise SystemExit(EXIT_ATTENTION)


def get_theme(ctx: Context, theme_id: str) -> Theme:
    try:
        return load(ctx.paths.root, theme_id)
    except ThemeError as exc:
        raise CliError(str(exc)) from None


# ── read-only commands ──


def cmd_list(args, out=print) -> int:
    ctx = context(out)
    active = transaction.read_state(ctx.paths).get("theme")
    out("CORE // THEMES")
    out("")
    for info in discover(ctx.paths.root):
        marker = "●" if info.id == active else " "
        origin = "built-in" if info.id in BUILTIN_THEMES else "yours"
        out(f"  {marker} {info.id:<12} {info.mode:<6} {origin:<9} {info.name}")
    out("")
    out("  core preview <theme>   ·   core apply <theme>   ·   core theme new <name> --from <theme>")
    return EXIT_OK


def preflight_lines(the_plan: transaction.Plan) -> list[str]:
    lines = []
    for key, state, detail, warnings in the_plan.rows:
        label = registry.BY_KEY[key].label
        lines.append(f"  {label:<18} {state_cell(state)} {detail}".rstrip())
        for warning in warnings:
            lines.append(f"  {'':<18} {paint('! ' + warning, '33')}")
    return lines


def cmd_status(args, out=print) -> int:
    ctx = context(out)
    theme = repair_module.current_theme(ctx)
    out("CORE // STATUS")
    out("")
    journal = transaction.read_journal(ctx.paths)
    if journal:
        out(paint(f"  An interrupted {journal.get('kind', 'run')} is waiting: run `core restore`.", "33"))
        out("")
    if theme is None:
        out("  No theme applied by CORE yet. `core list` shows the themes; `core apply <theme>` applies one.")
        return EXIT_OK
    out(f"  Active theme   {theme.name}  ({theme.mode})")
    out("")
    the_plan = transaction.plan(ctx, theme, None)  # the same "all applications" view apply uses
    for line in preflight_lines(the_plan):
        out(line)
    return EXIT_OK


def cmd_doctor(args, out=print) -> int:
    return repair_module.doctor(context(out), out, keys=args.apps or None)


def cmd_build(args, out=print) -> int:
    ctx = context(out)
    theme = get_theme(ctx, args.theme)
    try:
        built = build(ctx.paths, theme)
        vsix = vscode_vsix(ctx.paths)
    except BuildError as exc:
        raise CliError(str(exc)) from None
    out(f"Built {theme.name} into {built.root}")
    for path in sorted(p for p in built.root.rglob("*") if p.is_file()):
        out(f"  {path.relative_to(built.root)}")
    out(f"VS Code base extension: {vsix}")
    return EXIT_OK


# ── apply / preview ──


def run_transaction(theme_id: str, apps: list[str], *, preview: bool, dry_run: bool, out=print, wait=None) -> int:
    ctx = context(out)
    if not dry_run:
        require_no_journal(ctx, out)
    theme = get_theme(ctx, theme_id)
    try:
        build(ctx.paths, theme)
    except BuildError as exc:
        raise CliError(str(exc)) from None
    try:
        the_plan = transaction.plan(ctx, theme, apps or None)
    except KeyError as exc:
        raise CliError(str(exc).strip("'\"")) from None

    out(f"CORE // {'PREVIEW' if preview else 'APPLY'}   {theme.name}  ({theme.mode})")
    out("")
    for line in preflight_lines(the_plan):
        out(line)
    out("")

    if "chatgpt" in [key for key, _ in the_plan.skipped] and "chatgpt" in (apps or []):
        out("  ChatGPT: paste this in ChatGPT → Settings → Appearance → Import"
            f" (with ChatGPT in {theme.mode} mode):")
        out("  " + registry.BY_KEY["chatgpt"].import_string(ctx, theme))
        out("")

    if the_plan.blocked:
        for key, reason in the_plan.blocked:
            out(paint(f"  Cannot change {registry.BY_KEY[key].label}: {reason}", "31"))
        others = [key for key in the_plan.selected]
        if others:
            out(f"  To continue without it: core {'preview' if preview else 'apply'} {theme.id} {' '.join(others)}")
        out("No changes made.")
        return EXIT_FAILED

    if not the_plan.selected:
        out("Nothing to change on this machine. No changes made.")
        return EXIT_OK

    if dry_run:
        out(f"  Would {'preview' if preview else 'apply'}: " + ", ".join(registry.BY_KEY[k].label for k in the_plan.selected))
        out("Dry run: no changes made.")
        return EXIT_OK

    return transaction.execute(ctx, theme, the_plan, preview=preview, out=out, wait=wait)


def cmd_apply(args, out=print) -> int:
    return run_transaction(args.theme, args.apps, preview=False, dry_run=args.dry_run, out=out)


def cmd_preview(args, out=print) -> int:
    return run_transaction(args.theme, args.apps, preview=True, dry_run=args.dry_run, out=out)


def cmd_restore(args, out=print) -> int:
    return transaction.recover(context(out), out)


def cmd_repair(args, out=print) -> int:
    ctx = context(out)
    if not args.dry_run:
        require_no_journal(ctx, out)
    try:
        return repair_module.repair(ctx, [args.app] if args.app else None, dry_run=args.dry_run, out=out)
    except KeyError as exc:
        raise CliError(str(exc).strip("'\"")) from None


# ── authoring and export ──


def scaffold(root, name: str, source: str, display: str | None = None) -> str:
    """Copy one canonical theme file under a new identity. Returns the path."""

    if not is_valid_name(name):
        raise CliError(f"invalid theme name {name!r}: {NAME_RULE}")
    source_path = theme_path(root, source)
    if not source_path.is_file():
        raise CliError(f"unknown theme {source!r}")
    target = theme_path(root, name)
    if target.parent.exists():
        raise CliError(f"themes/{name} already exists")

    text = source_path.read_text()
    meta_end = re.search(r"(?m)^\[(?!theme\])", text)
    head, tail = (text[: meta_end.start()], text[meta_end.start() :]) if meta_end else (text, "")
    display = display or name.upper()
    head = re.sub(r'(?m)^id\s*=\s*".*"$', f'id = "{name}"', head, count=1)
    head = re.sub(r'(?m)^name\s*=\s*".*"$', f'name = "{display}"', head, count=1)
    head = re.sub(r'(?m)^(slug|brand|author)\s*=.*\n', "", head)
    head = re.sub(r'(?m)^description\s*=\s*".*"$', f'description = "Started from {source}; describe your theme here."', head, count=1)
    # Built-in identity that must not follow a user's theme.
    tail = re.sub(rf'(?m)^header\s*=\s*"{source.upper()}"\n', "", tail)
    tail = re.sub(r'(?m)^vault_theme\s*=.*\n', "", tail)

    banner = (f"# {display} — your CORE theme, started from `{source}`.\n"
              f"# This file is the whole theme. Edit it, then `core preview {name}`.\n\n")
    target.parent.mkdir(parents=True)
    target.write_text(banner + head + tail)
    try:
        Theme(target)
    except ThemeError as exc:  # never leave a broken scaffold behind
        target.unlink()
        target.parent.rmdir()
        raise CliError(f"could not scaffold: {exc}") from None
    return str(target)


def cmd_theme_new(args, out=print) -> int:
    ctx = context(out)
    path = scaffold(ctx.paths.root, args.name, args.from_theme, args.display)
    out(f"Created {path}")
    out("")
    out("  This one file is your theme: palette, roles, terminal colours and app mappings.")
    out(f"  Next:  core preview {args.name}    then    core apply {args.name}")
    return EXIT_OK


def cmd_export(args, out=print) -> int:
    ctx = context(out)
    if args.target != "chatgpt":
        raise CliError("only `chatgpt` can be exported")
    theme = get_theme(ctx, args.theme)
    build(ctx.paths, theme)
    text = registry.BY_KEY["chatgpt"].import_string(ctx, theme)
    if args.copy:
        result = effects.current().run(["pbcopy"], input=text, timeout=10)
        if not result.ok:
            raise CliError(f"could not copy to the clipboard ({result.text or result.returncode})")
        out(f"Copied the {theme.name} ChatGPT theme to the clipboard.")
    else:
        out(text)
    out("")
    out(f"In ChatGPT: switch Appearance to {theme.mode.capitalize()}, then Settings → Appearance → Import"
        " and paste. CORE never edits ChatGPT's own settings.")
    return EXIT_OK


# ── entry point ──


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="core",
        description="Define a visual theme once; preview or apply it across your Mac's apps.",
        epilog="Run `core` with no arguments for the interactive app. `core <theme>` is short for `core apply <theme>`.",
    )
    p.add_argument("--version", action="version", version=f"core {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("list", help="list themes").set_defaults(func=cmd_list)
    sub.add_parser("status", help="the active theme and each application's state").set_defaults(func=cmd_status)
    d = sub.add_parser("doctor", help="read-only diagnosis of every application")
    d.add_argument("apps", nargs="*")
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("build", help="compile a theme into build/ (touches no application)")
    b.add_argument("theme")
    b.set_defaults(func=cmd_build)

    for name, func, text in (("preview", cmd_preview, "apply temporarily; Enter restores"),
                             ("apply", cmd_apply, "apply and keep (all-or-nothing)")):
        c = sub.add_parser(name, help=text)
        c.add_argument("theme")
        c.add_argument("apps", nargs="*", help=f"limit to: {', '.join(registry.SELECTABLE_KEYS)}")
        c.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
        c.set_defaults(func=func)

    sub.add_parser("restore", help="undo an interrupted apply or preview").set_defaults(func=cmd_restore)

    r = sub.add_parser("repair", help="restore CORE-owned files after an app update")
    r.add_argument("app", nargs="?", help=f"one of: {', '.join(registry.REPAIR_KEYS)}")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_repair)

    t = sub.add_parser("theme", help="create themes")
    tsub = t.add_subparsers(dest="theme_command", required=True)
    n = tsub.add_parser("new", help="start a theme from an existing one")
    n.add_argument("name")
    n.add_argument("--from", dest="from_theme", default="shell")
    n.add_argument("--name", dest="display", help='display name (default: NAME in capitals)')
    n.set_defaults(func=cmd_theme_new)

    e = sub.add_parser("export", help="export an import string for ChatGPT")
    e.add_argument("theme")
    e.add_argument("target", choices=["chatgpt"])
    e.add_argument("--copy", action="store_true", help="copy to the clipboard with pbcopy")
    e.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from corelib import tui
            return tui.run()
        parser().print_help()
        return EXIT_OK
    if argv[0] in BUILTIN_THEMES:  # `core optical` is shorthand for `core apply optical`
        argv = ["apply", *argv]
    args = parser().parse_args(argv)
    if not getattr(args, "func", None):
        parser().print_help()
        return EXIT_OK
    try:
        return int(args.func(args) or 0)
    except CliError as exc:
        print(f"core: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except effects.EffectBlocked as exc:
        print(f"core: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except SystemExit as exc:  # a command that refused with a specific exit code
        return exc.code if isinstance(exc.code, int) else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
