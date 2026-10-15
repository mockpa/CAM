"""Wrapped rotary (4th axis, A around X).

Design flat in X/Y, where Y runs around the circumference of the stock
(0 .. pi * diameter). `wrap` maps the toolpath onto the cylinder:

    A = Y / r  (degrees)     Z = r + z

so Z0 is the rotary axis centreline and z = 0 in the flat design is the stock
surface. A straight segment in (X, Y) becomes a straight segment in (X, A),
which is exactly the helix on the cylinder, so no extra subdivision is needed.
Every 2.5D and V-carve operation works wrapped without changes.
"""
from __future__ import annotations

import math

from ..toolpath import Move, Toolpath


def wrap(tp: Toolpath, diameter: float, safe_z: float | None = None) -> Toolpath:
    r = diameter / 2.0
    out = Toolpath(tp.name, tp.tool, tp.spindle_rpm)
    for m in tp.moves:
        a = None if m.y is None else math.degrees(m.y / r)
        z = None if m.z is None else r + m.z
        if m.kind == "rapid" and m.z is not None and m.z > 0 and safe_z is not None:
            z = r + safe_z
        out.moves.append(Move(m.kind, m.x, None, z, a, m.feed))
    return out


def circumference(diameter: float) -> float:
    """Height of the flat design that wraps exactly once around the stock."""
    return math.pi * diameter
