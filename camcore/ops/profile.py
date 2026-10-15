"""Profile (contour) cutting: inside, outside or on the line."""
from __future__ import annotations

from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import JOIN_ROUND, polygons, rings, step_depths
from .contour import RingParam, Tabs, cut_contour
from .entry import approach, check_entry


def profile(shape: BaseGeometry, tool: Tool, depth: float, step_down: float,
            side: str = "outside", feed: float = 1000, plunge: float = 300,
            safe_z: float = 5.0, climb: bool = True, top_z: float = 0.0,
            entry: str = "ramp", ramp_angle: float = 3.0, tabs: dict | None = None,
            name: str = "profile") -> Toolpath:
    """Cut along the shape's boundary. Each contour goes through all depths without
    retracting: ramping down along the contour (or plunging, centre-cutting tools
    only). `tabs` = {"count" or "spacing", "width", "height", optional "ramp_angle"}
    leaves holding tabs: passes below the tab top rise over them and ramp back down."""
    check_entry(entry, tool)
    offset = {"outside": tool.radius, "inside": -tool.radius, "on": 0.0}[side]
    path_geom = shape.buffer(offset, join_style=JOIN_ROUND) if offset else shape
    tp = Toolpath(name, tool)
    contours = [r for p in polygons(path_geom) for r in rings(p, climb)]
    levels = step_depths(depth, step_down, top_z)
    for ring in contours:
        tab = Tabs(RingParam(ring).length, tabs, tool.diameter, top_z - depth, top_z) if tabs else None
        approach(tp, ring[0][0], ring[0][1], top_z, top_z, safe_z, plunge)
        cut_contour(tp, ring, levels, top_z, entry, ramp_angle, feed, plunge, tab)
        tp.rapid(z=safe_z)
    return tp
