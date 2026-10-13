"""One canonical file per theme, shared templates, deterministic builds."""

from __future__ import annotations

import json
import re
import tomllib
import unittest

from corelib import build as build_module
from corelib.render import TEMPLATES_DIR
from corelib.theme import BUILTIN_THEMES, Theme, discover, load
from tests.support import REPO, BUILTINS, SandboxTest


class CanonicalFiles(unittest.TestCase):
    def test_six_builtins_each_load_from_one_file(self):
        found = {info.id: info for info in discover(REPO)}
        self.assertEqual(set(BUILTINS), set(found) & set(BUILTINS))
        for theme_id in BUILTINS:
            directory = REPO / "themes" / theme_id
            self.assertEqual(found[theme_id].path, directory / "theme.toml")
            tracked = {p.name for p in directory.iterdir() if p.name != "generated"}
            self.assertEqual(tracked, {"theme.toml"}, f"{theme_id} carries more than its canonical file")

    def test_no_profile_files_and_no_per_theme_templates(self):
        self.assertFalse((REPO / "profiles").exists())
        self.assertEqual(list(REPO.glob("themes/*/templates")), [])

    def test_templates_are_shared(self):
        names = {p.name for p in TEMPLATES_DIR.iterdir()}
        for expected in ("ghostty.tmpl", "starship.toml.tmpl", "fastfetch.jsonc.tmpl", "hermes.skin.yaml.tmpl",
                         "obsidian.css.tmpl", "obsidian.manifest.json.tmpl", "vscode.theme.json.tmpl", "defaults.toml"):
            self.assertIn(expected, names)

    def test_mode_metadata(self):
        modes = {theme_id: load(REPO, theme_id).mode for theme_id in BUILTINS}
        self.assertEqual(modes, {"optical": "dark", "shell": "light", "ember": "light", "glacier": "light",
                                 "augment": "dark", "neon": "dark"})


class Builds(SandboxTest):
    def test_every_builtin_builds_completely_and_deterministically(self):
        for theme_id in BUILTINS:
            theme = load(self.box.root, theme_id)
            first = build_module.build(self.box.paths, theme)
            snapshot = {p: p.read_bytes() for p in first.root.rglob("*") if p.is_file()}
            build_module.build(self.box.paths, theme)
            self.assertEqual(snapshot, {p: p.read_bytes() for p in first.root.rglob("*") if p.is_file()})
            for key in build_module.OUTPUTS:
                text = first.path(key).read_text()
                self.assertNotRegex(text, r"(?<!\$)\{[a-z_.]+\}", f"{theme_id}/{key} has an unresolved token")

    def test_obsidian_css_root_rule_is_valid(self):
        """Regression: OPTICAL, AUGMENT and NEON shipped an invalid first selector."""

        for theme_id in BUILTINS:
            css = build_module.build(self.box.paths, load(self.box.root, theme_id)).path("obsidian_css").read_text()
            code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            first_selector = code.lstrip()[: code.lstrip().index("{")]
            self.assertEqual(" ".join(first_selector.split()), "body.theme-dark, body.theme-light", theme_id)
            self.assertNotIn("`", code)

    def test_fastfetch_escape_sequences_are_valid(self):
        for theme_id in BUILTINS:
            text = build_module.build(self.box.paths, load(self.box.root, theme_id)).path("fastfetch").read_text()
            self.assertNotIn("}m", text)

    def test_light_dark_propagates(self):
        for theme_id in BUILTINS:
            theme = load(self.box.root, theme_id)
            built = build_module.build(self.box.paths, theme)
            settings = json.loads(built.path("vscode_settings").read_text())
            self.assertEqual(settings["workbench.colorTheme"], "CORE Dark" if theme.mode == "dark" else "CORE Light")
            self.assertIn(f'"variant":"{theme.mode}"', built.path("chatgpt").read_text())
            css = built.path("obsidian_css").read_text()
            self.assertIn(f"color-scheme: {theme.mode};", css)

    def test_vscode_base_extension_is_one_stable_package(self):
        vsix = build_module.vscode_vsix(self.box.paths)
        import zipfile
        with zipfile.ZipFile(vsix) as archive:
            names = set(archive.namelist())
            package = json.loads(archive.read("extension/package.json"))
        self.assertIn("extension.vsixmanifest", names)
        self.assertEqual(f"{package['publisher']}.{package['name']}", build_module.VSCODE_EXTENSION_ID)
        self.assertEqual([t["label"] for t in package["contributes"]["themes"]], ["CORE Dark", "CORE Light"])
        self.assertEqual(vsix.read_bytes(), build_module.vscode_vsix(self.box.paths).read_bytes())
