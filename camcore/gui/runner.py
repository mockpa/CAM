"""Runs `camcore check` off the GUI thread, streaming its log lines.

The cyclic garbage collector runs in whichever thread happens to allocate. Left on
during a check, it can free a Qt object that became garbage on the GUI thread (a closed
dialog in a reference cycle) from the worker thread, and Qt crashes. So garbage is
collected on the GUI thread before a check, automatic collection is off while it runs
(reference counting still frees almost everything), and it is back on, with a
collection on the GUI thread, when the check has finished."""
from __future__ import annotations

import gc
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..check import check


class CheckRunner(QObject):
    """check.check(job, out_dir, sim=...) in a worker thread. `line` carries the
    log, `progress(line, total)` the LinuxCNC sim's place in the program, `finished(ok,
    view_html)` the result: the viewer path only if it passed."""

    line = Signal(str)
    progress = Signal(int, int)
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._gc_was_on = None
        self.finished.connect(self._gc_back)      # runs on the GUI thread (this object lives there)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, job_path: Path, out_dir: Path, sim: bool, machine: Path | None = None) -> None:
        if self.busy:
            raise RuntimeError("a check is already running")
        gc.collect()
        if self._gc_was_on is None:
            self._gc_was_on = gc.isenabled()
        gc.disable()
        self._thread = threading.Thread(target=self._run, args=(Path(job_path), Path(out_dir), sim, machine),
                                        daemon=True)
        self._thread.start()

    def _gc_back(self, *_):
        if self._gc_was_on:
            gc.enable()
            gc.collect()
        self._gc_was_on = None

    def wait(self, timeout=None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, job_path, out_dir, sim, machine):
        try:
            ok = check(job_path, out_dir, machine, sim=sim, log=self.line.emit, progress=self.progress.emit)
        except Exception:                     # noqa: BLE001 - show it in the report, never kill the app
            self.line.emit(traceback.format_exc())
            ok = False
        view = out_dir / f"{job_path.stem.removesuffix('_job')}_view.html"
        self.finished.emit(ok, str(view) if ok and view.exists() else "")
