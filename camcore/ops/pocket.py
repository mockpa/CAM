"""Contour-parallel pocket clearing."""
from __future__ import annotations

from shapely.geometry import LinearRing, LineString, Point
from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset, polygons, rings, step_depths
from .entry import approach, check_entry, helix_down, plan_helix, ramp_ring, rotate_ring


def offset_loops(shape: BaseGeometry, first: float, stepover: float, climb: bool = True):
    """Successive inward offsets: first by `first`, then by `stepover`, until empty.

    Returns a list of rings ordered innermost first, so clearing works outwards
    and the final pass is the finishing wall pass.
    """
    loops = []
    current = offset(shape, -first)
    while not current.is_empty:
        for p in polygons(current):
            loops.extend(rings(p, climb, wall_outside=True))
        current = offset(current, -stepover)
    loops.reverse()
    return loops


def pocket(shape: BaseGeometry, tool: Tool, depth: float, step_down: float,
           stepover_ratio: float = 0.4, feed: float = 1000, plunge: float = 300,
           safe_z: float = 5.0, climb: bool = True, top_z: float = 0.0,
           entry: str = "helix", ramp_angle: float = 3.0, helix_diameter: float | None = None,
           name: str = "pocket") -> Toolpath:
    """Clear `shape` level by level. Within a level the tool links from loop to loop at
    depth where the straight link stays inside the pocket; elsewhere it retracts and
    enters again (helix, falling back to a ramp along the loop, or plunge)."""
    if not 0 < stepover_ratio <= 1:
        raise ValueError("stepover_ratio must be in (0, 1]")
    check_entry(entry, tool, ("helix", "ramp", "plunge"))
    stepover = tool.diameter * stepover_ratio
    loops = offset_loops(shape, tool.radius, stepover, climb)
    allowed = offset(shape, -tool.radius + 1e-6)   # where the tool centre may go
    tp = Toolpath(name, tool)
    z_prev = top_z
    for z in step_depths(depth, step_down, top_z):
        here = None                                   # (x, y) while the tool is down at z
        for ring in loops:
            lr = LinearRing(ring)
            if here is not None:
                d = lr.project(Point(here))
                p = lr.interpolate(d)
                if LineString([here, (p.x, p.y)]).within(allowed.buffer(1e-6)):
                    loop = rotate_ring(ring, d)
                    tp.feed(x=loop[0][0], y=loop[0][1], f=feed)
                    for x, y in loop[1:]:
                        tp.feed(x=x, y=y, f=feed)
                    here = loop[0]
                    continue
            helix = plan_helix(ring, allowed, tool, helix_diameter) if entry == "helix" else None
            if helix:
                (cx, cy), r, d = helix
                approach(tp, cx + r, cy, z_prev, top_z, safe_z, plunge)
                helix_down(tp, (cx, cy), r, z_prev, z, ramp_angle, feed, climb)
                loop = rotate_ring(ring, d)
                for x, y in loop:
                    tp.feed(x=x, y=y, z=z, f=feed)
            elif entry == "plunge":
                approach(tp, ring[0][0], ring[0][1], z_prev, top_z, safe_z, plunge)
                tp.feed(z=z, f=plunge)
                loop = list(ring)
                for x, y in loop[1:]:
                    tp.feed(x=x, y=y, f=feed)
            else:
                approach(tp, ring[0][0], ring[0][1], z_prev, top_z, safe_z, plunge)
                loop = ramp_ring(tp, ring, z_prev, z, ramp_angle, feed)
            here = loop[0]
        tp.rapid(z=safe_z)
        z_prev = z
    return tp
