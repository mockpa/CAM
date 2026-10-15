"""V-carving.

method="offset": inset the shape by increasing distances r; the tool tip on the
inset contour at depth r / tan(angle/2) touches the original boundary exactly.
Stacking these contours cuts the V-groove everywhere, with corners rounded to
about `step` and one ring per step.

method="medial": the tip follows the medial axis (ops/medial.py) at the depth
whose cone just touches the walls, so one pass cuts the whole groove and
corners come out sharp (to `tolerance`). Where the groove would be deeper than
the bit or max_depth allows, the axis is cut off and the inset contour at that
depth cuts the walls instead, as in the offset method. The tip has to go down
along the V's own walls (into corners, and at the deepest point of a shape it
plunges straight down: no gentler path reaches it without gouging a wall), so
this needs entry="plunge" and a centre-cutting V-bit. Chains start at corner
tips where they can, so most descents are slopes, not plunges.
"""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset, polygons, rings
from .entry import approach, check_entry, ramp_ring
from .medial import medial_axis


def vcarve(shape: BaseGeometry, tool: Tool, step: float = 0.1, max_depth: float | None = None,
           feed: float = 800, plunge: float = 300, safe_z: float = 5.0, top_z: float = 0.0,
           climb: bool = True, entry: str = "ramp", ramp_angle: float = 3.0,
           method: str = "offset", tolerance: float = 0.01, name: str = "vcarve") -> Toolpath:
    if tool.kind != "vbit":
        raise ValueError("vcarve needs a V-bit")
    check_entry(entry, tool)
    limit = tool.depth_for_radius(tool.radius)       # bit fully engaged
    if max_depth is not None:
        limit = min(limit, max_depth)
    if method == "medial":
        if entry != "plunge":
            raise ValueError('vcarve method "medial" descends along the V walls and plunges at the deepest '
                             'points, so it needs entry "plunge" (and a center_cutting V-bit)')
        if not 0 < tolerance <= 0.1:
            raise ValueError("tolerance must be in (0, 0.1] mm")
        return _medial(shape, tool, limit, tolerance, feed, plunge, safe_z, top_z, climb, name)
    if method != "offset":
        raise ValueError(f'vcarve method must be "offset" or "medial", not {method!r}')
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


def _medial(shape, tool, limit, tolerance, feed, plunge, safe_z, top_z, climb, name) -> Toolpath:
    r_limit = limit * math.tan(math.radians(tool.v_angle) / 2)
    tp = Toolpath(name, tool)

    def z_of(r):
        return top_z - tool.depth_for_radius(min(r, r_limit))

    # where the groove is capped: the inset contour at the cap cuts the walls (deepest, first)
    for p in polygons(offset(shape, -r_limit)):
        for ring in rings(p, climb, wall_outside=True):
            tp.cut_polyline(ring, top_z - limit, safe_z, feed, plunge)

    pieces = []                                   # (xy, r, start key, end key)
    visited = set()                               # nodes cut to their depth already
    for k, poly in enumerate(polygons(shape)):
        for c in medial_axis(poly, tolerance):
            for xy, r, a, b in _clip(c, r_limit, k):
                pieces.append((xy, r, a, b))
                visited |= {key for key in (a, b) if isinstance(key, tuple) and key[0] == "ring"}
    degree: dict = {}
    for _, _, a, b in pieces:
        for key in (a, b):
            if key is not None:
                degree[key] = degree.get(key, 0) + 1

    here = None                                   # node the tool is at, at depth
    pos = None
    todo = list(range(len(pieces)))
    while todo:
        def ends(i):
            xy, r, a, b = pieces[i]
            yield i, False, a, xy[0], r[0]
            if b is not None and b != a:
                yield i, True, b, xy[-1], r[-1]
        cand = [e for i in todo for e in ends(i)]
        nxt = [e for e in cand if here is not None and e[2] == here]
        if nxt:
            pick, link = max(nxt, key=lambda e: e[4]), "stay"
        else:
            ref = pos if pos is not None else cand[0][3]
            near = lambda e: math.dist(ref, e[3])
            known = [e for e in cand if e[2] in visited]
            if known:
                pick, link = min(known, key=near), "air"
            else:
                tips = [e for e in cand if degree.get(e[2], 0) == 1]
                pick = min(tips, key=near) if tips else min(cand, key=lambda e: (e[4], near(e)))
                link = "plunge"
        i, flip, key, start, r0 = pick
        todo.remove(i)
        xy, r, a, b = pieces[i]
        if flip:
            xy, r, a, b = xy[::-1], r[::-1], b, a
        if link != "stay":
            if link == "air":
                approach(tp, start[0], start[1], z_of(r0), top_z, safe_z, plunge)
            else:
                approach(tp, start[0], start[1], top_z, top_z, safe_z, plunge)
                tp.feed(z=z_of(r0), f=plunge)
        for (x, y), ri in zip(xy[1:], r[1:]):
            tp.feed(x=float(x), y=float(y), z=z_of(ri), f=feed)
        visited |= {a, b}
        if b is None:                              # a single point: lift out again
            tp.rapid(z=safe_z)
            here, pos = None, (float(xy[0][0]), float(xy[0][1]))
        else:
            here, pos = b, (float(xy[-1][0]), float(xy[-1][1]))
    if tp.moves:
        tp.rapid(z=safe_z)
    return tp


def _clip(chain, r_limit, k):
    """The parts of a medial chain no deeper than the cap, as (xy, r, start, end).
    Node keys are made unique per polygon; a cut-off end lies on the capped
    contour (cut first), so it is keyed as already cut."""
    xy, r = chain.pts, chain.r
    key = lambda v: None if v is None else (k, v)
    if r.max() <= r_limit + 1e-9:
        return [(xy, r, key(chain.start), key(chain.end))]
    out, cur, start = [], None, key(chain.start)
    for i in range(len(xy)):
        if r[i] <= r_limit + 1e-9:
            if cur is None:
                cur = ([xy[i]], [r[i]], start)
            else:
                cur[0].append(xy[i])
                cur[1].append(r[i])
        if i + 1 < len(xy) and (r[i] <= r_limit) != (r[i + 1] <= r_limit):
            t = (r_limit - r[i]) / (r[i + 1] - r[i])
            p = xy[i] + (xy[i + 1] - xy[i]) * t
            ring = ("ring", k, i, float(p[0]), float(p[1]))
            if cur is None:                        # coming out of the capped part
                cur = ([p], [r_limit], ring)
            else:                                  # going into it
                cur[0].append(p)
                cur[1].append(r_limit)
                out.append((np.array(cur[0]), np.array(cur[1]), cur[2], ring))
                cur = None
    if cur is not None:
        out.append((np.array(cur[0]), np.array(cur[1]), cur[2], key(chain.end)))
    return out


def vcarve_flat_bottom_area(shape: BaseGeometry, tool: Tool, flat_depth: float) -> BaseGeometry:
    """Region a flat endmill must clear when V-carving with a depth cap."""
    r = flat_depth * math.tan(math.radians(tool.v_angle) / 2)
    return offset(shape, -r)
