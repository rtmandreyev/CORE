"""Every surface CORE knows, in display order."""

from __future__ import annotations

from corelib.surfaces.base import (  # noqa: F401  (re-exported vocabulary)
    ACTIVE, BLOCKED, EXPORT, INFO, NA, OPT_IN, READY, REPAIRABLE,
    Context, Integration, Status, Surface, SurfaceError,
)
from corelib.surfaces.configfile import Fastfetch, Starship
from corelib.surfaces.extras import ChatGPT, Claude, MacOS
from corelib.surfaces.ghostty import Ghostty
from corelib.surfaces.hermes import Hermes
from corelib.surfaces.obsidian import Obsidian
from corelib.surfaces.terminal import Terminal
from corelib.surfaces.vscode import VSCode

SURFACES: tuple[Surface, ...] = (
    Ghostty(), Terminal(), Starship(), Fastfetch(), VSCode(), Hermes(), Obsidian(),
    ChatGPT(), MacOS(), Claude(),
)
BY_KEY = {surface.key: surface for surface in SURFACES}
APPLY_KEYS = tuple(surface.key for surface in SURFACES if surface.kind == "apply")
SELECTABLE_KEYS = tuple(surface.key for surface in SURFACES if surface.kind in ("apply", "export", "optin"))
REPAIR_KEYS = tuple(surface.key for surface in SURFACES if surface.repairable)


def get(key: str) -> Surface:
    if key not in BY_KEY:
        raise KeyError(f"unknown application {key!r} (choose from: {', '.join(SELECTABLE_KEYS)})")
    return BY_KEY[key]
