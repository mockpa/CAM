"""The relief designer as a dialog. Same questions, suggestions and checks as
`camcore relief` (relief.cli.QUESTIONS), with a shaded preview of the prepared
relief; it writes the same files, and the main window then runs `camcore check`
on every job."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from ..check import DEFAULT_MACHINE
from ..relief.cli import QUESTIONS, Session, design, field_of
from ..relief.preview import shaded
from ..wizard import DEFAULT_LIBRARY, WizardError

PREVIEW_KEYS = ("mode", "stl_view", "rotate", "mirror", "width", "height", "fit", "blur", "black", "white",
                "gamma", "invert", "relief", "depth", "pin_relief")


def relief_mm(a: dict) -> float:
    """How high the relief stands in the chosen design (for the shading)."""
    return {"lamellas": a.get("relief"), "3d": a.get("depth"), "pins": a.get("pin_relief")}.get(a.get("mode")) or 20.0


class ReliefDialog(QDialog):
    def __init__(self, source: Path, out_dir: Path, library_path: Path | None = None,
                 machine_path: Path | None = None, name: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Relief: {Path(source).name}")
        self.out_dir = Path(out_dir)
        self.name = name or Path(source).stem
        self.library_path = Path(library_path or DEFAULT_LIBRARY)
        self.machine_path = Path(machine_path) if machine_path else None
        lib = json.loads(self.library_path.read_text())
        mdata = json.loads((self.machine_path or DEFAULT_MACHINE).read_text())
        self.session = Session(source, lib, mdata, self.library_path)
        self.jobs: list[Path] | None = None
        self._shown = None

        top = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel(f"{Path(source).name}<br>tool library {self.library_path}<br>"
                              f"machine {self.machine_path or DEFAULT_MACHINE}"))
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
            msg.setWordWrap(True)
            label = QLabel(q.key.replace("_", " "))
            label.setWordWrap(True)
            form.addRow(label, w)
            form.addRow("", msg)
            self.rows.append([q, w, msg, False, label])
        inner = QWidget()
        inner.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(520)
        left.addWidget(scroll)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020;")
        self.error.setWordWrap(True)
        left.addWidget(self.error)
        self.sim = QCheckBox("Run the LinuxCNC sim on every job (needs Docker, a few minutes each)")
        self.sim.setChecked(True)
        self.sim.setToolTip("Off: quick checks only; the programs are then not ready for the machine")
        left.addWidget(self.sim)
        left.addWidget(QLabel(f"The files go to {self.out_dir}"))
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Write and check")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        left.addWidget(self.buttons)
        top.addLayout(left, 3)
        right = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 360)
        right.addWidget(self.preview, 1)
        self.preview_note = QLabel("Seen from the front, light from the upper left")
        right.addWidget(self.preview_note)
        top.addLayout(right, 4)
        self.refresh()

    def _picked(self, q):
        for row in self.rows:
            if row[0] is q:
                row[3] = True
        self.refresh()

    def widget(self, key):
        return next(r[1] for r in self.rows if r[0].key == key)

    def message(self, key) -> str:
        return next(r[2] for r in self.rows if r[0].key == key).text()

    def visible(self, key) -> bool:
        return not next(r for r in self.rows if r[0].key == key)[1].isHidden()

    def refresh(self) -> bool:
        """Re-derive every suggestion from the answers above it and check each answer;
        hide what does not apply to the design; questions below the first invalid one
        wait. Returns all-valid."""
        s = self.session
        s.a = {}
        ok = True
        for row in self.rows:
            q, w, msg, chosen, label = row
            if not ok:                      # waits for the answer above
                w.setEnabled(False)
                msg.setVisible(False)
                continue
            w.setEnabled(True)
            shown = q.applies(s)
            gated = q.gate is not None and not s.a.get(q.gate)
            for x in (w, label):
                x.setVisible(shown and not gated)
            msg.setVisible(False)
            if not shown:
                continue
            if gated:                       # "adjust" is no: the suggestion, as the CLI takes it
                s.a[q.key] = q.parse(s)(str(q.default(s)))
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
            except WizardError as e:
                msg.setText(str(e))
                msg.setVisible(True)
                ok = False
        self.error.clear()
        self.error.setVisible(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        self._update_preview()
        return ok

    def _update_preview(self):
        a = self.session.a
        if not all(k in a for k in ("width", "height", "fit", "blur", "black", "white", "gamma", "invert")):
            return                          # keep the last good picture while an answer is invalid
        key = tuple(a.get(k) for k in PREVIEW_KEYS)
        if key == self._shown:
            return
        try:
            img = shaded(field_of(self.session), relief_mm(a))
        except ValueError:
            return
        self._shown = key
        h, w = img.shape
        self.image = QImage(img.tobytes(), w, h, w, QImage.Format_Grayscale8).copy()
        self.preview.setPixmap(QPixmap.fromImage(self.image))

    def accept(self):
        if not self.refresh():
            return
        try:
            self.jobs = design(self.session, self.out_dir, self.name, self.library_path, self.machine_path)
        except ValueError as e:              # the design checks (board, parts, tools)
            self.error.setText(str(e))
            self.error.setVisible(True)
            return
        super().accept()
