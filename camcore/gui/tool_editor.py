"""Tool library editor: the tools of the job's library file and their feeds and
speeds per material. Edits a working copy; Save writes the file only if it
passes the checks load_library makes."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..project import CUTTING_KEYS, TOOL_KINDS, format_value, parse_value, save_library, tool_fields, unique_name


class ToolEditor(QWidget):
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path, self.data, self.dirty = None, {}, False
        v = QVBoxLayout(self)
        self.title = QLabel()
        v.addWidget(self.title)
        bar = QHBoxLayout()
        for label, fn in [("Add tool", self.add_tool), ("Remove tool", self.remove_tool),
                          ("Add material", self.add_material), ("Remove material", self.remove_material),
                          ("Save library", self.save), ("Revert", self.revert)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        self.fields = tool_fields()
        split = QSplitter(Qt.Vertical)
        self.tools = QTableWidget(0, 1 + len(self.fields))
        self.tools.setHorizontalHeaderLabels(["name"] + [f.name for f in self.fields])
        self.tools.currentCellChanged.connect(lambda r, *_: self._show_cutting(r))
        self.tools.itemChanged.connect(self._tool_edited)
        self.cutting = QTableWidget(0, 0)
        self.cutting.itemChanged.connect(self._cutting_edited)
        lower = QWidget()
        lv = QVBoxLayout(lower)
        lv.setContentsMargins(0, 0, 0, 0)
        self.cutting_title = QLabel()
        lv.addWidget(self.cutting_title)
        lv.addWidget(self.cutting)
        split.addWidget(self.tools)
        split.addWidget(lower)
        v.addWidget(split)
        self.status = QLabel()
        self.status.setStyleSheet("color: #b00020;")
        v.addWidget(self.status)

    # --- loading ---------------------------------------------------------------------------------

    def load(self, path: Path | None):
        self.path = Path(path) if path else None
        self.data = {}
        self.status.clear()
        if self.path is None:
            self.title.setText("This job names no tool library (\"tool_library\" on the Job tab).")
        else:
            try:
                self.data = json.loads(self.path.read_text())
                self.title.setText(f"Tool library: <b>{self.path}</b>")
            except (OSError, json.JSONDecodeError) as e:
                self.title.setText(f"Cannot read the tool library {self.path}: {e}")
                self.path = None
        self.dirty = False
        self._fill_tools()

    def revert(self):
        self.load(self.path)

    def names(self) -> list[str]:
        return list(self.data)

    def _fill_tools(self, select: int = 0):
        self._loading = True
        t = self.tools
        t.setRowCount(len(self.data))
        for r, (name, tool) in enumerate(self.data.items()):
            t.setItem(r, 0, QTableWidgetItem(name))
            for c, f in enumerate(self.fields, start=1):
                if f.name == "kind":
                    box = QComboBox()
                    box.addItems(TOOL_KINDS)
                    box.setCurrentText(tool.get("kind", "flat"))
                    box.textActivated.connect(lambda v, n=name: self._set_tool(n, "kind", v))
                    t.setCellWidget(r, c, box)
                    t.setItem(r, c, QTableWidgetItem())
                elif f.type in (bool, "bool"):
                    it = QTableWidgetItem()
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if tool.get(f.name, f.default) else Qt.Unchecked)
                    t.setItem(r, c, it)
                else:
                    it = QTableWidgetItem(format_value(tool.get(f.name)))
                    it.setToolTip(f"default: {f.default}")
                    t.setItem(r, c, it)
        t.resizeColumnsToContents()
        self._loading = False
        if self.data:
            t.setCurrentCell(min(select, len(self.data) - 1), 0)
        self._show_cutting(t.currentRow())

    def current(self) -> str | None:
        r = self.tools.currentRow()
        return list(self.data)[r] if 0 <= r < len(self.data) else None

    def _show_cutting(self, row):
        self._loading = True
        name = list(self.data)[row] if 0 <= row < len(self.data) else None
        cut = self.data[name].get("cutting", {}) if name else {}
        keys = list(CUTTING_KEYS) + sorted({k for d in cut.values() for k in d} - set(CUTTING_KEYS))
        self.cut_keys = keys
        c = self.cutting
        c.clear()
        c.setColumnCount(1 + len(keys))
        c.setHorizontalHeaderLabels(["material"] + keys)
        c.setRowCount(len(cut))
        for r, (mat, vals) in enumerate(cut.items()):
            c.setItem(r, 0, QTableWidgetItem(mat))
            for col, k in enumerate(keys, start=1):
                c.setItem(r, col, QTableWidgetItem(format_value(vals.get(k))))
        c.resizeColumnsToContents()
        self.cutting_title.setText(f"Feeds and speeds of <b>{name}</b> per material (empty = the op's own value)"
                                   if name else "")
        self._loading = False

    # --- editing ---------------------------------------------------------------------------------

    def _touch(self):
        self.dirty = True
        self.status.setText("unsaved changes")

    def _set_tool(self, name, key, value):
        if value is None:
            self.data[name].pop(key, None)
        else:
            self.data[name][key] = value
        self._touch()

    def _tool_edited(self, item):
        if self._loading:
            return
        r, c = item.row(), item.column()
        name = list(self.data)[r]
        if c == 0:
            new = item.text().strip()
            if not new or (new != name and new in self.data):
                self.status.setText(f"tool name {new!r} is empty or taken")
                self._fill_tools(r)
                return
            self.data = {(new if k == name else k): v for k, v in self.data.items()}
            self._touch()
            return
        f = self.fields[c - 1]
        if f.type in (bool, "bool"):
            self._set_tool(name, f.name, item.checkState() == Qt.Checked)
            return
        v = parse_value(item.text())
        if v is not None and not isinstance(v, (int, float)):
            self.status.setText(f"{f.name} must be a number")
            self._fill_tools(r)
            return
        if f.name == "number" and v is not None:
            v = int(v)
        self._set_tool(name, f.name, v)

    def _cutting_edited(self, item):
        name = self.current()
        if self._loading or name is None:
            return
        cut = self.data[name].setdefault("cutting", {})
        r, c = item.row(), item.column()
        mat = list(cut)[r]
        if c == 0:
            new = item.text().strip()
            if not new or (new != mat and new in cut):
                self.status.setText(f"material {new!r} is empty or already there")
                self._show_cutting(self.tools.currentRow())
                return
            self.data[name]["cutting"] = {(new if k == mat else k): v for k, v in cut.items()}
        else:
            v = parse_value(item.text())
            key = self.cut_keys[c - 1]
            if v is None:
                cut[mat].pop(key, None)
            else:
                cut[mat][key] = v
        self._touch()

    def add_tool(self):
        if self.path is None:
            return
        name = unique_name("tool", self.data)
        nums = [t.get("number") or 0 for t in self.data.values()]
        self.data[name] = {"number": max(nums, default=0) + 1, "diameter": 6.0, "kind": "flat"}
        self._touch()
        self._fill_tools(len(self.data) - 1)

    def remove_tool(self):
        name = self.current()
        if name is None:
            return
        r = self.tools.currentRow()
        del self.data[name]
        self._touch()
        self._fill_tools(max(0, r - 1))

    def add_material(self):
        name = self.current()
        if name is None:
            return
        mat, ok = QInputDialog.getText(self, "Add material", f"Material for {name}:")
        cut = self.data[name].setdefault("cutting", {})
        if ok and mat.strip() and mat.strip() not in cut:
            cut[mat.strip()] = {}
            self._touch()
            self._show_cutting(self.tools.currentRow())

    def remove_material(self):
        name = self.current()
        r = self.cutting.currentRow()
        if name is None or r < 0:
            return
        cut = self.data[name]["cutting"]
        del cut[list(cut)[r]]
        if not cut:
            del self.data[name]["cutting"]
        self._touch()
        self._show_cutting(self.tools.currentRow())

    def save(self) -> bool:
        if self.path is None:
            return False
        try:
            save_library(self.path, self.data)
        except (TypeError, ValueError) as e:
            self.status.setText(f"not saved: {e}")
            return False
        self.dirty = False
        self.status.setText(f"saved {self.path}")
        self.saved.emit()
        return True
