"""Hermes: update-resilient integration through skins and customCSS only."""

from __future__ import annotations

import json

from corelib import surfaces as registry
from corelib.surfaces.hermes import display_skin, set_display_skin
from tests.support import SandboxTest

CONFIG = "model:\n  default: x\ndisplay:\n  skin: default\n  compact: true\nterminal:\n  backend: local\n"


class DailyUpdate(SandboxTest):
    """The acceptance scenario: an update deletes CORE's skin; repair puts it back."""

    def test_update_detect_repair_verify_noop(self):
        config = self.box.hermes(CONFIG)
        user_skin = self.box.home / ".hermes/skins/mine.yaml"
        user_skin.parent.mkdir(parents=True, exist_ok=True)
        user_skin.write_text("name: mine\ncolors: {}\n")

        # 1. valid CORE integration
        code, out = self.box.cli("apply", "optical", "hermes")
        self.assertEqual(code, 0, out)
        skin = self.box.home / ".hermes/skins/node-optical.yaml"
        self.assertIn("customCSS:", skin.read_text())
        self.assertIn("--ui-selection-background: #0B3A4A", skin.read_text())

        # 2. an upstream update removes CORE's skin, leaves everything else
        skin.unlink()
        config_after_update = config.read_text()

        # 3. doctor detects it (and writes nothing)
        before = self.box.tree()
        code, out = self.box.cli("doctor", "hermes")
        self.assertIn("REPAIRABLE", out)
        self.assertIn("node-optical.yaml (missing)", out)
        self.assertEqual(before, self.box.tree())

        # 4. dry-run: REPAIRABLE, an accurate plan, no changes
        code, out = self.box.cli("repair", "hermes", "--dry-run")
        self.assertEqual(code, 0, out)
        for expected in ("customCSS", "supported", "REPAIRABLE", "Generate the current OPTICAL Hermes skin",
                         "Install CORE-owned skin", "Preserve every other Hermes setting", "No changes made"):
            self.assertIn(expected, out)
        self.assertEqual(before, self.box.tree())

        # 5–6. repair restores the skin and verifies healthy
        code, out = self.box.cli("repair", "hermes")
        self.assertEqual(code, 0, out)
        self.assertIn("verified: ACTIVE", out)
        self.assertEqual(skin.read_text(), self.box.ctx().built(__import__("corelib.theme", fromlist=["load"]).load(self.box.root, "optical")).path("hermes").read_text())

        # 7. repeat repair: no-op
        snapshot = self.box.tree()
        code, out = self.box.cli("repair", "hermes")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing to repair", out)
        self.assertEqual(snapshot, self.box.tree())

        # unrelated configuration and the user's own skin survived
        self.assertEqual(config.read_text(), config_after_update)
        self.assertEqual(user_skin.read_text(), "name: mine\ncolors: {}\n")

        # 8–10. no checkout, no bundle mutation, no build/sign/install programs
        programs = {call[0] for call in self.box.fake.calls}
        self.assertTrue(programs <= {"ps", "defaults"}, programs)
        self.assertFalse((self.box.home / ".hermes/hermes-agent").exists())
        bundle = self.box.apps / "Hermes.app"
        self.assertEqual(sorted(p.name for p in bundle.rglob("*") if p.is_file()), ["context-ABC.js"])

    def test_apply_self_heals_with_the_same_repair_code(self):
        self.box.hermes(CONFIG)
        self.box.cli("apply", "optical", "hermes")
        (self.box.home / ".hermes/skins/node-optical.yaml").unlink()
        calls = []
        surface = registry.BY_KEY["hermes"]
        original = surface.ensure

        def counted(ctx, theme):
            calls.append(theme.id)
            return original(ctx, theme)

        surface.ensure = counted
        self.addCleanup(setattr, surface, "ensure", original)
        code, out = self.box.cli("apply", "optical", "hermes")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, ["optical"])
        self.assertIn("installed CORE skin", out)


class States(SandboxTest):
    def integration(self, theme_id="optical"):
        from corelib.theme import load
        return registry.BY_KEY["hermes"].integration(self.box.ctx(), load(self.box.root, theme_id) if theme_id else None)

    def test_absent_is_na(self):
        self.assertEqual(self.integration().state, "N/A")

    def test_app_never_started_is_not_applicable(self):
        (self.box.apps / "Hermes.app").mkdir(parents=True)
        self.box.ghostty("")
        self.assertEqual(self.integration().state, "N/A")
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 0, out)  # never blocks the other applications
        self.assertIn("not set up yet", out)

    def test_present_without_core_integration_is_ready(self):
        self.box.hermes(CONFIG)
        self.assertEqual(self.integration(None).state, "READY")
        self.assertEqual(self.integration().state, "READY")

    def test_stale_skin_is_repairable(self):
        self.box.hermes(CONFIG)
        self.box.cli("apply", "optical", "hermes")
        skin = self.box.home / ".hermes/skins/node-optical.yaml"
        skin.write_text(skin.read_text().replace("#0B3A4A", "#FFD24A"))
        state = self.integration()
        self.assertEqual((state.state, "out of date" in state.reason), ("REPAIRABLE", True))

    def test_malformed_skin_is_repairable(self):
        self.box.hermes(CONFIG)
        self.box.cli("apply", "optical", "hermes")
        (self.box.home / ".hermes/skins/node-optical.yaml").write_text("{{{ not yaml")
        self.assertIn("malformed", self.integration().reason)
        code, out = self.box.cli("repair", "hermes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.integration().state, "ACTIVE")

    def test_config_points_at_a_theme_that_no_longer_exists(self):
        self.box.hermes(CONFIG.replace("skin: default", "skin: core-vanished"))
        state = self.integration(None)
        self.assertEqual(state.state, "BLOCKED")
        self.assertIn("no longer exists", state.reason)

    def test_desktop_without_custom_css_is_reported_not_blocking(self):
        self.box.hermes(CONFIG, custom_css=False)
        facts = dict(self.integration().facts)
        self.assertEqual(facts["customCSS"], "not supported by this version")
        code, out = self.box.cli("apply", "optical", "hermes")
        self.assertEqual(code, 0, out)
        self.assertIn("predates skin customCSS", out)

    def test_restore_returns_display_skin_and_removes_the_skin(self):
        config = self.box.hermes(CONFIG)
        code, out = self.box.cli("preview", "glacier", "hermes", wait=lambda: None)
        self.assertEqual(code, 0, out)
        self.assertEqual(config.read_text(), CONFIG)
        self.assertFalse((self.box.home / ".hermes/skins/node-glacier.yaml").exists())


class ConfigEditing(SandboxTest):
    def test_display_skin_edits_touch_one_line(self):
        self.assertEqual(display_skin(CONFIG), "default")
        changed = set_display_skin(CONFIG, "node-shell")
        self.assertEqual(changed, CONFIG.replace("skin: default", "skin: node-shell"))
        self.assertEqual(set_display_skin(changed, "default"), CONFIG)
        no_skin = "display:\n  compact: true\n"
        self.assertEqual(display_skin(set_display_skin(no_skin, "x")), "x")
        self.assertEqual(set_display_skin(set_display_skin(no_skin, "x"), None), no_skin)
        self.assertEqual(display_skin(set_display_skin("model: y\n", "x")), "x")
        self.assertIsNone(display_skin("theme:\n  skin: nope\n"))
