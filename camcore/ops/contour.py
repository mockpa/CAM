"""Profile paths along a closed contour, parametrised by distance s along it.

The cut is a chain of pieces with a linear base depth z(s) (ramps down, flat
loops) or vertical drops (plunge entry). Holding tabs raise that to

    z(s) = max(base(s), tab(s)),   tab(s) = tab_top - tan(ramp_angle) * distance to the nearest tab

so the tool rises over each tab and ramps back down after it (trapezoid tabs).
Both functions are piecewise linear, so the path is emitted exactly at their
kinks and crossings; nothing is sampled.
"""
from __future__ import annotations

import bisect
import math

from ..toolpath import Toolpath
from .entry import MAX_RAMP_ANGLE

EPS = 1e-9


class RingParam:
    """Closed ring (first point == last) with lookups by distance along it."""

    def __init__(self, ring):
        self.pts = list(ring)
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.length = self.cum[-1]
        if self.length <= EPS:
            raise ValueError("cannot cut along a zero-length contour")

    def xy(self, s: float):
        s %= self.length
        i = min(bisect.bisect_right(self.cum, s) - 1, len(self.pts) - 2)
        seg = self.cum[i + 1] - self.cum[i]
        t = (s - self.cum[i]) / seg if seg else 0.0
        (x0, y0), (x1, y1) = self.pts[i], self.pts[i + 1]
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t

    def vertices_between(self, sa: float, sb: float):
        """Distances of ring vertices strictly inside (sa, sb), over as many laps as needed."""
        out = []
        for lap in range(math.floor(sa / self.length), math.ceil(sb / self.length) + 1):
            for c in self.cum[:-1]:
                s = c + lap * self.length
                if sa + EPS < s < sb - EPS:
                    out.append(s)
        return out


class Tabs:
    """Tab profile on one contour: plateaus of `plateau` mm (tool path length) at `tab_top`."""

    def __init__(self, length: float, spec: dict, tool_diameter: float, bottom_z: float, top_z: float):
        unknown = set(spec) - {"count", "spacing", "width", "height", "ramp_angle"}
        if unknown:
            raise ValueError(f"unknown tab settings: {', '.join(sorted(unknown))}")
        if ("count" in spec) == ("spacing" in spec):
            raise ValueError("tabs need either \"count\" or \"spacing\"")
        width, height = float(spec["width"]), float(spec["height"])
        if width <= 0 or height <= 0:
            raise ValueError("tab width and height must be > 0")
        if height >= top_z - bottom_z:
            raise ValueError(f"tab height {height} must be less than the cut depth {top_z - bottom_z}")
        angle = float(spec.get("ramp_angle", MAX_RAMP_ANGLE))
        if not 0 < angle <= MAX_RAMP_ANGLE:
            raise ValueError(f"tab ramp_angle must be in (0, {MAX_RAMP_ANGLE:g}] degrees")
        n = int(spec["count"]) if "count" in spec else max(1, round(length / float(spec["spacing"])))
        if n < 1:
            raise ValueError("tab count must be at least 1")
        self.length, self.top, self.slope = length, bottom_z + height, math.tan(math.radians(angle))
        # On the tool path a tab is the material width plus the tool diameter long.
        self.half = (width + tool_diameter) / 2
        if n * 2 * self.half >= length:
            raise ValueError(f"{n} tabs of width {width} mm don't fit on a {length:.1f} mm contour")
        self.centres = [length * (i + 0.5) / n for i in range(n)]

    def z(self, s: float) -> float:
        best = -math.inf
        for c in self.centres:
            d = abs((s - c + self.length / 2) % self.length - self.length / 2)
            best = max(best, self.top - self.slope * max(0.0, d - self.half))
        return best

    def kinks_between(self, sa: float, sb: float):
        """Plateau edges and the midpoints between tabs, inside (sa, sb)."""
        marks = []
        cs = self.centres
        for i, c in enumerate(cs):
            nxt = cs[(i + 1) % len(cs)] + (self.length if i == len(cs) - 1 else 0.0)
            marks += [c - self.half, c + self.half, (c + nxt) / 2]
        out = []
        for lap in range(math.floor(sa / self.length) - 1, math.ceil(sb / self.length) + 1):
            for m in marks:
                s = m + lap * self.length
                if sa + EPS < s < sb - EPS:
                    out.append(s)
        return out


def cut_contour(tp: Toolpath, ring, levels, top_z: float, entry: str, ramp_angle: float,
                feed: float, plunge: float, tabs: Tabs | None = None):
    """Cut a closed contour through all `levels` (descending z) without retracting.

    The tool must already be at ring[0] at top_z. Ramp entry descends along the
    contour; plunge entry drops straight down at the current point.
    """
    if entry != "plunge" and not 0 < ramp_angle <= MAX_RAMP_ANGLE:
        raise ValueError(f"ramp_angle must be in (0, {MAX_RAMP_ANGLE:g}] degrees")
    rp = RingParam(ring)
    tab = tabs.z if tabs else (lambda s: -math.inf)
    s, zc = 0.0, top_z

    def piece(sa, sb, za, zb):
        base = (lambda u: za + (zb - za) * (u - sa) / (sb - sa)) if sb > sa else (lambda u: zb)
        near_tabs = tabs is not None and min(za, zb) < tabs.top - EPS     # only passes below the tab top
        marks = sorted({sa, sb, *rp.vertices_between(sa, sb), *(tabs.kinks_between(sa, sb) if near_tabs else [])})
        pts = [marks[0]]
        for u, v in zip(marks, marks[1:]):
            du, dv = base(u) - tab(u), base(v) - tab(v)
            if du * dv < 0:                           # base and tab profile cross inside (u, v)
                pts.append(u + (v - u) * du / (du - dv))
            pts.append(v)
        for u in pts[1:]:
            x, y = rp.xy(u)
            tp.feed(x=x, y=y, z=max(base(u), tab(u)), f=feed)

    for z in levels:
        if entry == "plunge":
            tp.feed(z=max(z, tab(s)), f=plunge)
        else:
            ramp = (zc - z) / math.tan(math.radians(ramp_angle))
            piece(s, s + ramp, zc, z)
            s += ramp
        piece(s, s + rp.length, z, z)
        s += rp.length
        zc = z
