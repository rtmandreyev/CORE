"""Ghostty, Terminal, Starship/Fastfetch, ChatGPT, macOS appearance, Claude."""

from __future__ import annotations

import json
import os

from corelib import surfaces as registry
from corelib.build import CHATGPT_PREFIX, terminal_profile_name
from corelib.surfaces.ghostty import last_theme
from corelib.theme import load
from tests.support import SandboxTest


class Ghostty(SandboxTest):
    def test_last_directive_wins_and_is_the_one_replaced(self):
        config = self.box.ghostty("theme = A\nfont-size = 12\ntheme = B\n")
        self.assertEqual(last_theme(config.read_text()), "B")
        code, out = self.box.cli("apply", "shell", "ghostty")
        self.assertEqual(code, 0, out)
        self.assertEqual(config.read_text(), "theme = A\nfont-size = 12\ntheme = node-shell\n")
        self.box.cli("preview", "neon", "ghostty", wait=lambda: None)
        self.assertEqual(config.read_text(), "theme = A\nfont-size = 12\ntheme = node-shell\n")

    def test_writes_to_the_file_ghostty_loads_last(self):
        xdg = self.box.ghostty("theme = Dracula\n")
        support = self.box.ghostty("", location="support")
        code, out = self.box.cli("apply", "optical", "ghostty")
        self.assertEqual(code, 0, out)
        self.assertEqual(xdg.read_text(), "theme = Dracula\n")
        self.assertEqual(support.read_text(), "theme = node-optical\n")

    def test_created_config_is_removed_on_restore(self):
        config = self.box.ghostty(None)
        code, out = self.box.cli("preview", "optical", "ghostty", wait=lambda: None)
        self.assertEqual(code, 0, out)
        self.assertFalse(config.exists())
        self.assertFalse((self.box.home / ".config/ghostty/themes/node-optical").exists())

    def test_effective_config_comes_from_ghostty_itself_when_installed(self):
        self.box.ghostty("theme = node-optical\n")
        binary = self.box.apps / "Ghostty.app/Contents/MacOS/ghostty"
        binary.write_text("")
        self.box.respond(["ghostty", "+show-config"], stdout="font-size = 12\ntheme = Dracula\n")
        surface = registry.BY_KEY["ghostty"]
        self.assertEqual(surface.effective_theme(self.box.paths), "Dracula")

    def test_running_ghostty_is_reloaded_through_its_action_api(self):
        self.box.ghostty("")
        self.box.running(self.box.apps / "Ghostty.app/Contents/MacOS/ghostty")
        self.box.respond(["osascript"], contains="reload_config", stdout="true")
        code, out = self.box.cli("apply", "optical", "ghostty")
        self.assertEqual(code, 0, out)
        self.assertIn("Ghostty reloaded", out)

    def test_apply_migrates_every_legacy_repository_link(self):
        themes = self.box.home / ".config/ghostty/themes"
        themes.mkdir(parents=True)
        for slug, theme in (("node-optical", "optical"), ("node-shell", "shell")):
            os.symlink(f"/old/CORE/core/themes/{theme}/generated/ghostty/{slug}", themes / slug)
        os.symlink("/somewhere/else/my-theme", themes / "mine")
        self.box.ghostty("theme = node-optical\n")
        code, out = self.box.cli("apply", "optical", "ghostty")
        self.assertEqual(code, 0, out)
        self.assertFalse((themes / "node-optical").is_symlink())
        self.assertFalse((themes / "node-shell").is_symlink())
        self.assertIn("SHELL for Ghostty", (themes / "node-shell").read_text())
        self.assertTrue((themes / "mine").is_symlink())  # not CORE's: untouched

    def test_preview_never_migrates_other_links(self):
        themes = self.box.home / ".config/ghostty/themes"
        themes.mkdir(parents=True)
        os.symlink("/old/CORE/core/themes/shell/generated/ghostty/node-shell", themes / "node-shell")
        self.box.ghostty("theme = node-shell\n")
        self.box.cli("preview", "optical", "ghostty", wait=lambda: None)
        self.assertTrue((themes / "node-shell").is_symlink())

    def test_repo_symlink_is_replaced_by_a_copy_and_restored(self):
        theme_link = self.box.home / ".config/ghostty/themes/node-optical"
        theme_link.parent.mkdir(parents=True)
        os.symlink("/nonexistent/CORE/core/themes/optical/generated/ghostty/node-optical", theme_link)
        self.box.ghostty("theme = node-optical\n")
        self.assertEqual(registry.BY_KEY["ghostty"].integration(self.box.ctx(), load(self.box.root, "optical")).state, "REPAIRABLE")
        code, out = self.box.cli("preview", "optical", "ghostty", wait=lambda: self.assertFalse(theme_link.is_symlink()))
        self.assertEqual(code, 0, out)
        self.assertTrue(theme_link.is_symlink())


class Terminal(SandboxTest):
    def test_closed_terminal_gets_one_profile_and_default(self):
        prefs = self.box.terminal(default="Homebrew", profiles={"Homebrew": {"name": "Homebrew"}, "Pro": {"name": "Pro"}})
        code, out = self.box.cli("apply", "optical", "terminal")
        self.assertEqual(code, 0, out)
        name = terminal_profile_name(load(self.box.root, "optical"))
        self.assertEqual(prefs["Default Window Settings"], name)
        self.assertEqual(sorted(prefs["Window Settings"]), sorted(["Homebrew", "Pro", name]))
        profile = prefs["Window Settings"][name]
        for key in ("BackgroundColor", "TextColor", "CursorColor", "SelectionColor", "ANSIBlackColor", "ANSIBrightWhiteColor"):
            self.assertIn(key, profile)
        self.box.cli("preview", "shell", "terminal", wait=lambda: None)
        self.assertEqual(prefs["Default Window Settings"], name)

    def test_preview_restores_default_profile(self):
        prefs = self.box.terminal(default="Basic")
        code, out = self.box.cli("preview", "ember", "terminal", wait=lambda: None)
        self.assertEqual(code, 0, out)
        self.assertEqual((prefs["Default Window Settings"], prefs["Startup Window Settings"]), ("Basic", "Basic"))

    def test_running_terminal_uses_import_and_applescript(self):
        import re
        from corelib.effects import Result
        self.box.terminal(running=True)
        state = {"default": "Basic", "windows": ["41"]}

        def terminal(argv, _input):
            if argv[:1] == ["open"]:
                state["windows"].append("77")  # importing opens a window
                return Result(0)
            if argv[:1] != ["osascript"]:
                return None
            script = argv[2]
            if "get id of every window" in script:
                return Result(0, ", ".join(state["windows"]))
            if "name of default settings" in script:
                return Result(0, f"{state['default']}\n{state['default']}\n41\t1\tBasic\n")
            if "close (first window whose id is 77)" in script:
                state["windows"].remove("77")
                return Result(0)
            match = re.search(r'set default settings to settings set "([^"]+)"', script)
            if match:
                state["default"] = match.group(1)
            return Result(0)

        self.box.fake.handlers.insert(0, terminal)
        code, out = self.box.cli("preview", "optical", "terminal", wait=lambda: self.assertEqual(state["default"], "CORE OPTICAL"))
        self.assertEqual(code, 0, out)
        self.assertEqual(state, {"default": "Basic", "windows": ["41"]})  # import window closed, default restored
        self.assertIn("1 open tab(s) switched", out)
        self.assertFalse(any(call[:2] == ["defaults", "write"] for call in self.box.fake.calls))

    def test_tab_listing_uses_a_tab_character_not_terminals_tab_class(self):
        """Regression: inside `tell application "Terminal"`, `tab` is a tab object."""

        from corelib.surfaces.terminal import _TABS_SCRIPT
        self.assertNotIn("& tab &", _TABS_SCRIPT)
        self.assertIn("character id 9", _TABS_SCRIPT)

    def test_profile_colours_are_tagged_srgb(self):
        """Regression: untagged calibrated RGB rendered visibly off in real Terminal."""

        import plistlib
        from corelib.build import archived_color
        archive = plistlib.loads(archived_color("#B9E7F2"))
        colour, space = archive["$objects"][1], archive["$objects"][2]
        self.assertEqual(colour["NSComponents"], b"0.7254901961 0.9058823529 0.9490196078 1")
        self.assertEqual(space["NSID"], 7)  # sRGB
        self.assertEqual(archive["$objects"][colour["$class"].data]["$classname"], "NSColor")


class ConfigFiles(SandboxTest):
    def test_starship_is_a_copy_not_a_repository_link(self):
        config = self.box.starship("format = '$all'\n")
        code, out = self.box.cli("apply", "neon", "starship")
        self.assertEqual(code, 0, out)
        self.assertFalse(config.is_symlink())
        self.assertIn("NEON for Starship", config.read_text())
        self.box.cli("preview", "shell", "starship", wait=lambda: None)
        self.assertIn("NEON for Starship", config.read_text())

    def test_previous_symlink_is_restored_exactly(self):
        config = self.box.fastfetch(None)
        config.parent.mkdir(parents=True)
        os.symlink("/old/target.jsonc", config)
        self.box.cli("preview", "optical", "fastfetch", wait=lambda: self.assertFalse(config.is_symlink()))
        self.assertEqual(os.readlink(config), "/old/target.jsonc")


class Extras(SandboxTest):
    def test_chatgpt_export_matches_the_import_schema(self):
        self.box.chatgpt()
        code, out = self.box.cli("export", "optical", "chatgpt")
        self.assertEqual(code, 0, out)
        line = next(l for l in out.splitlines() if l.startswith(CHATGPT_PREFIX))
        data = json.loads(line[len(CHATGPT_PREFIX):])
        self.assertEqual(set(data), {"codeThemeId", "theme", "variant"})
        self.assertEqual(data["variant"], "dark")
        self.assertEqual(set(data["theme"]), {"accent", "accentSource", "contrast", "fonts", "ink", "opaqueWindows",
                                              "semanticColors", "surface"})
        for key in ("accent", "ink", "surface"):
            self.assertRegex(data["theme"][key], r"^#[0-9a-f]{6}$")
        self.assertEqual(data["theme"]["fonts"], {"code": None, "ui": None})
        self.assertFalse((self.box.home / ".codex").exists())

    def test_chatgpt_copy_uses_pbcopy(self):
        self.box.respond(["pbcopy"], stdout="")
        code, out = self.box.cli("export", "shell", "chatgpt", "--copy")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.box.fake.called("pbcopy"))

    def test_macos_appearance_is_never_changed_unless_selected(self):
        self.box.ghostty("")
        code, out = self.box.cli("apply", "shell")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.box.fake.called("osascript"))

    def test_macos_appearance_when_selected(self):
        self.box.respond(["osascript"], contains="dark mode to true", stdout="")
        dark = {"on": False}

        def appearance(argv, _input):
            from corelib.effects import Result
            if argv[:3] == ["defaults", "read", "-g"]:
                return Result(0, "Dark\n") if dark["on"] else Result(1, "", "does not exist")
            if argv[:1] == ["osascript"] and "dark mode to" in argv[2]:
                dark["on"] = "true" in argv[2]
                return Result(0)
            return None

        self.box.fake.handlers.append(appearance)
        code, out = self.box.cli("preview", "optical", "macos", wait=lambda: self.assertTrue(dark["on"]))
        self.assertEqual(code, 0, out)
        self.assertFalse(dark["on"])

    def test_macos_opt_in_through_user_config(self):
        self.box.paths.user_config.parent.mkdir(parents=True, exist_ok=True)
        self.box.paths.user_config.write_text("[surfaces]\nmacos = true\n")
        code, out = self.box.cli("apply", "optical", "--dry-run")
        self.assertIn("macOS appearance", out.split("Would apply:")[-1])

    def test_claude_is_informational(self):
        self.box.claude()
        code, out = self.box.cli("doctor", "claude")
        self.assertIn("Match System", out)
        self.assertNotIn("claude", [key for key in registry.SELECTABLE_KEYS])


class Run2Regressions(SandboxTest):
    def test_terminal_reencoded_profile_counts_as_current(self):
        """Regression: Terminal re-encodes imported colours (ICC attached); compare values."""

        import plistlib
        from corelib.build import terminal_profile
        from corelib.surfaces.terminal import decoded_color
        theme = load(self.box.root, "optical")
        profile = terminal_profile(theme)
        reencoded = dict(profile)
        archive = plistlib.loads(profile["BackgroundColor"])
        archive["$objects"][1]["NSComponents"] = b"0.007843137255 0.03921568627 0.07058823529 1"
        archive["$objects"][1]["NSRGB"] = b"0.01058577094 0.0395225659 0.05848469585\x00"
        reencoded["BackgroundColor"] = plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)
        self.assertEqual(decoded_color(reencoded["BackgroundColor"]), "#020A12")
        self.box.terminal(default="CORE OPTICAL", profiles={"CORE OPTICAL": reencoded})
        self.assertEqual(registry.BY_KEY["terminal"].integration(self.box.ctx(), theme).state, "ACTIVE")

    def test_vscode_selection_reset_by_vscode_is_repaired(self):
        """Regression: VS Code deletes workbench.colorTheme when the base extension vanishes."""

        import shutil
        from corelib import jsonc
        settings = self.box.vscode("{}", extension=True)
        self.box.cli("apply", "optical", "vscode")
        shutil.rmtree(self.box.home / ".vscode/extensions/core.core-base-0.1.0")
        (self.box.home / ".vscode/extensions/extensions.json").write_text("[]")
        settings.write_text(jsonc.delete_path(settings.read_text(), ["workbench.colorTheme"]))
        code, out = self.box.cli("repair", "vscode", "--dry-run")
        self.assertIn("REPAIRABLE", out)
        self.assertIn("Select CORE Dark again", out)
        code, out = self.box.cli("repair", "vscode")
        self.assertEqual(code, 0, out)
        self.assertEqual(jsonc.load(settings.read_text())["workbench.colorTheme"], "CORE Dark")

    def test_user_choice_of_another_theme_is_not_repaired(self):
        import shutil
        from corelib import jsonc
        settings = self.box.vscode("{}", extension=True)
        self.box.cli("apply", "optical", "vscode")
        shutil.rmtree(self.box.home / ".vscode/extensions/core.core-base-0.1.0")
        (self.box.home / ".vscode/extensions/extensions.json").write_text("[]")
        settings.write_text(jsonc.set_path(settings.read_text(), ["workbench.colorTheme"], "Monokai"))
        code, out = self.box.cli("repair", "vscode")
        self.assertIn("Nothing to repair", out)
        self.assertEqual(jsonc.load(settings.read_text())["workbench.colorTheme"], "Monokai")

    def test_applying_one_more_app_keeps_the_recorded_apps(self):
        import json
        self.box.ghostty("")
        self.box.hermes()
        self.box.cli("apply", "optical", "ghostty")
        self.box.cli("apply", "optical", "hermes")
        self.assertEqual(json.loads(self.box.paths.state_file.read_text())["surfaces"], ["ghostty", "hermes"])
