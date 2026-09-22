"""Serve a WACZ through pywb, a separate GPL process, so passages resolve against the capture."""

from __future__ import annotations

import socket
import subprocess
import tempfile
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Self
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
PYWB_BIN = REPO / ".venvs" / "pywb" / "bin"
COLLECTION = "capture"


class ReplayError(RuntimeError):
    """pywb could not index or serve the WACZ."""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


PYWB_CONFIG = "framed_replay: false\nenable_auto_fetch: false\nenable_memento: false\n"


class ReplayServer:
    """A temporary pywb collection holding one WACZ, served on a free loopback port."""

    def __init__(
        self, wacz_path: Path, *, pywb_bin: Path = PYWB_BIN, startup_timeout_s: float = 30
    ) -> None:
        self._wacz_path = wacz_path.resolve()
        self._pywb_bin = pywb_bin
        self._startup_timeout_s = startup_timeout_s
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self.port = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def replay_url(self, original_url: str) -> str:
        """The frameless (mp_) replay of a captured URL: rewritten page, no pywb banner."""
        return f"{self.base_url}/{COLLECTION}/mp_/{original_url}"

    def __enter__(self) -> Self:
        if not self._wacz_path.is_file():
            msg = f"no WACZ at {self._wacz_path}"
            raise ReplayError(msg)
        self._tmp = tempfile.TemporaryDirectory(prefix="replay-")
        root = Path(self._tmp.name)
        manager = str(self._pywb_bin / "wb-manager")
        self._run([manager, "init", COLLECTION], root)
        self._run([manager, "add", "--unpack-wacz", COLLECTION, str(self._wacz_path)], root)
        # Framed replay injects a script that bounces a top-level page into pywb's banner UI.
        (root / "config.yaml").write_text(PYWB_CONFIG, encoding="utf-8")
        self.port = free_port()
        self._log = (root / "wayback.log").open("wb")
        argv = [str(self._pywb_bin / "wayback"), "-p", str(self.port), "-b", "127.0.0.1", "-d", "."]
        self._process = subprocess.Popen(  # noqa: S603 — fixed argv; pywb stays a separate process
            argv, cwd=root, stdout=self._log, stderr=subprocess.STDOUT
        )
        self._wait_ready()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)
        if self._log is not None:
            self._log.close()
        if self._tmp is not None:
            self._tmp.cleanup()

    def _run(self, argv: list[str], cwd: Path) -> None:
        proc = subprocess.run(  # noqa: S603 — fixed argv built from repo paths
            argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=300
        )
        if proc.returncode != 0:
            msg = f"{argv[1]} failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}"
            raise ReplayError(msg)

    def _wait_ready(self) -> None:
        assert self._process is not None
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                msg = f"wayback exited with {self._process.returncode}: {self._log_tail()}"
                raise ReplayError(msg)
            try:
                with urlopen(f"{self.base_url}/", timeout=1):  # noqa: S310 — loopback only
                    return
            except HTTPError:
                return
            except (URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        msg = f"wayback did not answer within {self._startup_timeout_s}s: {self._log_tail()}"
        raise ReplayError(msg)

    def _log_tail(self) -> str:
        if self._tmp is None:
            return ""
        log = Path(self._tmp.name) / "wayback.log"
        return log.read_text(errors="replace")[-2000:] if log.exists() else ""
