"""The rotary wizard as a dialog. Same questions, suggestions and checks as
`camcore rotary-wizard` (wizard.QUESTIONS); it writes the same job file, and
the main window then runs `camcore check` on it."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QVBoxLayout)

from ..check import DEFAULT_MACHINE
from ..wizard import DEFAULT_LIBRARY, QUESTIONS, Session, WizardError, write_job


class WizardDialog(QDialog):
    def __init__(self, stl: Path, out_dir: Path, library_path: Path | None = None,
                 machine_path: Path | None = None, parent=None):
        super().__init__(parent)
        from ..stl import load_stl
        self.setWindowTitle(f"Rotary wizard: {Path(stl).name}")
        self.out_dir = Path(out_dir)
        self.library_path = Path(library_path or DEFAULT_LIBRARY)
        self.machine_path = Path(machine_path) if machine_path else None
        raw = load_stl(stl)
        lib = json.loads(self.library_path.read_text())
        mdata = json.loads((self.machine_path or DEFAULT_MACHINE).read_text())
        self.session = Session(stl, raw, lib, mdata, self.library_path)
        self.job_path = None

        v = QVBoxLayout(self)
        ext = raw.bounds[1] - raw.bounds[0]
        v.addWidget(QLabel(f"{Path(stl).name}: {len(raw.faces)} triangles, "
                           f"{ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm"
                           + ("" if raw.is_watertight else " (not watertight: check the result in the viewer)")
                           + f"<br>tool library {self.library_path}<br>machine {self.machine_path or DEFAULT_MACHINE}"))
        form = QFormLayout()
        self.rows = []                  # [question, widget, message, picked by the user, label]
        for q in QUESTIONS:
            if q.options:
                w = QComboBox()
                w.textActivated.connect(lambda _t, q=q: self._picked(q))
            else:
                w = QLineEdit()
                w.textEdited.connect(lambda _t: self.refresh())
            msg = QLabel()
            msg.setStyleSheet("color: #b00020;")
            label = QLabel(q.key.replace("_", " "))
            form.addRow(label, w)
            form.addRow("", msg)
            self.rows.append([q, w, msg, False, label])
        v.addLayout(form)
        self.placed = QLabel()
        v.addWidget(self.placed)
        self.sim = QCheckBox("Run the LinuxCNC sim (needs Docker, a few minutes)")
        self.sim.setChecked(True)
        self.sim.setToolTip("Off: a quick check only; the program is then not ready for the machine")
        v.addWidget(self.sim)
        v.addWidget(QLabel(f"The job goes to {self.out_dir}"))
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Write job and check")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self.refresh()

    def _picked(self, q):
        for row in self.rows:
            if row[0] is q:
                row[3] = True
        self.refresh()

    def widget(self, key):
        return next(r[1] for r in self.rows if r[0].key == key)

    def refresh(self) -> bool:
        """Re-derive every suggestion from the answers above it and check each
        answer; questions below the first invalid one wait. Returns all-valid."""
        s = self.session
        s.a = {}
        ok = True
        for row in self.rows:
            q, w, msg, chosen, label = row
            w.setEnabled(ok)
            if not ok:
                msg.clear()
                continue
            try:
                label.setText(q.text(s))
                default = str(q.default(s))
                label.setToolTip(f"{q.text(s)}\n\nsuggested: {default}")
                w.setToolTip(label.toolTip())
                if q.options:
                    opts = [str(o) for o in q.options(s)]
                    keep = w.currentText() if chosen and w.currentText() in opts else default
                    w.blockSignals(True)
                    w.clear()
                    w.addItems(opts)
                    w.setCurrentText(keep)
                    w.blockSignals(False)
                    text = keep
                else:
                    w.setPlaceholderText(default)
                    text = w.text().strip() or default
                s.a[q.key] = q.parse(s)(text)
                msg.clear()
                msg.setVisible(False)
            except WizardError as e:
                msg.setText(str(e))
                msg.setVisible(True)
                ok = False
            if ok and q.key == "rotate_x":
                length, r_max = s.placed()
                self.placed.setText(f"placed on the axis: X 0 .. {length:.2f} mm, largest radius {r_max:.2f} mm")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        return ok

    def accept(self):
        if not self.refresh():
            return
        self.job_path = write_job(self.session, self.out_dir, self.library_path, self.machine_path)
        super().accept()
