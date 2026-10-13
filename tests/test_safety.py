"""Canaries: the suite cannot reach the real machine."""

from __future__ import annotations

import importlib
import os
import subprocess
import unittest
from pathlib import Path

from corelib import effects
from corelib.paths import Paths, UnsafePaths, real_home, repo_root


class EffectBoundary(unittest.TestCase):
    def setUp(self):
        effects.install(None)

    def test_test_mode_is_on(self):
        self.assertTrue(effects.test_mode())

    def test_real_effects_refuse_to_exist(self):
        with self.assertRaises(effects.EffectBlocked):
            effects.RealEffects()

    def test_every_external_effect_is_blocked_without_a_fake(self):
        for argv in (["osascript", "-e", "beep"], ["open", "hermes://x"], ["obsidian", "version"],
                     ["code", "--install-extension", "x.vsix"], ["defaults", "write", "x", "y", "z"]):
            with self.assertRaises(effects.EffectBlocked, msg=argv):
                effects.current().run(argv)
        with self.assertRaises(effects.EffectBlocked):
            effects.osascript('tell application "Ghostty" to activate')

    def test_path_stubs_intercept_programs_called_directly(self):
        result = subprocess.run(["osascript", "-e", "beep"], capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 99)
        log = Path(os.environ["PATH"].split(":")[0]).parent / "tripwire.log"
        log.write_text("")  # this canary fired on purpose


class PathBoundary(unittest.TestCase):
    def test_home_is_not_the_real_home(self):
        self.assertNotEqual(Path(os.environ["HOME"]).resolve(), real_home().resolve())

    def test_from_env_refuses_the_real_home(self):
        with self.assertRaises(UnsafePaths):
            Paths.from_env({"CORE_TEST_MODE": "1", "HOME": str(real_home()), "CORE_BUILD_DIR": "/tmp/x"})

    def test_from_env_refuses_real_applications(self):
        with self.assertRaises(UnsafePaths):
            Paths.from_env({"CORE_TEST_MODE": "1", "HOME": os.environ["HOME"], "CORE_APP_DIR": "/Applications",
                            "CORE_BUILD_DIR": "/tmp/x"})

    def test_from_env_refuses_building_into_the_real_repository(self):
        with self.assertRaises(UnsafePaths):
            Paths.from_env({"CORE_TEST_MODE": "1", "HOME": os.environ["HOME"]})

    def test_loading_the_cli_captures_no_home(self):
        cli = importlib.import_module("corelib.cli")
        for name in dir(cli):
            value = getattr(cli, name)
            self.assertNotIsInstance(value, Paths, name)
            if isinstance(value, Path):
                self.assertFalse(str(value).startswith(str(real_home())), name)

    def test_launcher_is_not_python_module_state(self):
        launcher = repo_root() / "bin/core"
        self.assertTrue(launcher.read_text().startswith("#!/bin/sh"))
