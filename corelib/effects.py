"""The one boundary between CORE and the rest of the Mac.

Every external effect CORE has — running `osascript`, `open`, `defaults`, the
VS Code or Obsidian CLIs, `ps`, `pbcopy`, `ghostty +show-config` — goes through
`current()`. File reads and writes do not: they are scoped by `Paths`, which in
test mode refuses the real home.

Test mode (`CORE_TEST_MODE=1`) makes real effects impossible:

* `RealEffects` refuses to construct;
* with no fake installed, every effect raises `EffectBlocked`;
* a test installs a `FakeEffects` in-process with `install()`, or hands a
  subprocess a JSON script through `CORE_TEST_EFFECTS` (canned responses plus a
  call log), so end-to-end CLI tests still observe every effect.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

TEST_MODE_ENV = "CORE_TEST_MODE"
FAKE_ENV = "CORE_TEST_EFFECTS"


class EffectBlocked(RuntimeError):
    """An external effect was attempted in test mode without a fake."""


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return " ".join(f"{self.stdout} {self.stderr}".split())


def test_mode() -> bool:
    return os.environ.get(TEST_MODE_ENV) == "1"


class RealEffects:
    def __init__(self) -> None:
        if test_mode():
            raise EffectBlocked("real effects are disabled in test mode")

    def run(self, argv: list[str], *, cwd: str | Path | None = None, timeout: float = 20.0, input: str | None = None) -> Result:
        try:
            completed = subprocess.run(
                [str(part) for part in argv],
                cwd=str(cwd) if cwd else None,
                input=input,
                stdin=None if input is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return Result(127, "", f"{argv[0]}: command not found")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"{argv[0]}: timed out after {timeout:g}s")
        except OSError as exc:
            return Result(126, "", f"{argv[0]}: {exc}")

        return Result(completed.returncode, completed.stdout, completed.stderr)

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class BlockedEffects:
    """Test mode with no fake: anything external is a test bug."""

    def run(self, argv, **_kwargs):
        raise EffectBlocked(f"blocked external effect in test mode: {' '.join(map(str, argv))}")

    def which(self, name):
        raise EffectBlocked(f"blocked PATH lookup in test mode: {name}")

    def sleep(self, seconds):
        return None


@dataclass
class FakeEffects:
    """Canned responses matched by argv prefix, with a call log.

    `responses` is a list of {"match": [...], "returncode", "stdout", "stderr"};
    the first entry whose `match` is a prefix of the argv (after the program
    name is reduced to its basename) wins. Unmatched calls return 127 — never a
    silent success.
    """

    responses: list[dict] = field(default_factory=list)
    which_map: dict[str, str] = field(default_factory=dict)
    log_path: Path | None = None
    # In-process tests may add callables(argv) -> Result | None, tried first.
    handlers: list = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    @staticmethod
    def _normal(argv) -> list[str]:
        parts = [str(part) for part in argv]
        if parts:
            parts[0] = Path(parts[0]).name
        return parts

    def run(self, argv, *, cwd=None, timeout=20.0, input=None) -> Result:
        parts = self._normal(argv)
        self.calls.append(parts)

        if self.log_path is not None:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(parts) + "\n")

        for handler in self.handlers:
            handled = handler(parts, input)
            if handled is not None:
                return handled

        joined = " ".join(parts)
        for response in self.responses:
            match = [str(item) for item in response.get("match", [])]
            if parts[: len(match)] != match:
                continue
            if response.get("contains") and response["contains"] not in joined:
                continue
            for path, text in (response.get("writes") or {}).items():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(text)
            if response.get("once"):
                self.responses = [r for r in self.responses if r is not response]
            return Result(
                int(response.get("returncode", 0)),
                response.get("stdout", ""),
                response.get("stderr", ""),
            )

        return Result(127, "", f"{parts[0]}: not stubbed in test")

    def which(self, name: str) -> str | None:
        return self.which_map.get(name)

    def sleep(self, seconds: float) -> None:
        return None

    def called(self, *prefix: str) -> list[list[str]]:
        want = list(prefix)
        return [call for call in self.calls if call[: len(want)] == want]


_installed = None


def install(fake) -> None:
    """Install an effects implementation (tests only); `None` uninstalls."""

    global _installed
    _installed = fake


def current():
    if _installed is not None:
        return _installed

    if test_mode():
        script = os.environ.get(FAKE_ENV)

        if script:
            data = json.loads(Path(script).read_text())
            return FakeEffects(
                responses=data.get("responses", []),
                which_map=data.get("which", {}),
                log_path=Path(data["log"]) if data.get("log") else None,
            )

        return BlockedEffects()

    return RealEffects()


# ── small helpers every surface needs ──


def process_running(executable: Path) -> bool:
    """Whether a process whose command is exactly `executable` is running."""

    result = current().run(["ps", "-A", "-o", "comm="], timeout=10)

    if not result.ok:
        return False

    target = str(executable)
    return any(line.strip() == target for line in result.stdout.splitlines())


def osascript(script: str, *, timeout: float = 15.0) -> Result:
    return current().run(["osascript", "-e", script], timeout=timeout)
