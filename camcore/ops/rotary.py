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
    out = Toolpath(tp.name, tp.tool, tp.spindle_rpm, notes=list(tp.notes))
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


def index(tp: Toolpath, a: float, face_z: float, clear_z: float) -> Toolpath:
    """Indexed 3+1: place a toolpath built in face coordinates (Z0 = top of the
    face, X and Y as seen from above) on the face that A = a turns up.

    The path retracts to clear_z (above everything the part can swing through),
    turns A with nothing else moving, runs the op with Z raised by face_z, and
    retracts to clear_z again. Y0 and Z0 are on the rotary axis.
    """
    out = Toolpath(f"{tp.name} A{a:g}", tp.tool, tp.spindle_rpm, notes=list(tp.notes))
    out.rapid(z=clear_z)
    out.rapid(a=a)
    for m in tp.moves:
        if m.a is not None:
            raise ValueError(f"{tp.name}: an indexed op must not move A itself")
        z = None if m.z is None else m.z + face_z
        if z is not None and z > clear_z + 1e-9:
            raise ValueError(f"{tp.name}: Z {z:g} is above the index clearance {clear_z:g}")
        out.moves.append(Move(m.kind, m.x, m.y, z, None, m.feed))
    out.rapid(z=clear_z)
    return out


def index_angles(spec) -> list[float]:
    """A angles of an indexed op: a number, a list, {"angles": [...]} or
    {"sides": N, "start": deg} (N equal steps around the part)."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return [float(spec)]
    if isinstance(spec, dict) and "angles" in spec:
        spec = spec["angles"]
    if isinstance(spec, list):
        if not spec or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in spec):
            raise ValueError(f"index angles must be a non-empty list of numbers, not {spec!r}")
        return [float(v) for v in spec]
    if isinstance(spec, dict) and "sides" in spec:
        n = spec["sides"]
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"index sides must be a whole number >= 1, not {n!r}")
        start = float(spec.get("start", 0.0))
        return [start + 360.0 * k / n for k in range(n)]
    raise ValueError(f"index must be an angle, a list of angles, {{\"angles\": [...]}} or "
                     f"{{\"sides\": N, \"start\": deg}}, not {spec!r}")


def stock_top(stock: dict, a: float) -> float:
    """Highest point of rotary stock (Z, axis at Z0) when A = a: the face indexed ops start from."""
    if stock.get("type") == "cylinder":
        return stock["diameter"] / 2.0
    ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
    return max(-y * sa + z * ca for y in stock["y"] for z in stock["z"])


def stock_reach(stock: dict) -> float:
    """Largest radius of rotary stock: nothing on the part swings higher."""
    if stock.get("type") == "cylinder":
        return stock["diameter"] / 2.0
    return max(math.hypot(y, z) for y in stock["y"] for z in stock["z"])
