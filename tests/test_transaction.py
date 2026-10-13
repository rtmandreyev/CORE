"""Apply is all-or-nothing, interrupt-safe and recoverable."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

from corelib import surfaces as registry
from corelib.surfaces.hermes import display_skin
from tests.support import SandboxTest


class Transactions(SandboxTest):
    def setUp(self):
        super().setUp()
        self.ghostty = self.box.ghostty("theme = Dracula\n")
        self.settings = self.box.vscode('{\n    "workbench.colorTheme": "Monokai"\n}\n', extension=True)
        self.hermes = self.box.hermes()
        self.before = self.box.tree()

    def assert_untouched(self):
        after = self.box.tree()
        for name in (".config/ghostty/config", "Library/Application Support/Code/User/settings.json", ".hermes/config.yaml"):
            self.assertEqual(self.before.get(name), after.get(name), name)
        self.assertNotIn(".config/ghostty/themes/node-optical", after)
        self.assertNotIn(".hermes/skins/node-optical.yaml", after)

    def patch(self, key, method, replacement):
        surface = registry.BY_KEY[key]
        original = getattr(surface, method)
        setattr(surface, method, replacement)
        self.addCleanup(setattr, surface, method, original)
        return original

    def test_ctrl_c_during_apply_rolls_everything_back(self):
        def interrupted(ctx, theme, captured):
            raise KeyboardInterrupt

        self.patch("hermes", "apply", interrupted)  # after ghostty and vscode already applied
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 130, out)
        self.assertIn("rolling back", out)
        self.assert_untouched()
        self.assertFalse(self.box.paths.journal.exists())
        self.assertFalse(self.box.paths.state_file.exists())

    def test_half_applied_surface_is_restored_too(self):
        original = registry.BY_KEY["hermes"].apply

        def half(ctx, theme, captured):
            original(ctx, theme, captured)  # writes, then dies before returning
            raise KeyboardInterrupt

        self.patch("hermes", "apply", half)
        code, _ = self.box.cli("apply", "optical")
        self.assertEqual(code, 130)
        self.assertEqual(display_skin(self.hermes.read_text()), "default")
        self.assert_untouched()

    def test_failed_verification_rolls_back(self):
        self.patch("vscode", "verify", lambda ctx, theme: ["simulated mismatch"])
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 1, out)
        self.assertIn("verification failed", out)
        self.assert_untouched()

    def test_rollback_tries_every_surface_and_keeps_the_journal(self):
        self.patch("hermes", "apply", lambda ctx, theme, captured: (_ for _ in ()).throw(RuntimeError("boom")))

        def broken_restore(ctx, captured):
            raise OSError("disk says no")

        self.patch("vscode", "restore", broken_restore)
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 3, out)
        self.assertIn("ATTENTION", out)
        self.assertEqual(self.ghostty.read_text(), "theme = Dracula\n")  # restored despite vscode failing
        journal = json.loads(self.box.paths.journal.read_text())
        self.assertEqual(journal["kind"], "apply")
        # The next run refuses to stack another change on top.
        code, out = self.box.cli("apply", "shell")
        self.assertEqual(code, 3)
        self.assertIn("core restore", out)

    def test_restore_recovers_after_the_process_was_killed(self):
        """What `kill -9` leaves behind: a journal and a half-applied machine."""

        from corelib import transaction
        from corelib.theme import load

        ctx, theme = self.box.ctx(), load(self.box.root, "optical")
        the_plan = transaction.plan(ctx, theme, None)
        states = {key: registry.BY_KEY[key].capture(ctx, theme) for key in the_plan.selected}
        transaction.write_journal(self.box.paths, {"kind": "apply", "theme": "optical", "surfaces": the_plan.selected,
                                                   "states": states, "pid": 999999})
        registry.BY_KEY["ghostty"].apply(ctx, theme, states["ghostty"])
        registry.BY_KEY["hermes"].apply(ctx, theme, states["hermes"])
        self.assertIn("node-optical", self.ghostty.read_text())

        code, out = self.box.cli("status")
        self.assertIn("core restore", out)
        code, out = self.box.cli("restore")
        self.assertEqual(code, 0, out)
        self.assert_untouched()
        self.assertFalse(self.box.paths.journal.exists())

    def test_blocked_surface_stops_before_any_write(self):
        self.settings.write_text('{ "a": 1, "b": }')  # not editable safely
        code, out = self.box.cli("apply", "optical")
        self.assertEqual(code, 1, out)
        self.assertIn("Cannot change VS Code", out)
        self.assertIn("No changes made", out)
        self.assertEqual(self.ghostty.read_text(), "theme = Dracula\n")
        self.assertFalse(self.box.paths.journal.exists())


    def test_a_surprise_in_one_status_check_blocks_only_that_app(self):
        def explode(ctx, theme):
            raise ValueError("unexpected")

        self.patch("hermes", "status", explode)
        code, out = self.box.cli("apply", "optical", "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("could not inspect (unexpected)", out)
        self.assertIn("core apply optical ghostty vscode", out)


class SignalsInARealProcess(SandboxTest):
    def test_sigterm_during_preview_restores(self):
        ghostty = self.box.ghostty("theme = Dracula\n")
        self.box.hermes(desktop=False)
        process = subprocess.Popen([sys.executable, "-m", "corelib.cli", "preview", "optical"], cwd=self.box.tmp,
                                   env=self.box.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while "node-optical" not in ghostty.read_text() and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("node-optical", ghostty.read_text())
        process.send_signal(signal.SIGTERM)
        out, _ = process.communicate(timeout=30)
        self.assertEqual(ghostty.read_text(), "theme = Dracula\n", out)
        self.assertFalse(self.box.paths.journal.exists(), out)
        self.assertIn("Restored and verified", out)
