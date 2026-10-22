"""The editing panels of the main window: job settings, shapes and models,
operations. They edit Project.job in place and emit `changed`; none of them
knows how toolpaths are made."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from ..check import DEFAULT_MACHINE
from ..post import POSTS
from ..tooltips import JOB_TIPS, OP_TIPS, STRUCT_TIPS, param_tip
from ..project import (CHOICES, OP_KINDS, Project, describe_op, effective, format_value, materials, op_params,
                       target_key, unique_name, unknown_keys)
from .widgets import BAD, JsonEdit, combo

SOURCE = {"cutting": "from the tool's {material} data", "defaults": "from the job defaults"}


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def add_row(form: QFormLayout, label: str, widget: QWidget, tip: str = ""):
    """A form row whose label and field both explain themselves on hover."""
    lab = QLabel(label)
    lab.setToolTip(tip)
    widget.setToolTip(tip)
    form.addRow(lab, widget)
    return lab


def _path_row(edit: QLineEdit, browse) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit)
    b = QPushButton("…")
    b.setToolTip("Choose the file")
    b.setFixedWidth(30)
    b.clicked.connect(browse)
    h.addWidget(b)
    return w


class JobPanel(QWidget):
    """Job-level keys: post, material, tool library, machine, order, stock, defaults, rotary/index."""

    changed = Signal()
    library_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.set_project(project)

    def set_project(self, project: Project):
        self.project = p = project
        _clear(self.form)
        job = p.job
        add_row(self.form, "Post", combo(POSTS, job.get("post", "linuxcnc"), lambda v: self._set("post", v)),
                JOB_TIPS["post"])
        self.material = combo(materials(p.library()), job.get("material"), lambda v: self._set("material", v),
                              empty_label="(none: op values and defaults only)")
        self.material_label = add_row(self.form, "Material", self.material, JOB_TIPS["material"])
        self.library = QLineEdit(job.get("tool_library", ""))
        self.library.textEdited.connect(lambda t: self._set_library(t.strip() or None))
        self.library.setToolTip(JOB_TIPS["tool_library"])
        add_row(self.form, "Tool library", _path_row(self.library, self._browse_library), JOB_TIPS["tool_library"])
        self.machine = QLineEdit(job.get("machine", ""))
        self.machine.setPlaceholderText(f"default: {DEFAULT_MACHINE}")
        self.machine.textEdited.connect(lambda t: self._set("machine", t.strip() or None))
        self.machine.setToolTip(JOB_TIPS["machine"])
        add_row(self.form, "Machine", _path_row(self.machine, self._browse_machine), JOB_TIPS["machine"])
        add_row(self.form, "Tool order", combo(["minimize_changes"], job.get("tool_order"),
                                               lambda v: self._set("tool_order", v), empty_label="as written"),
                JOB_TIPS["tool_order"])
        for key, hint in [("stock", '{"type": "box", "x": [0, 100], "y": [0, 100], "z": [-10, 0]}'),
                          ("defaults", '{"safe_z": 5}'),
                          ("rotary", 'wrap around A, e.g. {"diameter": 40}'),
                          ("index", 'indexed 3+1, e.g. {"sides": 4} or [0, 180]'),
                          ("index_clearance", "5"),
                          ("post_options", "{}")]:
            add_row(self.form, key.replace("_", " ").capitalize(),
                    JsonEdit(job.get(key), lambda v, k=key: self._set(k, v), hint, tip=JOB_TIPS[key]), JOB_TIPS[key])

    def _set(self, key, value):
        if value is None:
            if self.project.job.pop(key, None) is not None:
                self.project.changed()
        elif self.project.job.get(key) != value:
            self.project.job[key] = value
            self.project.changed()
        self.changed.emit()

    def _set_library(self, value):
        self._set("tool_library", value)
        self.library_changed.emit()

    def refresh_materials(self):
        cur = self.project.job.get("material")
        new = combo(materials(self.project.library()), cur, lambda v: self._set("material", v),
                    empty_label="(none: op values and defaults only)")
        new.setToolTip(JOB_TIPS["material"])
        self.form.replaceWidget(self.material, new)
        self.material.deleteLater()
        self.material = new

    def _browse_library(self):
        f, _ = QFileDialog.getOpenFileName(self, "Tool library", str(self.project.base), "JSON (*.json)")
        if f:
            self.library.setText(self.project.rel(f))
            self._set_library(self.library.text())
            self.refresh_materials()

    def _browse_machine(self):
        f, _ = QFileDialog.getOpenFileName(self, "Machine", str(DEFAULT_MACHINE.parent), "JSON (*.json)")
        if f:
            self.machine.setText(self.project.rel(f))
            self._set("machine", self.machine.text())


class ShapesPanel(QWidget):
    """Named shapes and models: add from files or as rect/circle, rename, remove, edit as JSON."""

    changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for label, fn, tip in [
                ("Rectangle", self.add_rect, "Add a rectangle; edit its numbers in the JSON box below"),
                ("Circle", self.add_circle, "Add a circle; edit its numbers in the JSON box below"),
                ("SVG / DXF…", self.import_shape, "Import a drawing as a shape (closed contours only)"),
                ("STL model…", self.import_model, "Add a 3D model for 3D or rotary operations"),
                ("Rename…", self.rename, "Rename the selected shape or model; operations using it follow"),
                ("Remove", self.remove, "Delete the selected shape or model")]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        split = QSplitter(Qt.Vertical)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("select a shape or model to edit it as JSON")
        self.editor.setToolTip("The selected shape or model as JSON, with the same keys as in the job file. "
                               "Invalid JSON turns red and is not applied")
        self.editor.textChanged.connect(self._edited)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020;")
        split.addWidget(self.list)
        split.addWidget(self.editor)
        v.addWidget(split)
        v.addWidget(self.error)
        self.set_project(project)

    def set_project(self, project: Project, select: tuple | None = None):
        self.project = project
        self.entries = [("shapes", k) for k in project.job.get("shapes", {})] + \
                       [("models", k) for k in project.job.get("models", {})]
        self.list.blockSignals(True)
        self.list.clear()
        for section, name in self.entries:
            spec = project.job[section][name]
            self.list.addItem(f"{'shape' if section == 'shapes' else 'model'}  {name}  "
                              f"({spec.get('type', 'stl')}{', ' + spec['file'] if 'file' in spec else ''})")
        self.list.blockSignals(False)
        row = self.entries.index(select) if select in self.entries else (0 if self.entries else -1)
        self.list.setCurrentRow(row)
        self._show(row)

    def current(self):
        r = self.list.currentRow()
        return self.entries[r] if 0 <= r < len(self.entries) else None

    def _show(self, row):
        self._loading = True
        cur = self.current()
        self.editor.setPlainText(json.dumps(self.project.job[cur[0]][cur[1]], indent=2) if cur else "")
        self.editor.setEnabled(cur is not None)
        self.error.clear()
        self._loading = False

    def _edited(self):
        cur = self.current()
        if self._loading or cur is None:
            return
        try:
            spec = json.loads(self.editor.toPlainText())
            if not isinstance(spec, dict):
                raise ValueError("a shape or model is a JSON object")
        except ValueError as e:
            self.editor.setStyleSheet(BAD)
            self.error.setText(str(e))
            return
        self.editor.setStyleSheet("")
        self.error.clear()
        self.project.job[cur[0]][cur[1]] = spec
        self.project.changed()
        self.changed.emit()

    def _added(self, section, name):
        self.set_project(self.project, (section, name))
        self.changed.emit()

    def add_rect(self):
        self._added("shapes", self.project.add_shape({"type": "rect", "x": 0, "y": 0, "w": 50, "h": 30}, "rect"))

    def add_circle(self):
        self._added("shapes", self.project.add_shape({"type": "circle", "cx": 25, "cy": 25, "r": 20}, "circle"))

    def import_shape(self):
        f, _ = QFileDialog.getOpenFileName(self, "Import drawing", str(self.project.base),
                                           "Drawings (*.svg *.dxf)")
        if f:
            self._added("shapes", self.project.add_shape_file(f))

    def import_model(self):
        f, _ = QFileDialog.getOpenFileName(self, "Add STL model", str(self.project.base), "STL (*.stl)")
        if f:
            self._added("models", self.project.add_model_file(f))

    def rename(self):
        cur = self.current()
        if cur is None:
            return
        new, ok = QInputDialog.getText(self, "Rename", "New name (operations using it follow):", text=cur[1])
        if ok and new.strip():
            self._added(cur[0], self.project.rename(cur[0], cur[1], new.strip()))

    def remove(self):
        cur = self.current()
        if cur is None:
            return
        users = self.project.users(*cur)
        if users and QMessageBox.question(self, "Remove", f"{cur[1]} is used by {', '.join(users)}. "
                                          "Remove it anyway?") != QMessageBox.Yes:
            return
        del self.project.job[cur[0]][cur[1]]
        self.project.changed()
        self.set_project(self.project)
        self.changed.emit()


class OpForm(QWidget):
    """The keys of one operation. Parameter fields show, when empty, the value the
    operation will get anyway (tool data for the material, job defaults, op default)."""

    changed = Signal()

    def __init__(self, project: Project, op: dict, parent=None):
        super().__init__(parent)
        self.project, self.op = project, op
        lib = project.library()
        kind = op["op"]
        self.form = form = QFormLayout(self)
        add_row(form, "Operation", QLabel(f"<b>{kind}</b>"), OP_TIPS.get(kind, ""))
        add_row(form, "Id", JsonEdit(op.get("id"), lambda v: self._set("id", v), "names it for \"after\"",
                                     allow_text=True, tip=STRUCT_TIPS["id"]), STRUCT_TIPS["id"])
        add_row(form, "After", JsonEdit(op.get("after"), lambda v: self._set("after", v),
                                        'ids this waits for, e.g. ["rough"]', tip=STRUCT_TIPS["after"]),
                STRUCT_TIPS["after"])
        add_row(form, "Tool", combo(sorted(lib), op.get("tool"), lambda v: self._set("tool", v)), STRUCT_TIPS["tool"])
        tk = target_key(kind)
        if tk == "points":
            add_row(form, "Points", JsonEdit(op.get("points"), lambda v: self._set("points", v), "[[x, y], ...]",
                                             tip=STRUCT_TIPS["points"]), STRUCT_TIPS["points"])
        else:
            names = sorted(project.job.get("shapes" if tk == "shape" else "models", {}))
            add_row(form, tk.capitalize(), combo(names, op.get(tk), lambda v: self._set(tk, v)), STRUCT_TIPS[tk])
        if kind in ("vcarve", "inlay"):         # inlay: required, the flat areas
            add_row(form, "Clear tool", combo(sorted(lib), op.get("clear_tool"),
                                              lambda v: self._set("clear_tool", v), empty_label="(none)"),
                    STRUCT_TIPS["clear_tool"])
            add_row(form, "Clear", JsonEdit(op.get("clear"), lambda v: self._set("clear", v),
                                            'clearing pocket parameters, e.g. {"stepover_ratio": 0.45}',
                                            tip=STRUCT_TIPS["clear"]), STRUCT_TIPS["clear"])
        if kind == "inlay":
            add_row(form, "Fit with", JsonEdit(op.get("fit_with"), lambda v: self._set("fit_with", v),
                                               "plug only: the pocket job, e.g. inlay_pocket_job.json",
                                               allow_text=True, tip=STRUCT_TIPS["fit_with"]), STRUCT_TIPS["fit_with"])
        if tk != "model":                       # rotary model ops turn A themselves
            add_row(form, "Index", JsonEdit(op.get("index"), lambda v: self._set("index", v),
                                            "A angles: 90, [0, 180], {\"sides\": 4}", tip=STRUCT_TIPS["index"]),
                    STRUCT_TIPS["index"])
            add_row(form, "Face z", JsonEdit(op.get("face_z"), lambda v: self._set("face_z", v),
                                             "top of each indexed face (default: the stock)",
                                             tip=STRUCT_TIPS["face_z"]), STRUCT_TIPS["face_z"])
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b00020;")
        form.addRow(self.warning)
        head = QLabel("<b>Parameters</b> (empty = inherited, shown in grey)")
        head.setToolTip("Hover over a parameter to see what it does and where its grey value comes from")
        form.addRow(head)
        self.fields = {}
        for p in op_params(kind):
            if p.name in CHOICES:
                w = combo(CHOICES[p.name], op.get(p.name), lambda v, k=p.name: self._set(k, v),
                          empty_label="", editable=True)
                w.lineEdit().setPlaceholderText("")
            else:
                w = JsonEdit(op.get(p.name), lambda v, k=p.name: self._set(k, v), allow_text=True)
            self.fields[p] = w
            add_row(form, p.name, w, param_tip(kind, p.name))
        self.hints()

    def _set(self, key, value):
        self.project.set_op_value(self.op, key, value)
        self.hints()
        self.changed.emit()

    def hints(self):
        """Placeholders for the empty fields, and problems that will stop run_job."""
        problems = []
        try:
            eff = effective(self.project.job, self.project.library(), self.op)
        except ValueError as e:
            eff = {}
            problems.append(str(e))
        if self.op.get("tool") not in self.project.library():
            problems.append(f"tool {self.op.get('tool')!r} is not in the tool library")
        tk = target_key(self.op["op"])
        if tk != "points":
            have = self.project.job.get("shapes" if tk == "shape" else "models", {})
            if not self.op.get(tk):
                problems.append(f"pick a {tk}" + ("" if have else f" (add one on the Shapes & models tab first)"))
            elif self.op[tk] not in have:
                problems.append(f"{tk} {self.op[tk]!r} is not in the job (renamed or removed?)")
        if self.op["op"] == "inlay" and not self.op.get("clear_tool"):
            problems.append("inlay needs a clear tool (an end mill for the flat areas)")
        bad = unknown_keys(self.op)
        if bad:
            problems.append(f"{self.op['op']} does not take {', '.join(bad)} (edit the job file to remove)")
        material = self.project.job.get("material")
        for p, w in self.fields.items():
            if p.name in eff and eff[p.name][1] != "op":
                v, src = eff[p.name]
                hint, tip = format_value(v), SOURCE[src].format(material=material)
            elif p.required:
                stock_type = (self.project.job.get("stock") or {}).get("type")
                if p.name == "stock_diameter" and stock_type == "cylinder":
                    hint, tip = "from the cylinder stock", "the job's cylinder stock sets it"
                elif p.name == "stock" and stock_type == "box":
                    hint, tip = "from the box stock", "the job's box stock sets it"
                else:
                    hint, tip = "required", "the operation needs a value"
                    if p.name not in eff:
                        problems.append(f"{p.name} is required")
            else:
                hint, tip = format_value(p.default), "the operation's default"
            (w.lineEdit() if hasattr(w, "lineEdit") else w).setPlaceholderText(hint)
            what = param_tip(self.op["op"], p.name)
            w.setToolTip(f"{what}\n\nEmpty: {hint} ({tip})" if what else tip)
            if hasattr(w, "tip"):
                w.tip = w.toolTip()
        self.warning.setText("\n".join(problems))



class OpsPanel(QWidget):
    """The ordered operation list and the form of the selected one."""

    changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        add = QToolButton()
        add.setText("Add")
        add.setToolTip("Add an operation at the end of the list")
        add.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(add)
        for k in OP_KINDS:
            menu.addAction(k, lambda k=k: self.add(k)).setToolTip(OP_TIPS[k])
        menu.setToolTipsVisible(True)
        add.setMenu(menu)
        bar.addWidget(add)
        for label, fn, tip in [("Up", lambda: self.move(-1), "Cut the selected operation earlier"),
                               ("Down", lambda: self.move(1), "Cut the selected operation later"),
                               ("Duplicate", self.duplicate, "Copy the selected operation, e.g. to run it with "
                                                             "another tool"),
                               ("Remove", self.remove, "Delete the selected operation")]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        split = QSplitter(Qt.Vertical)
        self.list = QListWidget()
        self.list.setToolTip("The operations, in cutting order. Select one to edit it below")
        self.list.currentRowChanged.connect(self._show)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        split.addWidget(self.list)
        split.addWidget(self.scroll)
        split.setSizes([150, 450])
        v.addWidget(split)
        self.form = None
        self.set_project(project)

    @property
    def ops(self):
        return self.project.job.setdefault("operations", [])

    def set_project(self, project: Project, row: int = 0):
        self.project = project
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([describe_op(o, i) for i, o in enumerate(self.ops)])
        self.list.blockSignals(False)
        row = min(row, len(self.ops) - 1)
        self.list.setCurrentRow(row)
        self._show(row)

    def _show(self, row):
        self.form = OpForm(self.project, self.ops[row]) if 0 <= row < len(self.ops) else None
        if self.form:
            self.form.changed.connect(self._form_changed)
        self.scroll.setWidget(self.form or QLabel("No operation selected. Add one with Add."))

    def _form_changed(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.ops):
            self.list.item(r).setText(describe_op(self.ops[r], r))
        self.changed.emit()

    def add(self, kind):
        self.project.add_op(kind)
        self.set_project(self.project, len(self.ops) - 1)
        self.changed.emit()

    def move(self, delta):
        self.set_project(self.project, self.project.move_op(self.list.currentRow(), delta))
        self.changed.emit()

    def duplicate(self):
        r = self.list.currentRow()
        if r < 0:
            return
        op = json.loads(json.dumps(self.ops[r]))
        if "id" in op:
            op["id"] = unique_name(op["id"], {o.get("id") for o in self.ops})
        self.ops.insert(r + 1, op)
        self.project.changed()
        self.set_project(self.project, r + 1)
        self.changed.emit()

    def remove(self):
        r = self.list.currentRow()
        if r < 0:
            return
        self.project.remove_op(r)
        self.set_project(self.project, max(0, r - 1))
        self.changed.emit()

    def refresh(self):
        """Re-read after the library or shapes changed (tool and shape lists, hints)."""
        self.set_project(self.project, max(0, self.list.currentRow()))
