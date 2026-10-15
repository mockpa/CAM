"""Machine body: what moves with the spindle and the gantry, and what is fixed
on the table, as axis-aligned boxes and cylinders along Z or X.

Everything is in tool-tip machine coordinates: work coordinates plus the
work offset, the frame camcore-verify replays in. The spindle nose is the
tool tip plus the tool's gauge length (`"gauge_length"` in the tool library: spindle
nose to tip); the gantry moves with Y only.

A machine JSON describes it under "body":

    "body": {
      "default_tool_length": 40,
      "spindle": [ parts relative to the spindle nose on the tool axis ],
      "gantry":  [ parts with machine X and Z, and Y relative to the tool axis ],
      "fixed":   [ parts in machine coordinates; "tool_may_enter": true for the
                   spoilboard, which through-cuts touch ],
      "rotary":  {"jaw_face_x": .., "axis_y": .., "axis_z": ..,
                  "parts": [ relative to the jaw face on the rotary axis ]}
    }

A part is a box {"name", "x": [lo, hi], "y": [lo, hi], "z": [lo, hi]}, a
vertical cylinder {"name", "axis": "z", "x": cx, "y": cy, "r", "z": [lo, hi]}
or one along X {"name", "axis": "x", "y": cy, "z": cz, "r", "x": [lo, hi]}.
The rotary unit is only there for jobs with rotary stock (it comes off the
table for flat jobs); with it, the work offset of a rotary job follows from
where the unit sits and how much of the stock is held in the jaws
(`"chuck_hold"` on the stock): Y0 Z0 on the axis, the jaw face at the
stock's X start plus the hold.

This module is plain geometry: camcore-verify uses it and must stay
independent of ops and posts, so it imports neither.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

TOL = 0.01          # mm a part must reach into another before it counts as a collision


@dataclass(frozen=True)
class Part:
    """A box, or the cylinder inscribed in the box along Z ("cyl_z") or X ("cyl_x")."""
    name: str
    kind: str
    lo: tuple
    hi: tuple
    tool_may_enter: bool = False

    @property
    def r(self) -> float:
        i = 0 if self.kind == "cyl_z" else 1
        return (self.hi[i] - self.lo[i]) / 2

    def moved(self, d) -> "Part":
        return replace(self, lo=tuple(a + b for a, b in zip(self.lo, d)),
                       hi=tuple(a + b for a, b in zip(self.hi, d)))

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "lo": list(self.lo), "hi": list(self.hi)}


def part(spec: dict, shift=(0.0, 0.0, 0.0)) -> Part:
    name, axis = spec["name"], spec.get("axis")
    if axis is None:
        lo = tuple(spec[k][0] for k in "xyz")
        hi = tuple(spec[k][1] for k in "xyz")
        kind = "box"
    elif axis in ("z", "x"):
        r = spec["r"]
        along = "xyz".index(axis)
        lo, hi = [], []
        for i, k in enumerate("xyz"):
            if i == along:
                lo.append(spec[k][0]); hi.append(spec[k][1])
            else:
                lo.append(spec[k] - r); hi.append(spec[k] + r)
        kind = f"cyl_{axis}"
    else:
        raise ValueError(f"body part {name}: axis must be \"z\" or \"x\", not {axis!r}")
    if any(a >= b for a, b in zip(lo, hi)):
        raise ValueError(f"body part {name} is empty: every range needs lo < hi and r > 0")
    return Part(name, kind, tuple(lo), tuple(hi), bool(spec.get("tool_may_enter"))).moved(shift)


def _rect_dist(px, py, x0, x1, y0, y1):
    """Distance from points (px, py) to the rectangle [x0, x1] x [y0, y1] (0 inside)."""
    return np.hypot(np.maximum(np.maximum(x0 - px, px - x1), 0), np.maximum(np.maximum(y0 - py, py - y1), 0))


def overlaps(a: Part, offsets, b: Part):
    """For each row of `offsets` (N x 3): does `a` moved by it reach more than TOL into `b`?"""
    d = np.atleast_2d(np.asarray(offsets, dtype=float))
    alo, ahi = np.asarray(a.lo) + d, np.asarray(a.hi) + d
    blo, bhi = np.asarray(b.lo), np.asarray(b.hi)
    # the bounding boxes must overlap; for boxes that is all there is
    hit = np.all((alo < bhi - TOL) & (blo < ahi - TOL), axis=1)
    if a.kind == "box" and b.kind == "box" or not hit.any():
        return hit
    if a.kind == "box":                       # one cylinder (or two): put a cylinder first
        a, b = b, a
        alo, ahi, blo, bhi = blo, bhi, alo, ahi
    ac = (alo + ahi) / 2
    bc = (np.asarray(blo) + np.asarray(bhi)) / 2
    if a.kind == "cyl_z" and b.kind in ("box", "cyl_z"):
        if b.kind == "box":
            dist = _rect_dist(ac[..., 0], ac[..., 1], blo[..., 0], bhi[..., 0], blo[..., 1], bhi[..., 1])
            return hit & (dist < a.r - TOL)
        return hit & (np.hypot(ac[..., 0] - bc[..., 0], ac[..., 1] - bc[..., 1]) < a.r + b.r - TOL)
    if a.kind == "cyl_x" and b.kind in ("box", "cyl_x"):
        if b.kind == "box":
            dist = _rect_dist(ac[..., 1], ac[..., 2], blo[..., 1], bhi[..., 1], blo[..., 2], bhi[..., 2])
            return hit & (dist < a.r - TOL)
        return hit & (np.hypot(ac[..., 1] - bc[..., 1], ac[..., 2] - bc[..., 2]) < a.r + b.r - TOL)
    # one vertical cylinder and one along X: at every X the vertical one's section is a
    # rectangle, widest at the X closest to its axis; that rectangle against the disc
    z, x = (a, b) if a.kind == "cyl_z" else (b, a)
    zlo, zhi, xlo, xhi = (alo, ahi, blo, bhi) if a.kind == "cyl_z" else (blo, bhi, alo, ahi)
    zc, xc = (zlo + zhi) / 2, (xlo + xhi) / 2
    dx = np.maximum(np.maximum(xlo[..., 0] - zc[..., 0], zc[..., 0] - xhi[..., 0]), 0)
    w = np.sqrt(np.clip(z.r ** 2 - dx ** 2, 0, None))
    dist = _rect_dist(xc[..., 1], xc[..., 2], zc[..., 1] - w, zc[..., 1] + w, zlo[..., 2], zhi[..., 2])
    return hit & (dist < x.r - TOL)


def hull(parts) -> tuple:
    """Bounding box (lo, hi) of parts."""
    return (tuple(min(p.lo[i] for p in parts) for i in range(3)),
            tuple(max(p.hi[i] for p in parts) for i in range(3)))


class Body:
    def __init__(self, spec: dict):
        self.spec = spec
        self.default_tool_length = float(spec.get("default_tool_length", 40.0))
        self.spindle = [part(p) for p in spec.get("spindle", [])]
        self.gantry = [part(p) for p in spec.get("gantry", [])]
        self.fixed = [part(p) for p in spec.get("fixed", [])]
        rot = spec.get("rotary")
        self.rotary = None
        if rot:
            shift = (rot["jaw_face_x"], rot["axis_y"], rot["axis_z"])
            self.rotary = [part(p, shift) for p in rot["parts"]]

    def obstacles(self, rotary_job: bool) -> list[Part]:
        return self.fixed + (self.rotary if rotary_job and self.rotary else [])

    def spindle_parts(self, length: float, tool_diameter: float) -> list[Part]:
        """Spindle parts and the tool itself, relative to the tool tip."""
        tool = Part("tool", "cyl_z", (-tool_diameter / 2, -tool_diameter / 2, 0.0),
                    (tool_diameter / 2, tool_diameter / 2, length))
        return [p.moved((0, 0, length)) for p in self.spindle] + [tool]

    def rotary_offset(self, stock: dict) -> dict:
        """Work offset of a rotary job: Y0 Z0 on the axis, X from the jaw face."""
        rot = self.spec["rotary"]
        hold = stock["chuck_hold"]
        return {"x": rot["jaw_face_x"] - (stock["x"][0] + hold), "y": rot["axis_y"], "z": rot["axis_z"]}


def jaw_reach(body: Body, length: float, diameter: float, z_tip: float = 0.0) -> float:
    """How far in X the tool axis must stay from the jaw face (towards the free
    end) so that nothing of the spindle or the tool, with the tip at z_tip
    above the rotary axis, touches the rotary unit. The unit's parts lie behind
    the jaw face, so the further away the clearer: bisected."""
    rot = [part(p) for p in body.spec["rotary"]["parts"]]
    spindle = [p.moved((0.0, 0.0, z_tip)) for p in body.spindle_parts(length, diameter)]

    def clear(d):
        return not any(overlaps(p, [(d, 0.0, 0.0)], q)[0] for p in spindle for q in rot)

    lo, hi = 0.0, diameter / 2
    while not clear(hi):
        lo, hi = hi, hi * 2 + 1
        if hi > 1e4:
            raise ValueError("the spindle cannot clear the rotary unit at any distance")
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if clear(mid) else (mid, hi)
    return hi


def scene(body: Body, offset: dict, stock: dict | None, tools: dict) -> dict:
    """What the sim's vismach model draws, in tool-tip machine coordinates.
    Frames: "fixed", "gantry" (moves with Y), "spindle" (relative to the spindle
    nose), "stock" (turns with A around the rotary axis when rotary)."""
    rotary = stock is not None and stock["type"] in ("cylinder", "bar")
    parts = [{**p.to_dict(), "frame": "fixed"} for p in body.obstacles(rotary)]
    parts += [{**p.to_dict(), "frame": "gantry"} for p in body.gantry]
    parts += [{**p.to_dict(), "frame": "spindle"} for p in body.spindle]
    o = (offset["x"], offset["y"], offset["z"])
    if stock is not None:
        if stock["type"] == "box":
            s = Part("stock", "box", tuple(stock[k][0] for k in "xyz"), tuple(stock[k][1] for k in "xyz"))
        elif stock["type"] == "cylinder":
            r = stock["diameter"] / 2
            s = Part("stock", "cyl_x", (stock["x"][0], -r, -r), (stock["x"][1], r, r))
        else:
            s = Part("stock", "box", (stock["x"][0], stock["y"][0], stock["z"][0]),
                     (stock["x"][1], stock["y"][1], stock["z"][1]))
        parts.append({**s.moved(o).to_dict(), "frame": "stock"})
    lengths = {int(n): {"length": float(t.get("gauge_length", body.default_tool_length)),
                        "diameter": float(t.get("diameter", 3.0))} for n, t in tools.items()}
    return {"parts": parts, "tools": lengths, "rotary_axis": [o[1], o[2]] if rotary else None,
            "default_tool_length": body.default_tool_length}
