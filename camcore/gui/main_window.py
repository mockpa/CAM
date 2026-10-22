"""camcore-gui main window: edit a job, preview it, export it through `camcore check`."""
from __future__ import annotations

import os
import shutil
import tempfile
import webbrowser
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..project import Project
from .panels import JobPanel, OpsPanel, ShapesPanel
from .runner import CheckRunner
from .tool_editor import ToolEditor


def web_view():
    """The embedded viewer, or None where QtWebEngine is missing or disabled
    (CAMCORE_GUI_NO_WEBENGINE=1); the viewer then opens in the browser."""
    if os.environ.get("CAMCORE_GUI_NO_WEBENGINE"):
        return None
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError:
        return None
    return QWebEngineView()


class ViewerPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.label = QLabel("Preview or Export to see the program here.")
        self.open_btn = QPushButton("Open in browser")
        self.open_btn.setToolTip("Show the same viewer in a full browser window")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(lambda: webbrowser.open(Path(self.path).resolve().as_uri()))
        bar.addWidget(self.label, 1)
        bar.addWidget(self.open_btn)
        v.addLayout(bar)
        self.web = web_view()
        if self.web is not None:
            v.addWidget(self.web, 1)
        else:
            v.addStretch()
        self.path = None

    def show_file(self, path: str | None, caption: str = ""):
        self.path = path
        self.open_btn.setEnabled(bool(path))
        self.label.setText(caption)
        if self.web is not None:
            if path:
                self.web.load(QUrl.fromLocalFile(str(Path(path).resolve())))
            else:
                self.web.setHtml("")


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.resize(1400, 900)
        self.preview_dir = Path(tempfile.mkdtemp(prefix="camcore-preview-"))
        self.runner = CheckRunner(self)
        self.runner.line.connect(self._log)
        self.runner.finished.connect(self._finished)
        self.out_dir_custom = None
        self.last_result = None             # (ok, viewer path, exported) of the last check

        self.project = Project()
        self.job_panel = JobPanel(self.project)
        self.shapes_panel = ShapesPanel(self.project)
        self.ops_panel = OpsPanel(self.project)
        self.tools_panel = ToolEditor()
        for p in (self.job_panel, self.shapes_panel, self.ops_panel):
            p.changed.connect(self._changed)
        self.shapes_panel.changed.connect(self.ops_panel.refresh)
        self.job_panel.library_changed.connect(self._library_moved)
        self.tools_panel.saved.connect(self._library_saved)

        self.left = QTabWidget()
        for panel, label, tip in [
                (self.ops_panel, "Operations", "What gets cut, in which order"),
                (self.shapes_panel, "Shapes && models", "The 2D shapes and STL models the operations cut"),
                (self.job_panel, "Job", "Stock, material, machine and settings for the whole job"),
                (self.tools_panel, "Tool library", "The tools and their feeds and speeds per material")]:
            self.left.setTabToolTip(self.left.addTab(panel, label), tip)
        self.viewer = ViewerPane()
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(QFont("monospace"))
        self.right = QTabWidget()
        self.right.setTabToolTip(self.right.addTab(self.viewer, "Viewer"),
                                 "The 3D viewer of the last checked program")
        self.right.setTabToolTip(self.right.addTab(self.report, "Report"),
                                 "The log of the last check: what was checked and what was found")
        split = QSplitter()
        split.addWidget(self.left)
        split.addWidget(self.right)
        split.setSizes([560, 840])
        self.setCentralWidget(split)
        self._actions()
        self.statusBar()
        if path:
            self.open(path)
        else:
            self._set_project(Project())

    # --- menus and toolbar -----------------------------------------------------------------------

    def _actions(self):
        def act(text, fn, key=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if key:
                a.setShortcut(QKeySequence(key))
            return a
        self.a_new = act("&New", self.new, QKeySequence.New)
        self.a_open = act("&Open…", self.open_dialog, QKeySequence.Open)
        self.a_save = act("&Save", self.save, QKeySequence.Save)
        self.a_save_as = act("Save &As…", self.save_as, QKeySequence.SaveAs)
        self.a_quit = act("&Quit", self.close, QKeySequence.Quit)
        self.a_wizard = act("Rotary &wizard…", self.wizard)
        self.a_new.setToolTip("Start a new, empty job")
        self.a_open.setToolTip("Open a job file")
        self.a_save.setToolTip("Save the job file")
        self.a_save_as.setToolTip("Save the job under another name; relative paths are adjusted")
        self.a_wizard.setToolTip("Turned parts from an STL: answer a few questions, get a checked rotary job")
        self.a_relief = act("&Relief…", self.relief)
        self.a_relief.setToolTip("Lamellas, a 3D relief or pins from an image or STL: writes the jobs and "
                                 "paperwork and checks every job")
        self.a_preview = act("&Preview", self.preview, "Ctrl+R")
        self.a_preview.setToolTip("Generate and dry-run the program (no LinuxCNC sim) and show it. "
                                  "Nothing is written to the output folder.")
        self.a_export = act("&Export", self.export, "Ctrl+E")
        self.a_export.setToolTip("camcore check with the LinuxCNC sim: writes the .ngc to the output "
                                 "folder only if everything passes")
        m = self.menuBar()
        f = m.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as):
            f.addAction(a)
        f.addSeparator()
        f.addAction(self.a_quit)
        r = m.addMenu("&Run")
        r.addAction(self.a_preview)
        r.addAction(self.a_export)
        t = m.addMenu("&Tools")
        t.addAction(self.a_wizard)
        t.addAction(self.a_relief)
        tb = QToolBar("Main")
        self.addToolBar(tb)
        for a in (self.a_open, self.a_save, self.a_wizard, self.a_relief):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_preview)
        tb.addAction(self.a_export)
        out_tip = "Where Export writes the checked program (default: out/ next to the job)"
        out_label = QLabel("  Output folder: ")
        out_label.setToolTip(out_tip)
        tb.addWidget(out_label)
        self.out_edit = QLineEdit()
        self.out_edit.setToolTip(out_tip)
        self.out_edit.setMinimumWidth(300)
        self.out_edit.textEdited.connect(lambda t: setattr(self, "out_dir_custom", t.strip() or None))
        tb.addWidget(self.out_edit)
        b = QPushButton("…")
        b.setToolTip("Choose the output folder")
        b.clicked.connect(self._browse_out)
        tb.addWidget(b)

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", str(self.out_dir()))
        if d:
            self.out_dir_custom = d
            self.out_edit.setText(d)

    def out_dir(self) -> Path:
        return Path(self.out_dir_custom) if self.out_dir_custom else self.project.base / "out"

    # --- project ---------------------------------------------------------------------------------

    def _set_project(self, project: Project):
        self.project = project
        self.job_panel.set_project(project)
        self.shapes_panel.set_project(project)
        self.ops_panel.set_project(project)
        self.tools_panel.load(project.library_path())
        if not self.out_dir_custom:
            self.out_edit.setText(str(self.out_dir()))
        self.viewer.show_file(None, "Preview or Export to see the program here.")
        self._title()

    def _title(self):
        where = str(self.project.path) if self.project.path else "not saved"
        self.setWindowTitle(f"{'*' if self.project.dirty else ''}{self.project.name} ({where}) - camcore")

    def _changed(self):
        self._title()

    def _library_moved(self):
        self.tools_panel.load(self.project.library_path())
        self.ops_panel.refresh()

    def _library_saved(self):
        self.job_panel.refresh_materials()
        self.ops_panel.refresh()

    def _discard_ok(self) -> bool:
        if self.tools_panel.dirty:
            r = QMessageBox.question(self, "Tool library", "Save the changes to the tool library?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel or (r == QMessageBox.Save and not self.tools_panel.save()):
                return False
        if not self.project.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", f"Save the changes to {self.project.name}?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new(self):
        if self._discard_ok():
            self.out_dir_custom = None
            self._set_project(Project())

    def open_dialog(self):
        if not self._discard_ok():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Open job", str(self.project.base), "Jobs (*.json)")
        if f:
            self.open(f)

    def open(self, path):
        try:
            project = Project.load(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Open", f"Cannot open {path}:\n{e}")
            return
        if "operations" not in project.job:
            QMessageBox.warning(self, "Open", f"{path} is not a camcore job (no \"operations\").")
            return
        self.out_dir_custom = None
        self._set_project(project)

    def save(self) -> bool:
        if self.project.path is None:
            return self.save_as()
        self.project.save()
        self._title()
        return True

    def save_as(self) -> bool:
        f, _ = QFileDialog.getSaveFileName(self, "Save job", str(self.project.base / f"{self.project.name}_job.json"),
                                           "Jobs (*.json)")
        if not f:
            return False
        self.project.save(f)
        self._set_project(self.project)    # paths may have been rebased
        return True

    def closeEvent(self, ev):
        if self.runner.busy:
            QMessageBox.information(self, "camcore", "A check is still running; wait for it to finish.")
            ev.ignore()
        elif self._discard_ok():
            shutil.rmtree(self.preview_dir, ignore_errors=True)
            ev.accept()
        else:
            ev.ignore()

    # --- checks ----------------------------------------------------------------------------------

    def _ready(self) -> bool:
        """Checks run on the saved job file: save first (Save As if it never was)."""
        if self.runner.busy:
            return False
        if self.tools_panel.dirty and not self.tools_panel.save():
            self.left.setCurrentWidget(self.tools_panel)
            return False
        if self.project.dirty or self.project.path is None:
            if not self.save():
                return False
        return True

    def preview(self):
        """camcore check without the sim into a scratch folder: a look, not a program."""
        if self._ready():
            self._start(self.preview_dir, sim=False)

    def export(self):
        if self._ready():
            self._start(self.out_dir(), sim=True)

    def _start(self, out_dir: Path, sim: bool, job_path: Path | None = None):
        self._exporting = sim
        self.report.clear()
        self.right.setCurrentWidget(self.report)
        self.a_preview.setEnabled(False)
        self.a_export.setEnabled(False)
        self.statusBar().showMessage("checking…" if not sim else "checking with the LinuxCNC sim…")
        self.runner.start(job_path or self.project.path, out_dir, sim)

    def _log(self, line: str):
        self.report.appendPlainText(line)

    def _finished(self, ok: bool, view: str):
        queue = getattr(self, "_queue", [])
        if ok and queue:                    # more relief boards to check: the next one, same settings
            self._start(self._queue_out, self._exporting, queue.pop(0))
            return
        self._queue = []
        self.a_preview.setEnabled(True)
        self.a_export.setEnabled(True)
        self.last_result = (ok, view, self._exporting)
        if ok:
            what = "Exported (sim passed)" if self._exporting else "Preview (dry run only, not for the machine)"
            self.viewer.show_file(view, f"{what}: {view}")
            self.right.setCurrentWidget(self.viewer)
            self.statusBar().showMessage(what)
        else:
            self.statusBar().showMessage("FAILED: see the report")

    def wizard(self):
        from .wizard_dialog import WizardDialog
        if not self._discard_ok():
            return
        stl, _ = QFileDialog.getOpenFileName(self, "Rotary wizard: STL model", str(self.project.base), "STL (*.stl)")
        if not stl:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Rotary wizard: where the job goes",
                                             str(Path(stl).parent / "out" / f"{Path(stl).stem}_job.json"),
                                             "Jobs (*.json)")
        if not out:
            return
        self.run_wizard(WizardDialog(Path(stl), Path(out).parent, self.project.library_path(),
                                     self._machine_path(), self))

    def relief(self):
        from .relief_dialog import ReliefDialog
        if not self._discard_ok():
            return
        src, _ = QFileDialog.getOpenFileName(self, "Relief: image or STL", str(self.project.base),
                                             "Images and STL (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.stl)")
        if not src:
            return
        out = QFileDialog.getExistingDirectory(self, "Relief: output folder", str(Path(src).parent / "out"))
        if not out:
            return
        self.run_relief(ReliefDialog(Path(src), Path(out), self.project.library_path(), self._machine_path(), parent=self))

    def run_relief(self, dialog) -> bool:
        """Show the dialog; on OK open the first job it wrote and check every job in turn."""
        if not dialog.exec() or dialog.jobs is None:
            return False
        if not dialog.jobs:                 # pins: nothing to machine
            QMessageBox.information(self, "Relief", f"Pins have nothing to machine: the cut list and pin plan "
                                                    f"are in {dialog.out_dir}.")
            return True
        self.open(dialog.jobs[0])
        self.out_dir_custom = str(dialog.out_dir)
        self.out_edit.setText(self.out_dir_custom)
        self._queue, self._queue_out = list(dialog.jobs[1:]), dialog.out_dir
        if self._queue:
            self.statusBar().showMessage(f"{len(dialog.jobs)} boards: checking them one after another")
        self._start(dialog.out_dir, sim=dialog.sim.isChecked(), job_path=dialog.jobs[0])
        return True

    def _machine_path(self) -> Path | None:
        m = self.project.job.get("machine")
        return self.project.base / m if m else None

    def run_wizard(self, dialog) -> bool:
        """Show the dialog; on OK open the job it wrote and check it (as the CLI wizard does)."""
        if not dialog.exec() or dialog.job_path is None:
            return False
        self.open(dialog.job_path)
        self.out_dir_custom = str(dialog.out_dir)
        self.out_edit.setText(self.out_dir_custom)
        self._start(dialog.out_dir, sim=dialog.sim.isChecked())
        return True
