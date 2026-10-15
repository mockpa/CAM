"""DXF importer (ezdxf): LINE, ARC, CIRCLE, LWPOLYLINE (with bulges), POLYLINE,
SPLINE, ELLIPSE, and block INSERTs (exploded). Curves are flattened with chords
within CHORD_TOL, so the post can fit G2/G3 arcs back onto them.

Open pieces are joined end to end within `join_tolerance` into closed contours;
nested contours become holes (even-odd). A contour that still does not close is
an error (with the gap's location) unless ignore_open=True, because a CAM job
cannot pocket or profile an open outline.

Units come from $INSUNITS (unitless is taken as mm). Annotations (text,
dimensions, hatches, points, ...) are ignored.
"""
from __future__ import annotations

import math

from .ops.common import CHORD_TOL
from .shapes import join_chains, rings_to_geom

_UNITS_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 0.0254, 9: 0.0254, 10: 914.4,
             13: 0.001, 14: 100.0}
GEOMETRY = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"}
IGNORED = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER", "HATCH", "POINT", "VIEWPORT",
           "ATTDEF", "ATTRIB", "SOLID", "WIPEOUT", "IMAGE", "XLINE", "RAY", "TOLERANCE"}


def _entities(container, layers):
    for e in container:
        kind = e.dxftype()
        if kind == "INSERT":
            if layers is None or e.dxf.layer in layers:
                yield from _entities(e.virtual_entities(), None)   # block content follows the insert's layer
            continue
        if layers is not None and e.dxf.layer not in layers:
            continue
        if kind in GEOMETRY:
            yield e
        elif kind not in IGNORED:
            raise ValueError(f"unsupported DXF entity {kind} on layer {e.dxf.layer}")


def load_dxf(path: str, scale: float = 1.0, layers=None, join_tolerance: float = 0.01,
             ignore_open: bool = False):
    """Return a shapely (Multi)Polygon from a DXF's modelspace."""
    import ezdxf
    from ezdxf import path as dxfpath

    doc = ezdxf.readfile(path)
    units = doc.header.get("$INSUNITS", 0)
    if units not in _UNITS_MM:
        raise ValueError(f"unsupported DXF units $INSUNITS={units}")
    k = _UNITS_MM[units] * scale
    layers = set(layers) if layers is not None else None
    closed, pieces = [], []
    for e in _entities(doc.modelspace(), layers):
        p = dxfpath.make_path(e)
        pts = [(v.x * k, v.y * k) for v in p.flattening(distance=CHORD_TOL / k if k else CHORD_TOL)]
        if len(pts) < 2:
            continue
        if math.dist(pts[0], pts[-1]) <= 1e-9 and len(pts) >= 4:
            closed.append(pts)
        else:
            pieces.append(pts)
    joined, open_ = join_chains(pieces, join_tolerance)
    if open_ and not ignore_open:
        x, y = open_[0][-1]
        raise ValueError(f"{path}: {len(open_)} contour(s) don't close within {join_tolerance} mm, "
                         f"e.g. an open end at ({x:.3f}, {y:.3f}); fix the drawing, raise join_tolerance, "
                         f"pick layers, or set ignore_open")
    if not closed and not joined:
        raise ValueError(f"{path}: no closed contours found")
    return rings_to_geom(closed + joined)
