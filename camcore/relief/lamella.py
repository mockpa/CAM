"""Lamellas: a height field cut into strips that stand on edge side by side (relief plan phase 3).

Ported from Reliefwerk's `lam_model` / `lam_outline`. The artwork (W x H mm, the
HeightField) is cut into parallel strips `thickness` thick with `gap` between them,
centred across the artwork. Each strip becomes one part cut from a board of that
thickness: a flat bottom edge (glued to the back board) and a top edge that follows
the relief, base + relief x h, with h averaged across the strip's thickness.

Part coordinates: X along the strip from 0 to its length, Y up from the bottom edge
(Y0). Vertical strips ("v") run along the artwork's Y, front edge at X0; horizontal
strips ("h") run along its X, left edge at X0. Outlines carry no tool offset: the
profile op adds that. `cut_outline` is the part as a cutter of a given radius
leaves it (valleys narrower than the cutter stay filled), for nesting and the plans.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Polygon

from ..ops.common import offset, polygons
from .heightfield import HeightField

DIRECTIONS = ("v", "h")
SAMPLES_ACROSS = 5           # profile samples across a strip's thickness when averaging (Reliefwerk's)


@dataclass
class LamellaSettings:
    """Settings of a lamella design (the job-file keys under "relief")."""
    direction: str = "v"         # "v": strips run front to back, side by side across X; "h": rows
    thickness: float = 18.0      # strip = board thickness, mm
    gap: float = 0.0             # between strips, mm
    base: float = 15.0           # height of the lowest point (black), mm
    relief: float = 60.0         # extra height for white, mm
    step: float = 2.0            # profile sample spacing along a strip, mm
    average: bool = True         # average the height across the thickness (else its centre line)
    tolerance: float = 0.02      # outline simplification, mm


@dataclass
class Lamella:
    index: int                   # 0 = leftmost (v) / backmost (h)
    label: str                   # "V01", "H01", ...
    offset: float                # first face across the artwork: X for v, distance from the back for h
    s: np.ndarray                # sample positions along the strip, 0..length
    profile: np.ndarray          # top edge heights at s
    outline: Polygon = field(repr=False)

    @property
    def length(self) -> float:
        return float(self.s[-1])

    @property
    def height(self) -> float:
        return float(self.profile.max())

    @property
    def lowest(self) -> float:
        return float(self.profile.min())


def check(st: LamellaSettings):
    if st.direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, not {st.direction!r}")
    if st.thickness <= 0 or st.gap < 0:
        raise ValueError("need thickness > 0 and gap >= 0")
    if st.base <= 0 or st.relief < 0:
        raise ValueError("need base > 0 (every part needs a body under its lowest point) and relief >= 0")
    if st.step <= 0 or st.tolerance <= 0:
        raise ValueError("need step > 0 and tolerance > 0")


def layout(span: float, thickness: float, gap: float) -> tuple[int, float]:
    """(count, offset of the first strip): as many strips as fit across `span`, centred."""
    pitch = thickness + gap
    n = int((span + gap) // pitch + 1e-9)
    return n, (span - (n * pitch - gap)) / 2 if n else 0.0


def outline(s: np.ndarray, profile: np.ndarray, tol: float) -> Polygon:
    """The part: bottom edge on Y0 from 0 to the length, top edge = the profile simplified
    within `tol` (Douglas-Peucker, end points kept), counter-clockwise."""
    top = LineString(np.column_stack([s, profile])).simplify(tol, preserve_topology=False)
    pts = [(float(s[0]), 0.0), (float(s[-1]), 0.0)] + list(top.coords)[::-1]
    poly = Polygon(pts)
    if not poly.is_valid:
        raise ValueError("lamella outline is not a valid polygon")
    return poly


def lamellas(F: HeightField, st: LamellaSettings) -> list[Lamella]:
    """Strips of the artwork in order: left to right (v) or back to front (h)."""
    check(st)
    vert = st.direction == "v"
    span, length = (F.width, F.height) if vert else (F.height, F.width)
    n, off = layout(span, st.thickness, st.gap)
    k = max(2, int(math.ceil(length / st.step - 1e-9)) + 1)
    s = np.linspace(0.0, length, k)
    m = SAMPLES_ACROSS if st.average else 1
    across = (np.arange(m) + 0.5) / m * st.thickness
    out = []
    for i in range(n):
        a = off + i * (st.thickness + st.gap)
        c = (a + across)[:, None]
        if vert:
            x, y = np.broadcast_arrays(c, s[None, :])                 # front (Y0) to back
        else:
            x, y = np.broadcast_arrays(s[None, :], F.height - c)       # left to right, from the back
        prof = st.base + st.relief * F.sample(x, y).mean(axis=0)
        label = ("V" if vert else "H") + f"{i + 1:02d}"
        out.append(Lamella(i, label, float(a), s, prof, outline(s, prof, st.tolerance)))
    return out


def cut_outline(part: Polygon, radius: float) -> Polygon:
    """The part as a cutter of `radius` leaves it from outside: the outline closed by the
    cutter disc (grown by the radius, then shrunk back), so valleys narrower than the
    cutter stay filled. The profile op cuts exactly around this."""
    if radius <= 0:
        return part
    # a profile has no overhangs, so the closing stays one polygon without holes; the union
    # keeps it from dipping inside the part by the offset's chord tolerance at convex corners
    (p,) = polygons(offset(offset(part, radius), -radius).union(part))
    return p
