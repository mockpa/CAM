"""Profile (contour) cutting: inside, outside or on the line."""
from __future__ import annotations

from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import JOIN_ROUND, polygons, rings, step_depths
from .entry import approach, check_entry, ramp_ring


def profile(shape: BaseGeometry, tool: Tool, depth: float, step_down: float,
            side: str = "outside", feed: float = 1000, plunge: float = 300,
            safe_z: float = 5.0, climb: bool = True, top_z: float = 0.0,
            entry: str = "ramp", ramp_angle: float = 3.0,
            name: str = "profile") -> Toolpath:
    check_entry(entry, tool)
    offset = {"outside": tool.radius, "inside": -tool.radius, "on": 0.0}[side]
    path_geom = shape.buffer(offset, join_style=JOIN_ROUND) if offset else shape
    tp = Toolpath(name, tool)
    contours = [r for p in polygons(path_geom) for r in rings(p, climb)]
    # Cut each contour through all depths before moving on: fewer long rapids.
    for ring in contours:
        if entry == "plunge":
            for z in step_depths(depth, step_down, top_z):
                tp.cut_polyline(ring, z, safe_z, feed, plunge)
            continue
        # Ramp down along the contour from one level to the next without retracting.
        approach(tp, ring[0][0], ring[0][1], top_z, top_z, safe_z, plunge)
        z_prev = top_z
        for z in step_depths(depth, step_down, top_z):
            ring = ramp_ring(tp, ring, z_prev, z, ramp_angle, feed)
            z_prev = z
        tp.rapid(z=safe_z)
    return tp
