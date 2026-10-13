"""Whatever subset of applications is installed, CORE works with exactly that."""

from __future__ import annotations

import json

from corelib import jsonc
from corelib.surfaces.hermes import display_skin
from tests.support import SandboxTest


def applied_keys(box) -> list[str]:
    return json.loads(box.paths.state_file.read_text())["surfaces"]


class Matrix(SandboxTest):
    def test_empty_machine_changes_nothing(self):
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing to change", out)
        self.assertEqual(self.box.tree(), {})

    def test_only_ghostty_with_no_config_file(self):
        config = self.box.ghostty(None)
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 0, out)
        self.assertEqual(config.read_text(), "theme = node-optical\n")
        self.assertTrue((self.box.home / ".config/ghostty/themes/node-optical").is_file())
        self.assertFalse((self.box.home / ".config/ghostty/themes/node-optical").is_symlink())
        self.assertEqual(applied_keys(self.box), ["ghostty"])

    def test_only_terminal(self):
        self.box.terminal()
        code, out = self.box.cli("apply", "shell", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("Terminal", out)
        self.assertIn("Would apply: Terminal", out)

    def test_only_vscode_with_no_settings_file(self):
        settings = self.box.vscode(None)
        code, out = self.box.cli("apply", "shell")
        self.assertEqual(code, 0, out)
        data = jsonc.load(settings.read_text())
        self.assertEqual(data["workbench.colorTheme"], "CORE Light")
        self.assertIn("[CORE Light]", data["workbench.colorCustomizations"])
        self.assertTrue(self.box.fake.called("code", "--install-extension"))
        self.assertIn("Reload Window", out)

    def test_only_hermes(self):
        config = self.box.hermes(desktop=False)
        code, out = self.box.cli("apply", "neon")
        self.assertEqual(code, 0, out)
        self.assertEqual(display_skin(config.read_text()), "node-neon")
        self.assertIn("compact: true", config.read_text())
        self.assertNotIn("BLOCKED", out)

    def test_only_obsidian(self):
        vaults = self.box.obsidian({"a1": {"path": "Notes", "appearance": {"baseFontSize": 15}}})
        code, out = self.box.cli("apply", "glacier")
        self.assertEqual(code, 0, out)
        data = json.loads((vaults["a1"] / ".obsidian/appearance.json").read_text())
        self.assertEqual((data["cssTheme"], data["theme"], data["baseFontSize"]), ("CORE - GLACIER", "moonstone", 15))

    def test_combination_ghostty_vscode(self):
        self.box.ghostty("font-size = 13\n")
        self.box.vscode('{\n    "editor.fontSize": 14\n}\n')
        code, out = self.box.cli("apply", "augment")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(applied_keys(self.box)), ["ghostty", "vscode"])

    def test_everything_installed(self):
        self.box.ghostty("")
        self.box.terminal()
        self.box.starship()
        self.box.fastfetch()
        self.box.vscode("{}")
        self.box.hermes()
        self.box.obsidian({"v": {"path": "Vault", "appearance": {}}})
        self.box.chatgpt()
        self.box.claude()
        code, out = self.box.cli("apply", "ember")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(applied_keys(self.box)),
                         sorted(["ghostty", "terminal", "starship", "fastfetch", "vscode", "hermes", "obsidian"]))
        self.assertIn("ChatGPT", out)
        self.assertIn("Claude", out)
        self.assertIn("not managed", out)  # macOS appearance is opt-in only
        self.assertFalse(self.box.fake.called("osascript"))


class ReadOnlyCommands(SandboxTest):
    def setUp(self):
        super().setUp()
        self.box.ghostty("theme = Dracula\n")
        self.box.vscode('{"workbench.colorTheme": "Monokai"}')
        self.box.hermes()
        self.box.obsidian({"v": {"path": "V", "appearance": {"cssTheme": "Minimal"}}})

    def test_dry_run_writes_nothing(self):
        before = self.box.tree()
        code, out = self.box.cli("apply", "optical", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(before, self.box.tree())
        self.assertFalse(self.box.fake.called("code"))

    def test_doctor_writes_nothing(self):
        before = self.box.tree()
        code, out = self.box.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertEqual(before, self.box.tree())
        for label in ("Ghostty", "Terminal", "VS Code", "Hermes", "Obsidian", "ChatGPT", "Claude", "macOS appearance"):
            self.assertIn(label, out)

    def test_status_writes_nothing(self):
        before = self.box.tree()
        self.box.cli("status")
        self.assertEqual(before, self.box.tree())

    def test_status_does_not_claim_the_unmanaged_opt_in(self):
        self.box.cli("apply", "optical")
        code, out = self.box.cli("status")
        macos = next(line for line in out.splitlines() if "macOS appearance" in line)
        self.assertIn("not managed", macos)
        self.assertIn("OPT-IN", macos)
        self.assertNotIn("ACTIVE", macos)


class Preview(SandboxTest):
    def test_preview_restores_everything_exactly(self):
        ghostty = self.box.ghostty("theme = Dracula\nfont-size = 13\n")
        settings = self.box.vscode('{\n    // mine\n    "workbench.colorTheme": "Monokai",\n    "editor.fontSize": 14\n}\n',
                                   extension=True)
        hermes = self.box.hermes()
        vaults = self.box.obsidian({"v": {"path": "V", "appearance": {"cssTheme": "Minimal", "accentColor": "#ff0000"}}})
        before = self.box.tree()
        seen = {}

        def look():
            seen["ghostty"] = ghostty.read_text()
            seen["vscode"] = jsonc.load(settings.read_text())["workbench.colorTheme"]

        code, out = self.box.cli("preview", "optical", wait=look)
        self.assertEqual(code, 0, out)
        self.assertIn("theme = node-optical", seen["ghostty"])
        self.assertEqual(seen["vscode"], "CORE Dark")
        self.assertIn("Restored and verified", out)
        after = self.box.tree()
        for name in ("Library/Application Support/Code/User/settings.json", ".config/ghostty/config", ".hermes/config.yaml"):
            self.assertEqual(before[name], after[name], name)
        appearance = json.loads((vaults["v"] / ".obsidian/appearance.json").read_text())
        self.assertEqual(appearance, {"cssTheme": "Minimal", "accentColor": "#ff0000"})
        self.assertFalse(self.box.paths.journal.exists())
        self.assertFalse(self.box.paths.state_file.exists())  # a preview is never "applied"
