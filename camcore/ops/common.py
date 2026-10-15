"""Shared geometry helpers for operations."""
from __future__ import annotations

from shapely.geometry import LinearRing, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

JOIN_ROUND = "round"


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


def rings(poly: Polygon, climb: bool = True) -> list[list[tuple[float, float]]]:
    """Exterior + interior rings oriented for climb (or conventional) milling.

    With a clockwise-spinning spindle, climb milling keeps material on the right:
    outer boundaries are cut clockwise, holes counter-clockwise.
    """
    out = []
    ext = LinearRing(poly.exterior.coords)
    ext_coords = list(ext.coords)
    if ext.is_ccw == climb:
        ext_coords.reverse()
    out.append(ext_coords)
    for hole in poly.interiors:
        r = LinearRing(hole.coords)
        c = list(r.coords)
        if r.is_ccw != climb:
            c.reverse()
        out.append(c)
    return out


def step_depths(total_depth: float, step_down: float, top_z: float = 0.0) -> list[float]:
    """Z levels from top_z down to top_z - total_depth in increments <= step_down."""
    if total_depth <= 0:
        return []
    n = max(1, int(-(-total_depth // step_down)))
    return [top_z - total_depth * (i + 1) / n for i in range(n)]
