"""Small, careful file primitives shared by every surface.

* `write_text` is atomic (temp file + rename in the same directory) and keeps
  the existing file's permission bits.
* `snapshot` / `restore_snapshot` capture and put back a path's exact form:
  a file's text, a symlink's target, or absence.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None

    if path.exists() and not path.is_symlink():
        mode = stat.S_IMODE(path.stat().st_mode)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode if mode is not None else 0o644)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_text(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None


def snapshot(path: Path) -> dict:
    path = Path(path)

    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}

    if path.is_file():
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"kind": "binary", "sha256": sha256(data)}
        return {"kind": "file", "text": text, "sha256": sha256(data)}

    if path.is_dir():
        return {"kind": "dir"}

    return {"kind": "absent"}


def matches(path: Path, state: dict) -> bool:
    return snapshot(path) == state


def restore_snapshot(path: Path, state: dict) -> str:
    """Put a path back exactly as captured. Never removes a directory."""

    path = Path(path)
    kind = state.get("kind")

    if snapshot(path) == state:
        return "unchanged"

    if kind in ("binary", "dir"):
        raise OSError(f"{path}: captured {kind} state cannot be restored by CORE")

    if path.is_dir() and not path.is_symlink():
        raise OSError(f"{path}: refusing to replace a directory")

    if kind == "absent":
        if path.is_symlink() or path.exists():
            path.unlink()
        return "removed"

    if kind == "symlink":
        if path.is_symlink() or path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(state["target"])
        return "relinked"

    if kind == "file":
        if path.is_symlink():
            path.unlink()
        write_text(path, state["text"])
        return "restored"

    raise OSError(f"{path}: unknown captured state {kind!r}")
