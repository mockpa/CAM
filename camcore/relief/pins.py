"""Pins: a height field as a grid of square, round or hex pins cut to stepped lengths (relief plan phase 5).

Ported from Reliefwerk's `pin_model`. No machining: the output is paperwork (a cut list
per length, every pin's position, a pin plan), written by `relief/sheets.py`.

Pins sit in a grid of `size` (edge, diameter or across flats) plus `gap`, centred on the
artwork. A pin's height is base + relief x h, h averaged over 3 x 3 points a third of
the pin size apart; with `levels` >= 2 the heights snap to that many evenly spaced
lengths (fewer different lengths to saw), with 0 they are rounded to 0.5 mm.
Rows are named A, B, ... from the back, columns 1, 2, ... from the left.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .heightfield import HeightField

SHAPES = ("square", "round", "hex")
STEPLESS = 0.5          # mm: heights are rounded to this without levels


@dataclass
class PinSettings:
    """Settings of a pin design (the job-file keys under "relief")."""
    shape: str = "square"
    size: float = 30.0        # edge (square), diameter (round) or across flats (hex), mm
    gap: float = 2.0          # between pins, mm
    base: float = 10.0        # height for black, mm
    relief: float = 50.0      # extra height for white, mm
    levels: int = 8           # different lengths; 0 = any length (rounded to 0.5 mm)
    kerf: float = 3.0         # saw cut per pin, for the material total, mm


@dataclass
class PinGrid:
    cx: np.ndarray            # (nx,) pin centres X, mm from the left
    cy: np.ndarray            # (ny,) pin centres Y, mm from the bottom; row 0 is the back row
    heights: np.ndarray       # (ny, nx) mm
    level: np.ndarray         # (ny, nx) index into levels
    levels: list              # the lengths, shortest first

    @property
    def count(self) -> int:
        return int(self.heights.size)


def check(st: PinSettings):
    if st.shape not in SHAPES:
        raise ValueError(f"shape must be one of {SHAPES}, not {st.shape!r}")
    if st.size <= 0 or st.gap < 0 or st.base <= 0 or st.relief < 0 or st.kerf < 0:
        raise ValueError("need size > 0, gap >= 0, base > 0, relief >= 0 and kerf >= 0")
    if st.levels < 0 or st.levels == 1:
        raise ValueError("levels must be 0 (any length) or at least 2")


def row_name(j: int) -> str:
    """A, B, ..., Z, AA, AB, ... for row 0, 1, ..."""
    s, j = "", j + 1
    while j:
        j, r = divmod(j - 1, 26)
        s = chr(65 + r) + s
    return s


def pin_grid(F: HeightField, st: PinSettings) -> PinGrid:
    check(st)
    p = st.size + st.gap
    nx = int((F.width + st.gap) // p + 1e-9)
    ny = int((F.height + st.gap) // p + 1e-9)
    if nx < 1 or ny < 1:
        raise ValueError(f"no {st.size:g} mm pin fits the {F.width:g} x {F.height:g} mm artwork")
    offx = (F.width - (nx * p - st.gap)) / 2
    offy = (F.height - (ny * p - st.gap)) / 2
    cx = offx + st.size / 2 + np.arange(nx) * p
    cy = F.height - (offy + st.size / 2 + np.arange(ny) * p)          # row 0 at the back
    o = np.array([-st.size / 3, 0.0, st.size / 3])
    ox, oy = (g.ravel() for g in np.meshgrid(o, o))
    X = cx[None, :, None] + ox[None, None, :]
    Y = cy[:, None, None] + oy[None, None, :]
    X, Y = np.broadcast_arrays(X, Y)
    h = F.sample(X, Y).mean(axis=-1)
    if st.levels >= 2:
        level = np.rint(h * (st.levels - 1)).astype(int)
        levels = [st.base + st.relief * k / (st.levels - 1) for k in range(st.levels)]
        heights = np.array(levels)[level]
    else:
        heights = np.round((st.base + st.relief * h) / STEPLESS) * STEPLESS
        levels = sorted(set(np.round(heights.ravel(), 6).tolist()))
        level = np.searchsorted(np.array(levels), np.round(heights, 6))
    return PinGrid(cx, cy, heights, level, levels)


def outline(st: PinSettings, x: float, y: float) -> list[tuple[float, float]]:
    """A pin's footprint around (x, y), counter-clockwise (round: 48 sides)."""
    a = st.size
    if st.shape == "round":
        k = 48
        return [(x + a / 2 * math.cos(2 * math.pi * i / k), y + a / 2 * math.sin(2 * math.pi * i / k))
                for i in range(k)]
    if st.shape == "hex":                        # flats at top and bottom, `size` across them
        r = a / 2 / math.cos(math.pi / 6)
        return [(x + r * math.cos(math.pi / 3 * i), y + r * math.sin(math.pi / 3 * i)) for i in range(6)]
    h = a / 2
    return [(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)]


def cut_list(g: PinGrid, st: PinSettings) -> list[dict]:
    """Per length actually used: how many pins, and the bar length they take with the saw cuts."""
    rows = []
    for k, length in enumerate(g.levels):
        n = int((g.level == k).sum())
        if n:
            rows.append({"level": k + 1, "length": length, "count": n, "bar": n * (length + st.kerf)})
    return rows


def positions(g: PinGrid) -> list[dict]:
    rows = []
    for j in range(len(g.cy)):
        for i in range(len(g.cx)):
            rows.append({"pin": f"{row_name(j)}{i + 1}", "row": row_name(j), "column": i + 1,
                         "level": int(g.level[j, i]) + 1, "length": float(g.heights[j, i]),
                         "x": float(g.cx[i]), "y": float(g.cy[j])})
    return rows
