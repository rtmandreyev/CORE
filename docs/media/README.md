# Screenshots and demo

The README's images and demo video. Each is a capture of the real applications
on a Mac, themed by CORE, with demo content only.

| File | What it shows |
| --- | --- |
| `core-preview.mp4` | The hero demo (22 s, 1920×1080): the CORE app previews SHELL, Ghostty, Obsidian and VS Code switch together, Enter restores OPTICAL. |
| `core-preview.gif` | The same demo for the README (1280 px, 15 fps). |
| `core-app.png` | The interactive app (`core`) in Ghostty with OPTICAL active. |
| `optical.png`, `shell.png` | The same three windows under OPTICAL and under SHELL. |
| `themes.png` | One VS Code file under each of the six built-in themes. |

## How they were made

- Content is synthetic: a throwaway Obsidian vault with one note, a short
  TypeScript file in an otherwise empty folder, and the CORE app itself.
- Each window was captured on its own with ScreenCaptureKit, so nothing else on
  the screen could appear, then placed on a plain backdrop. The layout is a
  composition; the colours and every change in the video are CORE's.
- macOS draws a "being captured" badge over a window's traffic lights while it
  is recorded; the video shows the window's real traffic lights instead, taken
  from a still of the same window in the same theme.
- CORE ran with `HOME` pointing at a neutral alias of the home folder, so the
  paths it prints carry no user name. Nothing else about the run was changed.

## Rules for new captures

- Demo content only: no private conversations, notes, file names, paths,
  accounts or notifications in any frame.
- Capture app windows, not the whole screen.
- Keep the same windows and content across themes so the images compare cleanly.
