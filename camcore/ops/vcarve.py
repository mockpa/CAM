"""V-carving.

MVP method (offset-based): inset the shape by increasing distances r; the tool
tip on the inset contour at depth r / tan(angle/2) touches the original
boundary exactly. Stacking these contours cuts the correct V-groove everywhere.
Phase 2 replaces this with a medial-axis method for sharper corners and fewer moves.
"""
from __future__ import annotations

import math

from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset, polygons, rings
from .entry import approach, check_entry, ramp_ring


def vcarve(shape: BaseGeometry, tool: Tool, step: float = 0.1, max_depth: float | None = None,
           feed: float = 800, plunge: float = 300, safe_z: float = 5.0, top_z: float = 0.0,
           climb: bool = True, entry: str = "ramp", ramp_angle: float = 3.0,
           name: str = "vcarve") -> Toolpath:
    if tool.kind != "vbit":
        raise ValueError("vcarve needs a V-bit")
    check_entry(entry, tool)
    limit = tool.depth_for_radius(tool.radius)       # bit fully engaged
    if max_depth is not None:
        limit = min(limit, max_depth)
    passes: list[Toolpath] = []
    r = step
    while True:
        depth = tool.depth_for_radius(r)
        if depth > limit + 1e-9:
            break
        inset = offset(shape, -r)
        if inset.is_empty:
            break
        level = Toolpath(name, tool)
        for p in polygons(inset):
            for ring in rings(p, climb, wall_outside=True):
                if entry == "plunge":
                    level.cut_polyline(ring, top_z - depth, safe_z, feed, plunge)
                else:
                    approach(level, ring[0][0], ring[0][1], top_z, top_z, safe_z, plunge)
                    ramp_ring(level, ring, top_z, top_z - depth, ramp_angle, feed)
                    level.rapid(z=safe_z)
        passes.append(level)
        r += step
    # Deepest (innermost) passes first; the edge-defining shallow pass runs last
    # so it is the clean finishing cut.
    tp = Toolpath(name, tool)
    for level in reversed(passes):
        tp.moves.extend(level.moves)
    return tp


def vcarve_flat_bottom_area(shape: BaseGeometry, tool: Tool, flat_depth: float) -> BaseGeometry:
    """Region a flat endmill must clear when V-carving with a depth cap."""
    r = flat_depth * math.tan(math.radians(tool.v_angle) / 2)
    return offset(shape, -r)
