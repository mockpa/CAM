"""Runs `camcore check` off the GUI thread, streaming its log lines."""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..check import check


class CheckRunner(QObject):
    """check.check(job, out_dir, sim=...) in a worker thread. `line` carries the
    log, `finished(ok, view_html)` the result: the viewer path only if it passed."""

    line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, job_path: Path, out_dir: Path, sim: bool, machine: Path | None = None) -> None:
        if self.busy:
            raise RuntimeError("a check is already running")
        self._thread = threading.Thread(target=self._run, args=(Path(job_path), Path(out_dir), sim, machine),
                                        daemon=True)
        self._thread.start()

    def wait(self, timeout=None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, job_path, out_dir, sim, machine):
        try:
            ok = check(job_path, out_dir, machine, sim=sim, log=self.line.emit)
        except Exception:                     # noqa: BLE001 - show it in the report, never kill the app
            self.line.emit(traceback.format_exc())
            ok = False
        view = out_dir / f"{job_path.stem.removesuffix('_job')}_view.html"
        self.finished.emit(ok, str(view) if ok and view.exists() else "")
