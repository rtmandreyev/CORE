"""Theme authoring, the CLI process boundary and the interactive app."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib

from corelib.theme import load
from corelib.tui import App, AppRow
from tests.support import REPO, SandboxTest


class Authoring(SandboxTest):
    def test_new_theme_is_one_file_with_its_own_identity(self):
        code, out = self.box.cli("theme", "new", "solar", "--from", "shell")
        self.assertEqual(code, 0, out)
        directory = self.box.root / "themes/solar"
        self.assertEqual([p.name for p in directory.iterdir()], ["theme.toml"])
        theme = load(self.box.root, "solar")
        self.assertEqual((theme.name, theme.brand, theme.slug, theme.mode), ("SOLAR", "CORE", "core-solar", "light"))
        self.assertEqual(theme.obsidian_name, "CORE - SOLAR")
        self.assertNotIn("NODE", theme.path.read_text().split("[palette]")[0])

    def test_edit_one_value_build_preview_apply(self):
        self.box.cli("theme", "new", "solar", "--from", "optical", "--name", "Solar Flare")
        path = self.box.root / "themes/solar/theme.toml"
        path.write_text(path.read_text().replace('cyan = "#00CFE8"', 'cyan = "#FF8800"'))
        config = self.box.ghostty("")
        code, out = self.box.cli("build", "solar")
        self.assertEqual(code, 0, out)
        self.assertIn("#FF8800", (self.box.paths.build_dir / "solar/ghostty/core-solar").read_text())
        code, out = self.box.cli("apply", "solar")
        self.assertEqual(code, 0, out)
        self.assertEqual(config.read_text(), "theme = core-solar\n")
        starship = (self.box.paths.build_dir / "solar/starship/starship.toml").read_text()
        self.assertIn("[ CORE]", starship)

    def test_refuses_bad_names_and_existing_themes(self):
        self.assertEqual(self.box.cli("theme", "new", "Bad Name", "--from", "shell")[0], 1)
        self.assertEqual(self.box.cli("theme", "new", "shell", "--from", "optical")[0], 1)
        self.assertEqual(self.box.cli("theme", "new", "x", "--from", "nope")[0], 1)

    def test_invalid_theme_is_reported_not_crashed(self):
        self.box.cli("theme", "new", "broken", "--from", "shell")
        path = self.box.root / "themes/broken/theme.toml"
        path.write_text(path.read_text().replace('background = "black"', 'background = "nothing"', 1))
        code, out = self.box.cli("build", "broken")
        self.assertEqual(code, 1)
        self.assertIn("nothing", out)


class ProcessBoundary(SandboxTest):
    def test_cli_runs_as_a_process_with_noninteractive_stdin(self):
        self.box.ghostty("theme = Dracula\n")
        result = self.box.run("apply", "optical")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["ps", "-A", "-o", "comm="], self.box.subprocess_calls())

    def test_bare_core_without_a_terminal_prints_help(self):
        result = self.box.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: core", result.stdout)

    def test_aliases_delegate_to_apply(self):
        result = self.box.run("optical", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CORE // APPLY", result.stdout)

    def test_help_lists_commands_cleanly(self):
        result = self.box.run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("SUPPRESS", result.stdout)
        for command in ("doctor", "preview", "apply", "repair", "export"):
            self.assertIn(command, result.stdout)

    def test_launcher_finds_python(self):
        env = self.box.env()
        env["CORE_PYTHON"] = sys.executable
        result = subprocess.run([str(REPO / "bin/core"), "--version"], env=env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core 0.1.0", result.stdout)

    def test_install_script_links_idempotently_and_never_clobbers(self):
        bin_dir = self.box.tmp / "localbin"
        env = dict(self.box.env(), CORE_BIN_DIR=str(bin_dir), CORE_PYTHON=sys.executable)
        run = lambda: subprocess.run(["sh", str(REPO / "install.sh")], env=env, capture_output=True, text=True,
                                     stdin=subprocess.DEVNULL, timeout=30)
        first, second = run(), run()
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr)
        self.assertEqual(os.readlink(bin_dir / "core"), str(REPO / "bin/core"))
        self.assertIn("Add", first.stdout)  # the temp bin dir is not on PATH: it says so, edits nothing
        (bin_dir / "core").unlink()
        (bin_dir / "core").write_text("mine")
        self.assertEqual(run().returncode, 1)
        self.assertEqual((bin_dir / "core").read_text(), "mine")

    def test_version_is_the_release_version(self):
        result = self.box.run("--version")
        self.assertEqual(result.stdout.strip(), "core 0.1.0")

    def test_repair_dry_run_is_read_only(self):
        self.box.hermes()
        self.box.ghostty("theme = node-optical\n")
        before = self.box.tree()
        result = self.box.run("repair", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.box.tree())


class InteractiveApp(SandboxTest):
    def app(self):
        from corelib.theme import discover
        return App(discover(self.box.root), "optical", color=False)

    def test_home_lists_themes_and_actions(self):
        text = "\n".join(self.app().render(80))
        for label in ("Active theme", "● OPTICAL", "1  ◆ OPTICAL", "6    NEON", "Apply", "Preview", "Doctor", "Repair",
                      "New theme", "Help", "Quit"):
            self.assertIn(label, text)

    def test_pick_theme_then_apply_all(self):
        app = self.app()
        app.handle_key("2")
        self.assertEqual(app.theme, "shell")
        action = app.handle_key("\r")  # cursor jumped to Apply
        self.assertIsNone(action)
        self.assertEqual(app.screen, "scope")
        action = app.handle_key("\r")
        self.assertEqual((action.kind, action.theme, action.apps), ("apply", "shell", []))

    def test_select_applications(self):
        app = self.app()
        app.cursor = len(app.themes) + 1  # Preview
        app.handle_key("\r")
        app.handle_key("j")
        self.assertEqual(app.handle_key("\r").kind, "load-rows")
        app.rows = [AppRow("ghostty", "Ghostty", "READY", "", True), AppRow("vscode", "VS Code", "N/A", "absent", False),
                    AppRow("chatgpt", "ChatGPT", "EXPORT", "export/import", False)]
        app.handle_key("j")
        app.handle_key(" ")  # N/A cannot be checked
        self.assertFalse(app.rows[1].checked)
        app.handle_key("j")
        app.handle_key(" ")
        app.handle_key("j")
        action = app.handle_key("\r")
        self.assertEqual((action.kind, action.apps), ("preview", ["ghostty", "chatgpt"]))

    def test_quit(self):
        app = self.app()
        app.handle_key("q")
        self.assertTrue(app.quit)

    def test_ctrl_c_goes_home_then_quits(self):
        app = self.app()
        app.cursor = len(app.themes)
        app.handle_key("\r")
        self.assertEqual(app.screen, "scope")
        app.handle_key("\x03")
        self.assertEqual((app.screen, app.quit), ("home", False))
        app.handle_key("\x03")
        self.assertTrue(app.quit)

    def test_short_terminal_fits(self):
        lines = self.app().render(60, 18)
        self.assertLessEqual(len(lines), 18)
        self.assertTrue(any("OPTICAL" in line for line in lines))

    def test_repair_action(self):
        app = self.app()
        app.cursor = len(app.themes) + 3
        self.assertEqual(app.handle_key("\r").kind, "repair")
