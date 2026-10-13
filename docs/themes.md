# Writing a theme

A CORE theme is one file: `themes/<name>/theme.toml`.

```sh
core theme new solar --from shell          # copies shell's file under a new identity
$EDITOR themes/solar/theme.toml            # the only file you edit
core build solar                           # compile into build/solar/ and check references
core preview solar                         # look; Enter restores
core apply solar                           # keep it
```

No Python, no profile file, no template to copy. To share a theme, share that file.

## The file

```toml
[theme]
id = "solar"            # must match the directory name
name = "SOLAR"          # display name
mode = "light"          # light or dark: drives VS Code, Obsidian, ChatGPT, macOS
description = "…"

[palette]               # the colours
paper = "#F4F1E8"
ink = "#1D1B16"
sun = "#C2410C"

[roles]                 # what they mean (background, text and selection are required)
background = "paper"
text = "ink"
signal = "sun"
selection = "…"

[terminal]              # foreground, background, cursor, selection_background, selection_foreground
[terminal.ansi]         # black … bright_white, all 16
[prompt]                # Starship: frame, brand, separator, directory, git_branch, …
[apps.vscode]           # editor colours and syntax, by role
[apps.hermes]           # Hermes skin colours, by role
[apps.obsidian]         # Obsidian chrome, text, code, headings, by role
[apps.fastfetch]
[apps.chatgpt]          # surface, ink, accent, diff colours, contrast, fonts
```

Start from a built-in: its file shows every key. Keys you leave out come from
`corelib/templates/defaults.toml`. Values are palette names, role names or
literal `#RRGGBB`; inside an `[apps.*]` table a value may also name another key
of the same table.

If a reference is wrong, `core build <name>` names the table, key and value.

## Optional identity keys

`[theme]` may also set `slug` (the file name used for the Ghostty theme and the
Hermes skin, default `core-<id>`), `brand` (the word in the prompt and banners,
default `CORE`) and `author` (written into the Obsidian theme manifest).

## Generated files

`core build` writes `build/<name>/`: one file per application plus the ChatGPT
import string. It is disposable output, ignored by git, and rebuilt
automatically by `preview` and `apply` when the theme changes.
