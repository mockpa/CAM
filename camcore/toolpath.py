"""Toolpath intermediate representation (IR).

Operations emit a Toolpath; post-processors, simulation and estimators consume it.
Coordinates are absolute mm; `a` is the rotary axis in degrees (None for 3-axis).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass
class Move:
    kind: str                   # rapid | feed
    x: float | None = None
    y: float | None = None
    z: float | None = None
    a: float | None = None
    feed: float | None = None


@dataclass
class Toolpath:
    name: str
    tool: object
    spindle_rpm: float = 18000
    moves: list[Move] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # things the op adapted that the user should know

    def rapid(self, x=None, y=None, z=None, a=None):
        self.moves.append(Move("rapid", x, y, z, a))

    def feed(self, x=None, y=None, z=None, a=None, f=None):
        self.moves.append(Move("feed", x, y, z, a, f))

    def cut_polyline(self, pts: Sequence[tuple[float, float]], z: float, safe_z: float,
                     feed: float, plunge: float, closed: bool = True):
        """Rapid to start, plunge, follow pts at depth z, retract."""
        pts = list(pts)
        if not pts:
            return
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        self.rapid(z=safe_z)
        self.rapid(x=pts[0][0], y=pts[0][1])
        self.feed(z=z, f=plunge)
        for x, y in pts[1:]:
            self.feed(x=x, y=y, f=feed)
        self.rapid(z=safe_z)

    def cut_polyline_3d(self, pts: Sequence[tuple[float, float, float]], safe_z: float,
                        feed: float, plunge: float):
        """Follow a path whose depth varies per point (used by V-carve)."""
        if not pts:
            return
        self.rapid(z=safe_z)
        self.rapid(x=pts[0][0], y=pts[0][1])
        self.feed(z=pts[0][2], f=plunge)
        for x, y, z in pts[1:]:
            self.feed(x=x, y=y, z=z, f=feed)
        self.rapid(z=safe_z)

    def uses_a_axis(self) -> bool:
        return any(m.a is not None for m in self.moves)

    def length(self) -> tuple[float, float]:
        """(feed_length, rapid_length) in mm."""
        pos = [0.0, 0.0, 0.0]
        feed = rapid = 0.0
        for m in self.moves:
            new = [m.x if m.x is not None else pos[0],
                   m.y if m.y is not None else pos[1],
                   m.z if m.z is not None else pos[2]]
            d = math.dist(pos, new)
            if m.kind == "feed":
                feed += d
            else:
                rapid += d
            pos = new
        return feed, rapid

    def bounds(self) -> tuple[float, float, float, float, float, float]:
        xs = [m.x for m in self.moves if m.x is not None]
        ys = [m.y for m in self.moves if m.y is not None]
        zs = [m.z for m in self.moves if m.z is not None]
        return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def concat(paths: Iterable[Toolpath]) -> list[Toolpath]:
    return [p for p in paths if p.moves]
