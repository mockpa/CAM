"""Post-processor base: turns Toolpath IR into G-code text."""
from __future__ import annotations

from ..toolpath import Toolpath


class PostProcessor:
    name = "generic"
    decimals = 3
    supports_a_axis = False

    def header(self) -> list[str]:
        return ["G21", "G90", "G17", "G94"]

    def footer(self) -> list[str]:
        return ["M5", "G0 X0 Y0", "M30"]

    def tool_change(self, tp: Toolpath) -> list[str]:
        return [f"({tp.name}: {tp.tool.name})", f"M3 S{int(tp.spindle_rpm)}", "G4 P2"]

    def fmt(self, v: float) -> str:
        s = f"{v:.{self.decimals}f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    def moves(self, tp: Toolpath) -> list[str]:
        out, last_feed, pos = [], None, {}
        for m in tp.moves:
            words = []
            for axis in ("x", "y", "z", "a"):
                v = getattr(m, axis)
                if v is None:
                    continue
                if axis == "a" and not self.supports_a_axis:
                    raise ValueError(f"post '{self.name}' has no rotary axis")
                if pos.get(axis) is not None and abs(pos[axis] - v) < 10 ** -self.decimals:
                    continue
                pos[axis] = v
                words.append(f"{axis.upper()}{self.fmt(v)}")
            if not words:
                continue
            if m.kind == "rapid":
                out.append("G0 " + " ".join(words))
            else:
                line = "G1 " + " ".join(words)
                if m.feed is not None and m.feed != last_feed:
                    line += f" F{self.fmt(m.feed)}"
                    last_feed = m.feed
                out.append(line)
        return out

    def emit(self, paths: list[Toolpath]) -> str:
        lines = list(self.header())
        for tp in paths:
            lines += self.tool_change(tp)
            lines += self.moves(tp)
        lines += self.footer()
        return "\n".join(lines) + "\n"
