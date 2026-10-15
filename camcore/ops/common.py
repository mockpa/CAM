"""Shared geometry helpers for operations."""
from __future__ import annotations

import math

from shapely.geometry import LinearRing, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

JOIN_ROUND = "round"
CHORD_TOL = 0.005    # mm: max bulge of a straight segment against the true arc it stands for


def quad_segs(radius: float) -> int:
    """Segments per quarter circle so each chord is within CHORD_TOL of the arc
    (fine enough for the post to fit G2/G3 arcs back within its 0.01 mm)."""
    r = abs(radius)
    if r <= CHORD_TOL:
        return 4
    step = 2 * math.acos(1 - CHORD_TOL / r)
    return max(8, math.ceil((math.pi / 2) / step))


def offset(geom: BaseGeometry, d: float) -> BaseGeometry:
    """Round-joined offset (inwards for d < 0) with arcs as fine as quad_segs()."""
    return geom.buffer(d, quad_segs=quad_segs(d), join_style=JOIN_ROUND)


def polygons(geom: BaseGeometry) -> list[Polygon]:
    """Flatten any geometry into a list of non-empty polygons."""
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(polygons(g))
        return out
    return []


def rings(poly: Polygon, climb: bool = True, wall_outside: bool = False) -> list[list[tuple[float, float]]]:
    """Exterior + interior rings oriented for climb (or conventional) milling.

    With a clockwise-spinning (M3) spindle, climb milling keeps the wall on the
    right of the direction of travel and the waste on the left.

    wall_outside=False: the material to keep is inside the exterior ring (outside
    profiles): exterior clockwise, holes counter-clockwise.
    wall_outside=True: the material to keep is outside the exterior ring and
    inside the holes (pockets, inside profiles, V-carve): exterior
    counter-clockwise, islands clockwise.
    """
    cw_exterior = climb != wall_outside
    out = []
    ext = LinearRing(poly.exterior.coords)
    ext_coords = list(ext.coords)
    if ext.is_ccw == cw_exterior:
        ext_coords.reverse()
    out.append(ext_coords)
    for hole in poly.interiors:
        r = LinearRing(hole.coords)
        c = list(r.coords)
        if r.is_ccw != cw_exterior:
            c.reverse()
        out.append(c)
    return out


def step_depths(total_depth: float, step_down: float, top_z: float = 0.0) -> list[float]:
    """Z levels from top_z down to top_z - total_depth in increments <= step_down."""
    if total_depth <= 0:
        return []
    n = max(1, int(-(-total_depth // step_down)))
    return [top_z - total_depth * (i + 1) / n for i in range(n)]
