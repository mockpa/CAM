"""GRBL 1.1 post. GRBL has no tool changer; a change of tool pauses with M0."""
from __future__ import annotations

from ..toolpath import Toolpath
from .base import PostProcessor


class GrblPost(PostProcessor):
    name = "grbl"
    decimals = 3

    def __init__(self):
        self._last_tool = None

    def emit(self, paths):
        self._last_tool = None
        return super().emit(paths)

    def tool_change(self, tp: Toolpath) -> list[str]:
        out = [f"({tp.name})"]
        if self._last_tool is not None and tp.tool != self._last_tool:
            out += ["M5", "G0 Z10", f"(change tool to {tp.tool.name}, then resume)", "M0"]
        if tp.tool != self._last_tool:
            out += [f"M3 S{int(tp.spindle_rpm)}", "G4 P2"]
        self._last_tool = tp.tool
        return out
