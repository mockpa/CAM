"""Paperwork for relief designs (relief plan phase 5): parts lists, cut lists and plans.

Lamellas: `NAME_parts.csv` (every part: size, where it goes in the artwork, where it is
on which board), `NAME_assembly.svg` (the artwork seen from the front, every strip
labelled) and per board `NAME_boardN.svg` / `.dxf` (outlines, labels, tabs).
Pins: `NAME_cut_list.csv` (per length: count and bar length with the saw cuts),
`NAME_positions.csv` (every pin) and `NAME_pin_plan.svg` / `.dxf`.

Plans are in mm with Y up, X0 Y0 at the bottom left, as on the machine. A dot marks
each lamella's start (its X0 end): the front end of a vertical strip, the left end of
a horizontal one. CSV: comma separated, decimal point, units in the headers.
"""
from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from .heightfield import HeightField
from .lamella import LamellaSettings
from .lamella_job import Design, SheetSettings
from .pins import PinGrid, PinSettings, cut_list, outline, positions

WOOD, INK, LINE, MARK = "#f3e3c8", "#000", "#999", "#b42318"


def num(v: float, nd: int = 2) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Svg:
    """A drawing in mm, Y up, origin at the bottom left (`margin` mm of paper around it)."""

    def __init__(self, w: float, h: float, margin: float = 10.0, top: float = 0.0, min_width: float = 0.0):
        """`top` mm of headings above the drawing; the paper is at least `min_width` wide."""
        self.w, self.h, self.m, self.top, self.paper_w = w, h, margin, top, max(w, min_width)
        self.items = []

    def _p(self, x, y):
        return self.m + x, self.m + self.top + self.h - y

    def poly(self, pts, fill="none", stroke=INK, width=0.3, closed=True):
        d = " ".join("{},{}".format(*map(num, self._p(x, y))) for x, y in pts)
        tag = "polygon" if closed else "polyline"
        self.items.append(f'<{tag} points="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{num(width)}"/>')

    def rect(self, x, y, w, h, **kw):
        self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], **kw)

    def dot(self, x, y, r, fill=MARK):
        cx, cy = self._p(x, y)
        self.items.append(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}" fill="{fill}"/>')

    def text(self, x, y, s, size=5.0, fill=INK, anchor="middle", turned=False):
        """Text with its baseline at (x, y); `turned` reads bottom to top."""
        tx, ty = self._p(x, y)
        at = f'transform="translate({num(tx)},{num(ty)}) rotate(-90)"' if turned else f'x="{num(tx)}" y="{num(ty)}"'
        self.items.append(f'<text {at} font-size="{num(size)}" font-family="sans-serif" '
                          f'text-anchor="{anchor}" fill="{fill}">{escape(s)}</text>')

    def heading(self, line: int, s: str, size=5.0):
        """Text above the drawing (line 0 is the topmost)."""
        self.items.append(f'<text x="{num(self.m)}" y="{num(self.m + (line + 1) * size * 1.4)}" '
                          f'font-size="{num(size)}" font-family="sans-serif">{escape(s)}</text>')

    def tostring(self) -> str:
        W, H = self.paper_w + 2 * self.m, self.h + 2 * self.m + self.top
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{num(W)}mm" height="{num(H)}mm" viewBox="0 0 {num(W)} {num(H)}">\n'
                f'<rect width="{num(W)}" height="{num(H)}" fill="#fff"/>\n' + "\n".join(self.items) + "\n</svg>\n")


def write_csv(path: Path, header: list[str], rows: list[list]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows([[num(v) if isinstance(v, float) else v for v in r] for r in rows])


def _dxf(path: Path, layers: dict, draw):
    import ezdxf

    doc = ezdxf.new("R2010", setup=False)
    doc.units = ezdxf.units.MM
    for name, colour in layers.items():
        doc.layers.add(name, color=colour)
    draw(doc.modelspace())
    doc.saveas(path)


# --------------------------------------------------------------------------
# Lamellas
# --------------------------------------------------------------------------

def label_size(part_height: float) -> float:
    return max(4.0, min(12.0, part_height * 0.25))


def write_lamella_paperwork(F: HeightField, ls: LamellaSettings, st: SheetSettings, d: Design,
                            out_dir, name: str) -> list[Path]:
    out = Path(out_dir)
    files = []
    where = {ob.label: (sh.index + 1, ob) for sh in d.sheets for ob in sh.parts}
    across = "from the left" if ls.direction == "v" else "from the back"
    rows = []
    for p in d.parts:
        board, ob = where[p.label]
        x0, y0 = ob.points.min(axis=0)
        rows.append([p.label, p.length, p.lowest, p.height, p.offset, board, float(x0), float(y0),
                     "yes" if ob.turned else "no", "yes" if ob.flipped else "no"])
    path = out / f"{name}_parts.csv"
    write_csv(path, ["part", "length mm", "lowest mm", "highest mm", f"position mm ({across})", "board",
                     "board x mm", "board y mm", "turned 90", "turned 180"], rows)
    files.append(path)

    # assembly: the artwork from the front, bottom edges on the back board
    W, H, t = F.width, F.height, ls.thickness
    svg = Svg(W, H, top=24, min_width=170)
    svg.heading(0, f"Assembly, seen from the front: {len(d.parts)} lamellas {num(t)} mm thick, "
                   f"{num(W)} x {num(H)} mm")
    svg.heading(1, "Straight edges on the back board; the dot is each lamella's start")
    svg.rect(0, 0, W, H, stroke=LINE, width=0.4)
    fs = max(2.5, min(8.0, t * 0.55))
    for p in d.parts:
        if ls.direction == "v":
            svg.rect(p.offset, 0, t, H, fill=WOOD, width=0.3)
            svg.text(p.offset + t / 2 + fs / 3, H / 2, p.label, size=fs, turned=True)
            svg.dot(p.offset + t / 2, 2.0, 1.0)
        else:
            y = H - p.offset - t
            svg.rect(0, y, W, t, fill=WOOD, width=0.3)
            svg.text(W / 2, y + t / 2 - fs / 3, p.label, size=fs)
            svg.dot(2.0, y + t / 2, 1.0)
    path = out / f"{name}_assembly.svg"
    path.write_text(svg.tostring())
    files.append(path)

    for sh in d.sheets:
        n = sh.index + 1
        svg = Svg(st.width, st.height, top=12)
        svg.heading(0, f"Board {n} of {len(d.sheets)}: {num(st.width)} x {num(st.height)} x {num(t)} mm, "
                       f"{len(sh.parts)} parts; tabs in red, the dot marks each part's start")
        svg.rect(0, 0, st.width, st.height, stroke=LINE, width=0.5)
        for ob in sh.parts:
            svg.poly(ob.points, fill=WOOD, width=0.4)
            a, b = ob.points[0], ob.points[1]                 # the bottom edge, from the start
            u = (b - a) / np.hypot(*(b - a))
            inward = np.array([-u[1], u[0]])                  # counter-clockwise outline: inside on the left
            svg.dot(*(a + 3 * u + 3 * inward), 1.2)
            lowest = next(p for p in d.parts if p.label == ob.label).lowest
            size = label_size(lowest)
            c = (a + b) / 2 + inward * lowest * 0.45
            svg.text(float(c[0]), float(c[1]) - size / 3, ob.label, size=size, fill=MARK)
            for tx, ty in ob.tabs:
                svg.dot(float(tx), float(ty), 1.5)
        path = out / f"{name}_board{n}.svg"
        path.write_text(svg.tostring())
        files.append(path)

        def draw(msp, sh=sh):
            msp.add_lwpolyline([(0, 0), (st.width, 0), (st.width, st.height), (0, st.height)], close=True,
                               dxfattribs={"layer": "BOARD"})
            for ob in sh.parts:
                msp.add_lwpolyline([tuple(map(float, q)) for q in ob.points], close=True, dxfattribs={"layer": "PARTS"})
                c = ob.points.mean(axis=0)
                msp.add_text(ob.label, height=5, dxfattribs={"layer": "LABELS"}).set_placement(tuple(map(float, c)))
                for tx, ty in ob.tabs:
                    msp.add_circle((float(tx), float(ty)), 1.5, dxfattribs={"layer": "TABS"})

        path = out / f"{name}_board{n}.dxf"
        _dxf(path, {"BOARD": 8, "PARTS": 7, "LABELS": 1, "TABS": 1}, draw)
        files.append(path)
    return files


# --------------------------------------------------------------------------
# Pins
# --------------------------------------------------------------------------

def write_pin_paperwork(F: HeightField, st: PinSettings, g: PinGrid, out_dir, name: str) -> list[Path]:
    out = Path(out_dir)
    files = []
    cl = cut_list(g, st)
    path = out / f"{name}_cut_list.csv"
    write_csv(path, ["level", "length mm", "count", f"bar mm (with {num(st.kerf)} mm saw cuts)"],
              [[r["level"], float(r["length"]), r["count"], float(r["bar"])] for r in cl]
              + [["total", "", sum(r["count"] for r in cl), float(sum(r["bar"] for r in cl))]])
    files.append(path)
    path = out / f"{name}_positions.csv"
    write_csv(path, ["pin", "row", "column", "level", "length mm", "centre x mm", "centre y mm"],
              [[r["pin"], r["row"], r["column"], r["level"], r["length"], r["x"], r["y"]] for r in positions(g)])
    files.append(path)

    W, H = F.width, F.height
    used = [r for r in cl]
    svg = Svg(W, H, top=14 + 7 * len(used))
    svg.heading(0, f"Pin plan, seen from the front: {len(g.cx)} x {len(g.cy)} {st.shape} pins {num(st.size)} mm; "
                   "the number is the level", size=5)
    for k, r in enumerate(used):
        svg.heading(k + 1, f"level {r['level']}: {num(r['length'])} mm x {r['count']}", size=4.5)
    svg.rect(0, 0, W, H, stroke=LINE, width=0.4)
    nl = max(1, len(g.levels) - 1)
    fs = max(2.0, min(10.0, st.size * 0.35))
    for j, y in enumerate(g.cy):
        for i, x in enumerate(g.cx):
            k = int(g.level[j, i])
            shade = int(235 - 150 * k / nl)
            svg.poly(outline(st, float(x), float(y)), fill=f"rgb({shade},{int(shade * 0.92)},{int(shade * 0.8)})",
                     width=0.25)
            svg.text(float(x), float(y) - fs / 3, str(k + 1), size=fs)
    path = out / f"{name}_pin_plan.svg"
    path.write_text(svg.tostring())
    files.append(path)

    def draw(msp):
        msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True, dxfattribs={"layer": "FRAME"})
        for j, y in enumerate(g.cy):
            for i, x in enumerate(g.cx):
                if st.shape == "round":
                    msp.add_circle((float(x), float(y)), st.size / 2, dxfattribs={"layer": "PINS"})
                else:
                    msp.add_lwpolyline(outline(st, float(x), float(y)), close=True, dxfattribs={"layer": "PINS"})
                msp.add_text(str(int(g.level[j, i]) + 1), height=fs,
                             dxfattribs={"layer": "LEVELS"}).set_placement((float(x), float(y)))

    path = out / f"{name}_pin_plan.dxf"
    _dxf(path, {"FRAME": 8, "PINS": 7, "LEVELS": 1}, draw)
    files.append(path)
    return files
