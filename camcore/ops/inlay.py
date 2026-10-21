"""V-carve inlays: a pocket in the base board and a mirrored plug in the insert board,
cut with the same V-bit so their walls match.

alpha = half the V angle, P = pocket_depth, g = glue_gap, m = surface_gap, s = P - g.

part="pocket": the flat-bottom V-carve of the design to depth P (vcarve with
max_depth=P; the clear tool takes the flat floor). At depth d its outline is the
design eroded by d * tan(alpha).

part="plug": the design mirrored about its bounding box centre line (D'). The
V-bit carves the region around D' (vcarve with start_depth s, capped at s + m):
the tip at distance r from D' runs at depth s + r / tan(alpha), so above depth s
the plug is D' eroded by (s - z) * tan(alpha), the pocket's own shape mirrored.
Only the walls around D' are cut with the V-bit (out to r_m = m * tan(alpha)); the
flange (frame minus offset(D', r_m)) is cleared flat to s + m by the clear tool
first, right up to the frame edge, so no rim is left that could rest on the base
board.

Glued: the plug is flipped into the pocket; the walls meet with its face g above
the pocket floor and its flange m above the base surface. camcore/inlay_fit.py
measures this on verify's simulated stock.
"""
from __future__ import annotations

import inspect
import math

import numpy as np
from shapely.geometry.polygon import orient
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from ..shapes import mirror
from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset, polygons
from .vcarve import _vcarve, vcarve, vcarve_flat_bottom_area

PARTS = ("pocket", "plug")
SHARP_NOTCH = 90.0       # degrees; the plug plunges into the tip of notches narrower than this


def inlay(shape: BaseGeometry, tool: Tool, pocket_depth: float, part: str = "pocket",
          glue_gap: float = 0.5, surface_gap: float = 1.5, plug_margin: float | None = None,
          stock: dict | None = None, method: str = "medial", step: float = 0.1,
          feed: float = 800, plunge: float = 300, safe_z: float = 5.0, top_z: float = 0.0,
          climb: bool = True, entry: str = "plunge", ramp_angle: float = 3.0,
          tolerance: float = 0.01, name: str = "inlay") -> Toolpath:
    """The V-bit pass of one inlay part (the clear tool's pass comes from inlay_clear_area).
    method="medial" (the default; needs a centre-cutting bit) gets sharp corners
    right on both parts; "offset" cannot reach into corners sharper than the bit's
    step allows, and the fit check shows where that makes the plug stand proud."""
    s, m = inlay_checks(tool, part, pocket_depth, glue_gap, surface_gap)
    if part == "pocket":
        tp = vcarve(shape, tool, step=step, max_depth=pocket_depth, feed=feed, plunge=plunge, safe_z=safe_z,
                    top_z=top_z, climb=climb, entry=entry, ramp_angle=ramp_angle, method=method,
                    tolerance=tolerance, name=name)
        return tp
    design = mirror(shape, "y")
    r_m = m * _tan(tool)
    frame = plug_frame(design, stock, plug_margin)
    if not offset(design, r_m).within(frame):
        raise ValueError(f"the plug's walls ({r_m:.2f} mm around the design) do not fit inside the plug "
                         + ("frame: raise plug_margin" if plug_margin is not None else
                            "stock: make the plug stock bigger or move the design"))
    # V-carve a band around the design starting at depth s: its outer edge lies far
    # enough out that everything cut there is dropped (the flange is cleared flat).
    band = offset(design, 2 * r_m + 2 * step + 1.0).difference(design)
    tp = _vcarve(band, tool, step, s + m, feed, plunge, safe_z, top_z, climb, entry, ramp_angle, method,
                 tolerance, start_depth=s, name=name, keep=offset(design, r_m + 0.5))
    # Into a notch sharper than SHARP_NOTCH no carving path reaches the very tip (at a
    # tangent cusp the medial axis stops short), and the material left there would hold
    # the plug off its seat. Anywhere outside the design depth s is no deeper than the
    # plug's shape allows, so the tip goes down on the corner and runs out along the
    # notch at depth s until it is wide enough for the carving above.
    if entry == "plunge":
        for (x, y), path in sharp_notches(design, tolerance):
            tp.rapid(z=safe_z)
            tp.rapid(x=x, y=y)
            tp.rapid(z=top_z + 1.0)
            tp.feed(z=top_z - s, f=plunge)
            for px, py in path:
                tp.feed(x=px, y=py, f=feed)
            tp.rapid(z=safe_z)
    if plug_margin is not None:
        x0, y0, x1, y1 = frame.bounds
        tp.notes.append(f"inlay plug: outside the frame X{x0:g}..{x1:g} Y{y0:g}..{y1:g} the board stays full "
                        f"thickness; trim it inside the flange before gluing")
    return tp


def sharp_notches(geom: BaseGeometry, tolerance: float = 0.01, angle: float | None = None):
    """Corners of geom whose outside (the notch) is narrower than `angle` degrees, each
    with the points of a straight run out along the notch's bisector, outside geom,
    while the notch is still narrower than 4 * tolerance (as [((x, y), [(x, y), ...])])."""
    angle = SHARP_NOTCH if angle is None else angle
    step, longest = tolerance, 5.0
    out = []
    for p in polygons(geom):
        p = orient(p)                              # area on the left of every ring
        for ring in (p.exterior, *p.interiors):
            c = np.array(ring.coords)[:-1]
            u = c - np.roll(c, 1, axis=0)          # into each vertex
            v = np.roll(c, -1, axis=0) - c         # out of it
            cross = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
            dot = (u * v).sum(axis=1)
            turn = np.degrees(np.arctan2(-cross, dot))   # > 0: a right turn, the notch side
            for k in np.nonzero(180 - turn < angle)[0]:
                a = u[k] / np.hypot(*u[k])
                b = v[k] / np.hypot(*v[k])
                d = b - a                          # along the notch, away from the corner
                d /= np.hypot(*d)
                path, n = [], 1
                while n * step <= longest:
                    q = c[k] + d * n * step
                    pt = Point(*q)
                    if geom.contains(pt) or geom.boundary.distance(pt) > 2 * tolerance:
                        break
                    path.append((float(q[0]), float(q[1])))
                    n += 1
                out.append(((float(c[k][0]), float(c[k][1])), path[-1:]))
    return out


def inlay_checks(tool: Tool, part: str, pocket_depth: float, glue_gap: float, surface_gap: float):
    """Refuse settings that cannot make a fitting inlay. Returns (s, m): the plug's
    start depth and the flange depth below it."""
    if part not in PARTS:
        raise ValueError(f'inlay part must be "pocket" or "plug", not {part!r}')
    if tool.kind != "vbit":
        raise ValueError("inlay needs a V-bit")
    if pocket_depth <= 0:
        raise ValueError("pocket_depth must be > 0")
    if not 0 <= glue_gap < pocket_depth:
        raise ValueError(f"glue_gap must be at least 0 and less than pocket_depth ({pocket_depth:g} mm), "
                         f"not {glue_gap:g}")
    if surface_gap <= 0:
        raise ValueError("surface_gap must be > 0: the plug's flange has to stand off the base surface "
                         "so the clamp pressure goes into the walls")
    s, m = pocket_depth - glue_gap, surface_gap
    need = pocket_depth if part == "pocket" else s + m
    full = tool.depth_for_radius(tool.radius)
    if full < need - 1e-9:
        what = "pocket_depth" if part == "pocket" else "pocket_depth - glue_gap + surface_gap"
        raise ValueError(f"V-bit {tool.name} cuts only {full:.2f} mm deep at its full diameter, but the "
                         f"{part} needs {need:g} mm ({what}); use a bigger bit or a shallower inlay")
    return s, m


def plug_frame(design: BaseGeometry, stock: dict | None, plug_margin: float | None,
               grow: float = 0.0) -> BaseGeometry:
    """The plug's flange area: the design's bounding box grown by plug_margin, or
    without a margin the whole box stock (grown by `grow`, so a clearing tool runs
    off its edges)."""
    if plug_margin is not None:
        if plug_margin <= 0:
            raise ValueError("plug_margin must be > 0")
        x0, y0, x1, y1 = design.bounds
        return box(x0 - plug_margin, y0 - plug_margin, x1 + plug_margin, y1 + plug_margin)
    if not stock or stock.get("type") != "box":
        raise ValueError("an inlay plug needs a box stock (its flange covers the whole board) or a plug_margin")
    return box(stock["x"][0] - grow, stock["y"][0] - grow, stock["x"][1] + grow, stock["y"][1] + grow)


def inlay_clear_area(shape: BaseGeometry, tool: Tool, clear_tool: Tool, **params) -> tuple[BaseGeometry, float]:
    """What the clear tool takes before the V-bit pass, as (region, depth below top_z):
    the pocket's flat floor, or the plug's flange. `params` are the inlay op's own."""
    defaults = {k: p.default for k, p in inspect.signature(inlay).parameters.items()}
    p = {**defaults, **params}
    s, m = inlay_checks(tool, p["part"], p["pocket_depth"], p["glue_gap"], p["surface_gap"])
    if p["part"] == "pocket":
        return vcarve_flat_bottom_area(shape, tool, p["pocket_depth"]), p["pocket_depth"]
    design = mirror(shape, "y")
    frame = plug_frame(design, p["stock"], p["plug_margin"], grow=clear_tool.radius)
    return frame.difference(offset(design, m * _tan(tool))), s + m


def _tan(tool: Tool) -> float:
    return math.tan(math.radians(tool.v_angle) / 2)
