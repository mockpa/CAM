"""Packing rectangles onto sheets (relief plan phase 4), ported from Reliefwerk's MaxRects.

MaxRects with the best-short-side-fit rule: every free rectangle of a sheet is kept
(maximal, overlapping), a box goes where it leaves the least on its shorter side.
Boxes are packed tallest first; each goes on the first sheet already started that
takes it (in either orientation if turning is allowed), else on a new sheet.
"""
from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-9


class MaxRects:
    """Free space of one W x H sheet as maximal free rectangles (x, y, w, h)."""

    def __init__(self, w: float, h: float):
        self.w, self.h = w, h
        self.free = [(0.0, 0.0, float(w), float(h))]

    def find(self, w: float, h: float):
        """(score, x, y) of the best spot for a w x h box, or None. Lower score is better:
        least left over on the shorter side, then on the longer side, then lowest, leftmost."""
        best = None
        for fx, fy, fw, fh in self.free:
            if w <= fw + EPS and h <= fh + EPS:
                sc = (min(fw - w, fh - h), max(fw - w, fh - h), fy, fx)
                if best is None or sc < best[0]:
                    best = (sc, fx, fy)
        return best

    def place(self, x: float, y: float, w: float, h: float):
        """Take the box out of every free rectangle it overlaps; keep only maximal ones."""
        out = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                out.append((fx, fy, fw, fh))
                continue
            if x > fx:
                out.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                out.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                out.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                out.append((fx, y + h, fw, fy + fh - y - h))
        out = [r for r in out if r[2] > 1e-6 and r[3] > 1e-6]
        keep = []
        for i, a in enumerate(out):
            inside = any(i != j and a[0] >= b[0] - EPS and a[1] >= b[1] - EPS and a[0] + a[2] <= b[0] + b[2] + EPS
                         and a[1] + a[3] <= b[1] + b[3] + EPS and (a != b or i > j) for j, b in enumerate(out))
            if not inside:
                keep.append(a)
        self.free = keep


@dataclass
class Placed:
    item: object        # what was packed
    sheet: int
    x: float            # lower-left corner of the box on the sheet
    y: float
    w: float            # size as placed (after turning)
    h: float
    turned: bool        # turned 90 deg (counter-clockwise) from the item's own w x h


def pack(items, sheet_w: float, sheet_h: float, allow_turn: bool = True):
    """Pack items (anything with `.w`, `.h`) onto as many sheet_w x sheet_h sheets as needed.

    Returns (sheets, too_big): sheets is a list of lists of Placed (in packing order),
    too_big the items that fit no empty sheet."""
    bins, sheets, too_big = [], [], []
    for item in sorted(items, key=lambda it: -it.h):
        shapes = [(False, item.w, item.h)] + ([(True, item.h, item.w)] if allow_turn else [])
        best = None
        for k, mr in enumerate(bins):
            for turned, w, h in shapes:
                f = mr.find(w, h)
                if f and (best is None or f[0] < best[0]):
                    best = (f[0], f[1], f[2], turned, w, h, k)
            if best:
                break
        if best is None:
            mr = MaxRects(sheet_w, sheet_h)
            for turned, w, h in shapes:
                f = mr.find(w, h)
                if f:
                    bins.append(mr)
                    sheets.append([])
                    best = (f[0], f[1], f[2], turned, w, h, len(bins) - 1)
                    break
        if best is None:
            too_big.append(item)
            continue
        _, x, y, turned, w, h, k = best
        bins[k].place(x, y, w, h)
        sheets[k].append(Placed(item, k, x, y, w, h, turned))
    return sheets, too_big
