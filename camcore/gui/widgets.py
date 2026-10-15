"""Small editing widgets shared by the panels."""
from __future__ import annotations

import json

from PySide6.QtWidgets import QComboBox, QLineEdit

from ..project import format_value, parse_value

BAD = "background: #f4c7c3;"


class JsonEdit(QLineEdit):
    """One line of JSON for a job key. Empty = the key is absent. on_value(v) is
    called with each valid edit (None when emptied); invalid text turns red."""

    def __init__(self, value, on_value, placeholder="", allow_text=False, parent=None):
        super().__init__(format_value(value) if allow_text else ("" if value is None else json.dumps(value)), parent)
        self.on_value, self.allow_text = on_value, allow_text
        self.setPlaceholderText(placeholder)
        self.textEdited.connect(self._edited)

    def _edited(self, text):
        if self.allow_text:
            v = parse_value(text)
        else:
            try:
                v = json.loads(text) if text.strip() else None
            except json.JSONDecodeError as e:
                self.setStyleSheet(BAD)
                self.setToolTip(f"not valid JSON: {e}")
                return
        self.setStyleSheet("")
        self.setToolTip("")
        self.on_value(v)


def combo(options, current, on_value, empty_label=None, editable=False):
    """A combo box over options; with empty_label, a first entry that means None.
    A current value that is not among the options is kept, so it is not lost."""
    box = QComboBox()
    box.setEditable(editable)
    items = ([empty_label] if empty_label is not None else []) + list(options)
    if current is not None and current not in items:
        items.append(current)
    box.addItems([str(i) for i in items])
    box.setCurrentIndex(items.index(current) if current is not None else 0)

    def changed(text):
        on_value(None if (empty_label is not None and text == empty_label) or text == "" else text)
    (box.currentTextChanged if editable else box.textActivated).connect(changed)
    return box
