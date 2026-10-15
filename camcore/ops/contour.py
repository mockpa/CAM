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

from shapely.geometry import LinearRing

from ..toolpath import Toolpath
from .common import quad_segs
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
        self.requested = n
        n = min(n, math.ceil(length / (2 * self.half)) - 1)     # as many as fit with a gap between them
        self.centres = [length * (i + 0.5) / n for i in range(n)] if n > 0 else []

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


class Lead:
    """Lead-in/lead-out circle in the waste, tangent to the contour at its start.

    Climb milling keeps the wall on the right, so the waste is on the left and the
    lead circle runs counter-clockwise (clockwise for conventional milling).
    Lead-in is the quarter arc onto the contour start, lead-out the quarter arc
    off it; between levels the tool descends on the rest of the circle.
    """

    def __init__(self, ring, radius: float, climb: bool):
        # tangent at the start: the mean of the segments before and after it, so on a
        # curve flattened into chords the lead is tangent to the curve, not to one chord
        (px, py), (qx, qy), (ox, oy) = ring[0], ring[1], ring[-2]
        a = math.atan2(qy - py, qx - px)
        b = math.atan2(py - oy, px - ox)
        mid = a + ((b - a + math.pi) % (2 * math.pi) - math.pi) / 2
        tx, ty = math.cos(mid), math.sin(mid)
        self.sign = 1 if climb else -1                       # +1: counter-clockwise
        wx, wy = -ty * self.sign, tx * self.sign             # unit normal towards the waste
        self.r = radius
        self.c = (px + radius * wx, py + radius * wy)
        phi_p = math.atan2(-wy, -wx)
        self.phi_s = phi_p - self.sign * math.pi / 2         # where the lead-in starts
        self.phi_e = phi_p + self.sign * math.pi / 2         # where the lead-out ends
        self.phi_p = phi_p

    def at(self, phi):
        return self.c[0] + self.r * math.cos(phi), self.c[1] + self.r * math.sin(phi)

    def circle(self) -> LinearRing:
        n = 4 * quad_segs(self.r)
        return LinearRing([self.at(2 * math.pi * k / n) for k in range(n)])

    def arc(self, tp: Toolpath, phi0: float, sweep: float, z0: float, z1: float, feed: float):
        """Move along the circle from phi0 by `sweep` (>= 0, in the lead direction), z0 -> z1."""
        n = max(1, math.ceil(sweep / (math.pi / 2 / quad_segs(self.r))))
        for k in range(1, n + 1):
            x, y = self.at(phi0 + self.sign * sweep * k / n)
            tp.feed(x=x, y=y, z=z0 + (z1 - z0) * k / n, f=feed)


def plan_lead(ring, radius: float, climb: bool, keep_clear, tool_radius: float) -> Lead | None:
    """A lead circle of at most `radius` whose path keeps the tool off the part, or None.

    keep_clear(ring) tells whether a tool-centre path stays in the waste."""
    r = radius
    while r >= tool_radius / 2:
        lead = Lead(ring, r, climb)
        if keep_clear(lead.circle()):
            return lead
        r /= 2
    return None


def cut_contour(tp: Toolpath, ring, levels, top_z: float, entry: str, ramp_angle: float,
                feed: float, plunge: float, tabs: Tabs | None = None, lead: Lead | None = None):
    """Cut a closed contour through all `levels` (descending z) without retracting.

    Without a lead the tool must already be at ring[0] at top_z: ramp entry
    descends along the contour, plunge entry drops straight down. With a lead it
    must be at the lead-in start at top_z: each level descends on the lead circle,
    arcs onto the contour, cuts the full loop and arcs off again.
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

    if lead is not None:
        phi = lead.phi_s
        for z in levels:
            back = (lead.phi_s - phi) * lead.sign % (2 * math.pi)     # rest of the circle to the lead-in start
            if entry == "plunge":
                lead.arc(tp, phi, back, zc, zc, feed)
                tp.feed(z=z, f=plunge)
            else:
                need = (zc - z) / math.tan(math.radians(ramp_angle)) / lead.r      # radians of descent
                turns = max(0, math.ceil((need - back) / (2 * math.pi) - 1e-9))
                lead.arc(tp, phi, back + 2 * math.pi * turns, zc, z, feed)
            lead.arc(tp, lead.phi_s, math.pi / 2, z, z, feed)                    # lead-in
            piece(s, s + rp.length, z, z)                                        # full loop with tabs
            s += rp.length
            lead.arc(tp, lead.phi_p, math.pi / 2, z, z, feed)                    # lead-out
            phi, zc = lead.phi_e, z
        return

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
