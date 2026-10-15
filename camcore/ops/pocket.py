"""Contour-parallel pocket clearing."""
from __future__ import annotations

from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import JOIN_ROUND, polygons, rings, step_depths


def offset_loops(shape: BaseGeometry, first: float, stepover: float, climb: bool = True):
    """Successive inward offsets: first by `first`, then by `stepover`, until empty.

    Returns a list of rings ordered innermost first, so clearing works outwards
    and the final pass is the finishing wall pass.
    """
    loops = []
    current = shape.buffer(-first, join_style=JOIN_ROUND)
    while not current.is_empty:
        for p in polygons(current):
            loops.extend(rings(p, climb))
        current = current.buffer(-stepover, join_style=JOIN_ROUND)
    loops.reverse()
    return loops


def pocket(shape: BaseGeometry, tool: Tool, depth: float, step_down: float,
           stepover_ratio: float = 0.4, feed: float = 1000, plunge: float = 300,
           safe_z: float = 5.0, climb: bool = True, top_z: float = 0.0,
           name: str = "pocket") -> Toolpath:
    if not 0 < stepover_ratio <= 1:
        raise ValueError("stepover_ratio must be in (0, 1]")
    stepover = tool.diameter * stepover_ratio
    loops = offset_loops(shape, tool.radius, stepover, climb)
    tp = Toolpath(name, tool)
    for z in step_depths(depth, step_down, top_z):
        for ring in loops:
            tp.cut_polyline(ring, z, safe_z, feed, plunge)
    return tp
