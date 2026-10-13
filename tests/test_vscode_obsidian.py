"""VS Code (settings-driven) and Obsidian (registry IDs, semantic restore)."""

from __future__ import annotations

import json

from corelib import jsonc
from corelib import surfaces as registry
from corelib.theme import load
from tests.support import SandboxTest

SETTINGS = """{
    // keep this comment
    "editor.fontSize": 14,
    "workbench.colorTheme": "Monokai",
    "workbench.colorCustomizations": {
        "[Monokai]": { "editor.background": "#000000" }
    },
}
"""


class VSCodeSettingsDriven(SandboxTest):
    def test_apply_owns_only_its_keys_and_preview_restores_bytes(self):
        settings = self.box.vscode(SETTINGS, extension=True)
        extensions = sorted(p.name for p in (self.box.home / ".vscode/extensions").iterdir())
        seen = {}
        code, out = self.box.cli("preview", "neon", "vscode", wait=lambda: seen.setdefault("text", settings.read_text()))
        self.assertEqual(code, 0, out)
        during = jsonc.load(seen["text"])
        self.assertEqual(during["workbench.colorTheme"], "CORE Dark")
        self.assertEqual(during["workbench.colorCustomizations"]["[Monokai]"], {"editor.background": "#000000"})
        self.assertIn("// keep this comment", seen["text"])
        self.assertEqual(settings.read_text(), SETTINGS)
        self.assertNotIn("Reload Window", out)  # colours are live once the base extension exists
        self.assertEqual(extensions, sorted(p.name for p in (self.box.home / ".vscode/extensions").iterdir()))
        self.assertFalse(self.box.fake.called("code"))

    def test_switching_themes_leaves_no_stale_core_block(self):
        """Regression: SHELL's [CORE Light] block stayed behind after applying OPTICAL."""

        settings = self.box.vscode(SETTINGS, extension=True)
        self.box.cli("apply", "shell", "vscode")
        self.box.cli("apply", "optical", "vscode")
        data = jsonc.load(settings.read_text())
        self.assertEqual(list(data["workbench.colorCustomizations"]), ["[Monokai]", "[CORE Dark]"])
        self.assertEqual(list(data["editor.tokenColorCustomizations"]), ["[CORE Dark]"])
        # A preview from here restores both blocks exactly.
        before = settings.read_text()
        self.box.cli("preview", "glacier", "vscode", wait=lambda: None)
        self.assertEqual(settings.read_text(), before)

    def test_preview_does_not_mint_extensions_and_custom_themes_need_none(self):
        self.box.vscode("{}", extension=True)
        self.box.cli("theme", "new", "solar", "--from", "shell")
        code, out = self.box.cli("apply", "solar", "vscode")
        self.assertEqual(code, 0, out)
        folders = [p.name for p in (self.box.home / ".vscode/extensions").iterdir() if p.is_dir()]
        self.assertEqual(folders, ["core.core-base-0.1.0"])

    def test_base_extension_installs_through_the_cli_only(self):
        self.box.vscode("{}")
        code, out = self.box.cli("apply", "optical", "vscode")
        self.assertEqual(code, 0, out)
        call = self.box.fake.called("code", "--install-extension")[0]
        self.assertTrue(call[2].endswith(".vsix"))
        self.assertIn("Reload Window", out)

    def test_missing_code_command_blocks_before_writing(self):
        settings = self.box.vscode("{}", cli=False)
        code, out = self.box.cli("apply", "optical", "vscode")
        self.assertEqual(code, 1, out)
        self.assertIn("`code` command was not found", out)
        self.assertEqual(settings.read_text(), "{}")

    def test_created_settings_file_is_removed_on_restore(self):
        settings = self.box.vscode(None, extension=True)
        code, out = self.box.cli("preview", "optical", "vscode", wait=lambda: None)
        self.assertEqual(code, 0, out)
        self.assertFalse(settings.exists())

    def test_doctor_reports_dangling_registry_entries(self):
        self.box.vscode("{}", extension=True)
        registry_file = self.box.home / ".vscode/extensions/extensions.json"
        entries = json.loads(registry_file.read_text())
        entries.append({"identifier": {"id": "example.stale-theme"}, "version": "0.1.0", "relativeLocation": "example.stale-theme-0.1.0"})
        registry_file.write_text(json.dumps(entries))
        code, out = self.box.cli("doctor", "vscode")
        self.assertIn("without a folder: example.stale-theme", out)

    def test_broken_base_extension_is_repairable(self):
        self.box.vscode('{"workbench.colorTheme": "CORE Dark"}', extension=True)
        import shutil
        shutil.rmtree(self.box.home / ".vscode/extensions/core.core-base-0.1.0")
        code, out = self.box.cli("repair", "vscode", "--dry-run")
        self.assertIn("REPAIRABLE", out)
        code, out = self.box.cli("repair", "vscode")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.box.fake.called("code", "--install-extension"))


VAULTS = {
    "id-notes": {"path": "Notes", "open": True, "appearance": {"cssTheme": "Minimal", "accentColor": "#123456", "baseFontSize": 15}},
    "id-work-docs": {"path": "Work/docs", "open": False, "appearance": {}},
    "id-core-docs": {"path": "CORE/core/docs", "open": True, "appearance": {}},
    "id-x-docs": {"path": "x/docs", "open": False, "appearance": None},
    "id-missing": {"path": "Gone", "init": False},
}


class ObsidianRegistry(SandboxTest):
    def setUp(self):
        super().setUp()
        self.vaults = self.box.obsidian(VAULTS)

    def cli_calls(self):
        return [c for c in self.box.fake.calls if c[0] == "obsidian-cli" and c[1] != "version"]

    def test_closed_app_writes_files_only(self):
        code, out = self.box.cli("apply", "optical", "obsidian")
        self.assertEqual(code, 0, out)
        for vault_id in ("id-notes", "id-work-docs", "id-core-docs", "id-x-docs"):
            data = json.loads((self.vaults[vault_id] / ".obsidian/appearance.json").read_text())
            self.assertEqual((data["cssTheme"], data["theme"]), ("CORE - OPTICAL", "obsidian"), vault_id)
        self.assertEqual(self.cli_calls(), [])
        self.assertIn("update when reopened", out)

    def test_running_app_is_driven_by_registry_id_and_only_open_vaults(self):
        self.box.obsidian_cli(enabled=True)
        self.box.respond(["obsidian-cli"], contains="theme:set", stdout="Switched to: CORE - OPTICAL")
        self.box.respond(["obsidian-cli"], contains=" theme", stdout="CORE - OPTICAL")
        code, out = self.box.cli("apply", "optical", "obsidian")
        self.assertEqual(code, 0, out)
        targets = {call[1] for call in self.cli_calls()}
        self.assertEqual(targets, {"vault=id-notes", "vault=id-core-docs"})
        self.assertFalse(any("vault=docs" in " ".join(c) for c in self.box.fake.calls))
        self.assertIn("switched live", out)

    def test_cli_disabled_is_reported_honestly(self):
        self.box.obsidian_cli(enabled=False)
        code, out = self.box.cli("apply", "optical", "obsidian")
        self.assertEqual(code, 0, out)
        self.assertIn("CLI is off", out)
        self.assertNotIn("switched live", out)

    def test_app_level_mismatch_fails_verification(self):
        self.box.obsidian_cli(enabled=True)
        self.box.respond(["obsidian-cli"], contains="theme:set", stdout="Switched")
        self.box.respond(["obsidian-cli"], contains=" theme", stdout="Minimal")  # the app did not switch
        before = (self.vaults["id-notes"] / ".obsidian/appearance.json").read_text()
        code, out = self.box.cli("apply", "optical", "obsidian")
        self.assertEqual(code, 1, out)
        self.assertIn("the running app shows Minimal", out)
        self.assertEqual(json.loads(before), json.loads((self.vaults["id-notes"] / ".obsidian/appearance.json").read_text()))

    def test_restore_is_semantic_not_byte_exact(self):
        notes = self.vaults["id-notes"] / ".obsidian/appearance.json"

        def obsidian_rewrites_the_file():
            data = json.loads(notes.read_text())
            notes.write_text(json.dumps(data, separators=(",", ":")))  # Obsidian's own formatting

        code, out = self.box.cli("preview", "shell", "obsidian", wait=obsidian_rewrites_the_file)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(notes.read_text()), VAULTS["id-notes"]["appearance"])
        self.assertFalse((self.vaults["id-x-docs"] / ".obsidian/appearance.json").exists())
        self.assertFalse((self.vaults["id-notes"] / ".obsidian/themes/CORE - SHELL").exists())

    def test_doctor_names_duplicate_vaults_unambiguously(self):
        code, out = self.box.cli("doctor", "obsidian")
        for label in ("Work/docs", "core/docs", "x/docs", "id id-core-docs"):
            self.assertIn(label, out)
        self.assertIn("not initialized", out)

    def test_repair_reinstalls_theme_files_into_vaults_that_select_them(self):
        self.box.cli("apply", "glacier", "obsidian")
        import shutil
        shutil.rmtree(self.vaults["id-work-docs"] / ".obsidian/themes/CORE - GLACIER")
        code, out = self.box.cli("repair", "obsidian")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.vaults["id-work-docs"] / ".obsidian/themes/CORE - GLACIER/theme.css").is_file())
