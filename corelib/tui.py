"""The interactive CORE app (bare `core` in a terminal).

Two rules keep it small and testable:

* `App.render(width)` returns lines; it never prints. `App.handle_key(key)`
  changes state and may return an Action; it never touches the terminal.
* An Action runs one of the ordinary CLI functions (`run_transaction`,
  `repair`, `doctor`, `scaffold`, …) in normal terminal mode, so the app can
  never behave differently from the one-shot commands.

Keys: ↑/↓ or j/k move, Enter chooses, 1–9 picks a theme, Space toggles an
application, a/n select all/none, Esc or q goes back; q or Ctrl-C on the home
screen quits (Ctrl-C elsewhere goes home). The screen redraws on resize.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys
from dataclasses import dataclass, field
from typing import Callable

from corelib import surfaces as registry
from corelib import transaction
from corelib.paths import Paths
from corelib.surfaces import NA, Context
from corelib.theme import ThemeInfo, discover
from corelib.version import VERSION

KEYS = {
    "\x1b[A": "up", "\x1bOA": "up", "\x1b[B": "down", "\x1bOB": "down",
    "\r": "enter", "\n": "enter", " ": "space", "\x1b": "escape", "\x7f": "backspace", "\x03": "ctrl-c",
    "k": "up", "j": "down",
}
ACTIONS = ("Apply", "Preview", "Doctor", "Repair", "New theme", "Help", "Quit")
DIM, BOLD, ACCENT, OK, WARN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[0m"
STATE_STYLE = {"ACTIVE": OK, "READY": ACCENT, "REPAIRABLE": WARN, "BLOCKED": "\033[31m"}


@dataclass
class Action:
    kind: str  # apply, preview, doctor, repair, new-theme, export
    theme: str = ""
    apps: list[str] = field(default_factory=list)


@dataclass
class AppRow:
    key: str
    label: str
    state: str
    note: str
    checked: bool


class App:
    def __init__(self, themes: list[ThemeInfo], active: str | None, *, color: bool = True) -> None:
        self.themes = themes
        self.active = active
        self.color = color
        self.screen = "home"
        self.cursor = 0
        self.theme = active if active in [t.id for t in themes] else (themes[0].id if themes else "")
        self.mode = "apply"
        self.rows: list[AppRow] = []
        self.message = ""
        self.quit = False

    # ── helpers ──

    def paint(self, text: str, style: str) -> str:
        return f"{style}{text}{RESET}" if self.color and style else text

    def home_items(self) -> list[tuple[str, str]]:
        return [("theme", info.id) for info in self.themes] + [("action", name) for name in ACTIONS]

    def info(self, theme_id: str) -> ThemeInfo | None:
        return next((info for info in self.themes if info.id == theme_id), None)

    def short(self, theme_id: str) -> str:
        info = self.info(theme_id)
        return info.name.split("//")[-1].strip() if info else theme_id

    # ── rendering ──

    def render(self, width: int = 80, height: int = 0) -> list[str]:
        lines = self._render(width)
        # Short terminals: drop spacing first, then the hint, then cut.
        while height and len(lines) > height and "" in lines[1:]:
            lines.remove("")
        if height and len(lines) > height:
            lines = lines[: height - 1] + ["  " + self.paint("… enlarge the window to see everything", DIM)]
        return lines

    def _render(self, width: int) -> list[str]:
        width = max(40, min(width, 88))
        title = self.paint("CORE", BOLD) + " " * (width - 4 - len(VERSION) - 4) + self.paint(VERSION, DIM)
        lines = ["", "  " + title, "  " + self.paint("─" * (width - 4), DIM), ""]
        lines += getattr(self, f"render_{self.screen.replace('-', '_')}")(width)
        if self.message:
            lines += ["", "  " + self.paint(self.message, WARN)]
        lines += ["", "  " + self.paint(self.hint(), DIM)]
        return lines

    def hint(self) -> str:
        if self.screen == "home":
            return "↑↓ move · Enter choose · 1–9 theme · q quit"
        if self.screen == "select":
            return "↑↓ move · Space toggle · a all · n none · Enter run · Esc back"
        return "↑↓ move · Enter choose · Esc back"

    def mark(self, index: int) -> str:
        return self.paint("›", ACCENT) if index == self.cursor else " "

    def render_home(self, width: int) -> list[str]:
        lines = ["  Active theme"]
        if self.active and self.info(self.active):
            info = self.info(self.active)
            lines.append(f"    {self.paint('●', OK)} {self.short(info.id):<12} {self.paint(info.mode, DIM)}")
        else:
            lines.append(self.paint("    none yet — choose a theme, then Apply", DIM))
        lines += ["", "  Themes"]
        for index, (kind, value) in enumerate(self.home_items()):
            if kind == "theme":
                info = self.info(value)
                chosen = self.paint("◆", ACCENT) if value == self.theme else " "
                number = str(index + 1) if index < 9 else " "
                lines.append(f"  {self.mark(index)} {number}  {chosen} {self.short(value):<12} {self.paint(info.mode, DIM)}")
            else:
                if value == ACTIONS[0]:
                    lines += ["", f"  Actions   {self.paint('for ' + self.short(self.theme), DIM)}"]
                lines.append(f"  {self.mark(index)}    {value}")
        return lines

    def render_scope(self, width: int) -> list[str]:
        verb = "Apply" if self.mode == "apply" else "Preview"
        return [
            f"  {verb} {self.paint(self.short(self.theme), BOLD)} to",
            "",
            f"  {self.mark(0)} All available applications",
            f"  {self.mark(1)} Select applications…",
        ]

    def render_select(self, width: int) -> list[str]:
        verb = "Apply" if self.mode == "apply" else "Preview"
        lines = [f"  {verb} {self.paint(self.short(self.theme), BOLD)} to", ""]
        for index, row in enumerate(self.rows):
            box = "[x]" if row.checked else "[ ]"
            if row.state == NA:
                box = self.paint("[-]", DIM)
            room = max(10, width - 44)
            detail = row.note if len(row.note) <= room else row.note[: room - 1].rstrip() + "…"
            state = self.paint(f"{row.state:<10}", STATE_STYLE.get(row.state, DIM))
            lines.append(f"  {self.mark(index)} {box} {row.label:<17} {state} {self.paint(detail, DIM)}".rstrip())
        lines += ["", f"  {self.mark(len(self.rows))} {self.paint(verb, BOLD)} {sum(r.checked for r in self.rows)} application(s)"]
        return lines

    def render_help(self, width: int) -> list[str]:
        return [
            "  CORE applies one theme across the apps on this Mac.",
            "",
            "  Apply      change your apps and keep it (all-or-nothing)",
            "  Preview    change them temporarily; Enter puts everything back",
            "  Doctor     read-only diagnosis of each application",
            "  Repair     restore CORE's own files after an app update (asks first)",
            "  New theme  copy a theme into themes/<name>/theme.toml to edit",
            "",
            "  One-shot commands: core list · status · doctor · apply · preview",
            "                     repair · restore · export <theme> chatgpt",
            "",
            f"  {self.mark(0)} Back",
        ]

    # ── keys ──

    def handle_key(self, key: str) -> Action | None:
        self.message = ""
        key = KEYS.get(key, key)
        if key == "ctrl-c":
            if self.screen == "home":
                self.quit = True
            else:
                self.go("home", len(self.themes))
            return None
        if self.screen == "home":
            return self.key_home(key)
        if self.screen == "scope":
            return self.key_scope(key)
        if self.screen == "select":
            return self.key_select(key)
        if key in ("enter", "escape", "q"):
            self.go("home")
        return None

    def go(self, screen: str, cursor: int = 0) -> None:
        self.screen, self.cursor = screen, cursor

    def move(self, key: str, count: int) -> None:
        if key == "up":
            self.cursor = (self.cursor - 1) % count
        elif key == "down":
            self.cursor = (self.cursor + 1) % count

    def key_home(self, key: str) -> Action | None:
        items = self.home_items()
        if key in ("up", "down"):
            self.move(key, len(items))
            return None
        if key.isdigit() and 1 <= int(key) <= min(9, len(self.themes)):
            self.theme = self.themes[int(key) - 1].id
            self.cursor = len(self.themes)  # jump to Apply
            return None
        if key == "q":
            self.quit = True
            return None
        if key != "enter":
            return None
        kind, value = items[self.cursor]
        if kind == "theme":
            self.theme = value
            self.cursor = len(self.themes)
            return None
        if value in ("Apply", "Preview"):
            self.mode = value.lower()
            self.go("scope")
        elif value == "Doctor":
            return Action("doctor")
        elif value == "Repair":
            return Action("repair")
        elif value == "New theme":
            return Action("new-theme", self.theme)
        elif value == "Help":
            self.go("help")
        elif value == "Quit":
            self.quit = True
        return None

    def key_scope(self, key: str) -> Action | None:
        if key in ("escape", "q"):
            self.go("home", len(self.themes))
        elif key in ("up", "down"):
            self.move(key, 2)
        elif key == "enter" and self.cursor == 0:
            self.go("home", len(self.themes))
            return Action(self.mode, self.theme)
        elif key == "enter":
            self.go("select")
            return Action("load-rows", self.theme)
        return None

    def key_select(self, key: str) -> Action | None:
        count = len(self.rows) + 1
        if key in ("escape", "q"):
            self.go("scope", 1)
        elif key in ("up", "down"):
            self.move(key, count)
        elif key in ("a", "n"):
            for row in self.rows:
                row.checked = key == "a" and row.state != NA
        elif key in ("space", "enter") and self.cursor < len(self.rows):
            row = self.rows[self.cursor]
            if row.state == NA:
                self.message = f"{row.label}: {row.note}"
            else:
                row.checked = not row.checked
        elif key == "enter":
            chosen = [row.key for row in self.rows if row.checked]
            if not chosen:
                self.message = "Select at least one application."
                return None
            self.go("home", len(self.themes))
            return Action(self.mode, self.theme, chosen)
        return None


def load_rows(ctx: Context, theme_id: str) -> list[AppRow]:
    from corelib.theme import load

    theme = load(ctx.paths.root, theme_id)
    opted_in = transaction.user_opt_ins(ctx.paths)
    rows = []
    for key in registry.SELECTABLE_KEYS:
        surface = registry.BY_KEY[key]
        try:
            status = surface.status(ctx, theme)
        except Exception as exc:
            status = registry.Status("BLOCKED", str(exc))
        note = {"export": "export/import", "optin": "system appearance"}.get(surface.kind, status.detail)
        default = surface.kind == "apply" or key in opted_in
        rows.append(AppRow(key, surface.label, status.state, note, default and status.state != NA))
    return rows


# ── the terminal loop ──


def read_key(fd: int, timeout: float | None = None) -> str | None:
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    data = os.read(fd, 1)
    if data == b"\x1b":
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            data += os.read(fd, 8)
    return data.decode(errors="replace")


def run() -> int:
    import termios
    import tty

    from corelib import cli

    def context() -> Context:
        return Context(Paths.from_env())

    ctx = context()
    app = App(discover(ctx.paths.root), transaction.read_state(ctx.paths).get("theme"),
              color=not os.environ.get("NO_COLOR"))
    if not app.themes:
        print("core: no themes found in", ctx.paths.themes_dir)
        return 1

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    resized = {"flag": False}

    def on_resize(_signum, _frame):
        resized["flag"] = True

    signal.signal(signal.SIGWINCH, on_resize)

    def draw() -> None:
        size = shutil.get_terminal_size((80, 24))
        sys.stdout.write("\033[H\033[2J" + "\r\n".join(app.render(size.columns, size.lines)))
        sys.stdout.flush()

    def outside(body: Callable[[], object]) -> None:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()
        try:
            body()
        except KeyboardInterrupt:
            print("\n  Cancelled.")
        except (cli.CliError, KeyError) as exc:
            print(f"\n  {exc}")
        except SystemExit:
            pass
        try:
            input("\n  Press Enter to return to CORE… ")
        except (EOFError, KeyboardInterrupt):
            pass
        sys.stdout.write("\033[?1049h\033[?25l")
        tty.setraw(fd)

    sys.stdout.write("\033[?1049h\033[?25l")
    tty.setraw(fd)
    try:
        while not app.quit:
            draw()
            key = None
            while key is None:
                key = read_key(fd, 0.25)
                if key is None and resized["flag"]:
                    resized["flag"] = False
                    draw()
            action = app.handle_key(key)
            if action is None:
                continue
            if action.kind == "load-rows":
                app.rows = load_rows(context(), action.theme)
            elif action.kind in ("apply", "preview"):
                outside(lambda: cli.run_transaction(action.theme, action.apps, preview=action.kind == "preview", dry_run=False))
                app.active = transaction.read_state(context().paths).get("theme")
            elif action.kind == "doctor":
                outside(lambda: cli.repair_module.doctor(context()))
            elif action.kind == "repair":
                def repair() -> None:
                    cli.repair_module.repair(context(), dry_run=True)
                    answer = input("\n  Run this repair now? [y/N] ").strip().lower()
                    if answer in ("y", "yes"):
                        print()
                        cli.repair_module.repair(context())
                outside(repair)
            elif action.kind == "new-theme":
                def create() -> None:
                    name = input(f"\n  New theme name (lowercase, e.g. solar), based on {action.theme}: ").strip()
                    if name:
                        path = cli.scaffold(context().paths.root, name, action.theme)
                        print(f"\n  Created {path}\n  Edit that one file, then preview it from the home screen.")
                outside(create)
                app.themes = discover(context().paths.root)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()
    return 0
