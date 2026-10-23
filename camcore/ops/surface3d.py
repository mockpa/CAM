"""3-axis machining of a mesh: Z-level roughing, raster and waterline finishing.

The model is in machine coordinates (Z0 at the stock top, see job.build_model).
Cutter locations come from camcore.surface (rust/surface_kernel): drop-cutter for
raster passes, waterline loops for roughing levels and waterline finishing.
Both are exact against the triangles at the sampled points; between samples a
path is a straight move, and decimation drops points the straight move passes
within `tolerance` of (compared at the same XY, as the controller moves).

Roughing pockets each level with the 2.5D pocket machinery (helix or ramp
entries, links at depth inside the cleared region). The region a level may
clear is the boundary minus the waterline loops of the cutter grown by
`stock_to_leave`: loops shrink upwards (the cutter is treated as infinitely
long), so each level lies inside the one above, which is already cleared.
"""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LinearRing, LineString, Point, Polygon, box

from .. import surface
from ..shapes import rings_to_geom
from ..tools import Tool
from ..toolpath import Toolpath
from .entry import check_entry
from .pocket import clear_levels, offset_loops


def _model_box(model):
    (x0, y0, z0), (x1, y1, z1) = model.bounds
    return float(x0), float(y0), float(z0), float(x1), float(y1), float(z1)


def _boundary(boundary, default):
    x0, y0, x1, y1 = boundary if boundary is not None else default
    if not (x1 > x0 and y1 > y0):
        raise ValueError("boundary must be [x_min, y_min, x_max, y_max] with min < max")
    return box(x0, y0, x1, y1)


def decimate(pts: np.ndarray, tol: float, below: bool = True, left: bool = True) -> np.ndarray:
    """Drop points of an (n, 3) polyline that the straight move between their
    kept neighbours passes within tol of. A dropped point is compared with the
    move at the same XY position (the controller moves X, Y and Z together),
    so a steep flank cannot hide a Z error behind a small perpendicular distance.
    With below=False the move may pass above dropped points but never under one
    (the points are the lowest heights that do not gouge). With left=False it
    never passes to the right of one (climb contours: the wall is on the right)."""
    if len(pts) <= 2:
        return pts
    keep, s, n = [0], 0, len(pts)

    def ok(e):
        q = pts[s:e + 1] - pts[s]
        seg, mid = q[-1], q[1:-1]
        L2 = float(seg[:2] @ seg[:2])
        if L2 < 1e-18:
            if abs(seg[2]) < 1e-12:
                return bool((np.einsum("ij,ij->i", mid, mid) <= tol * tol).all())
            t = np.clip(mid[:, 2] / seg[2], 0, 1)
        else:
            t = np.clip(mid[:, :2] @ seg[:2] / L2, 0, 1)
        dev = mid - t[:, None] * seg
        if not below and (dev[:, 2] > 1e-9).any():
            return False
        if not left and (seg[0] * dev[:, 1] - seg[1] * dev[:, 0] > 1e-9).any():
            return False
        return bool((np.einsum("ij,ij->i", dev, dev) <= tol * tol).all())

    while s < n - 1:
        e, step, bad = s + 1, 1, None          # gallop while the move still fits ...
        while e < n - 1:
            nxt = min(e + step, n - 1)
            if ok(nxt):
                e, step = nxt, step * 2
            else:
                bad = nxt
                break
        while bad is not None and bad - e > 1:  # ... then bisect between a good and a bad end
            mid = (e + bad) // 2
            if ok(mid):
                e = mid
            else:
                bad = mid
        keep.append(e)
        s = e
    return pts[keep]


def limit_descent(pts: np.ndarray, max_descent: float) -> np.ndarray:
    """Raise points so the path never goes down steeper than max_descent (deg)
    along its travel; it only ever stays higher than the input."""
    out = pts.copy()
    k = math.tan(math.radians(max_descent))
    for i in range(1, len(out)):
        run = math.hypot(out[i, 0] - out[i - 1, 0], out[i, 1] - out[i - 1, 1])
        out[i, 2] = max(out[i, 2], out[i - 1, 2] - k * run)
    return out


def surface_rough(model, tool: Tool, stock: dict, step_down: float, stock_to_leave: float = 0.5,
                  stepover_ratio: float = 0.4, feed: float = 1500, plunge: float = 300, safe_z: float = 5.0,
                  floor: float | None = None, boundary=None, sampling: float = 0.2, entry: str = "helix",
                  ramp_angle: float = 3.0, helix_diameter: float | None = None, climb: bool = True,
                  name: str = "surface-rough") -> Toolpath:
    """Z-level roughing of box stock down to the model plus `stock_to_leave`.

    Levels run from `step_down` below the stock top down to `floor` (default:
    the model's lowest point plus stock_to_leave, never below the stock bottom).
    `boundary` [x0, y0, x1, y1] limits the tool centre (default: the stock).
    """
    if tool.kind not in ("flat", "ball", "bull"):
        raise ValueError("3D roughing needs a flat, ball or bull-nose end mill")
    if not stock or stock.get("type") != "box":
        raise ValueError("3D roughing needs box stock")
    if step_down <= 0:
        raise ValueError("step_down must be > 0")
    if not 0 < stepover_ratio <= 1:
        raise ValueError("stepover_ratio must be in (0, 1]")
    if stock_to_leave < 0:
        raise ValueError("stock_to_leave must be >= 0")
    check_entry(entry, tool, ("helix", "ramp", "plunge"))
    bottom, top = stock["z"]
    if safe_z <= top:
        raise ValueError(f"safe_z {safe_z} must be above the stock top {top}")
    mx0, my0, mz0, mx1, my1, mz1 = _model_box(model)
    if floor is None:
        floor = max(mz0 + stock_to_leave, bottom)
    if floor < bottom - 1e-9:
        raise ValueError(f"floor {floor} is below the stock bottom {bottom}")
    region = _boundary(boundary, (stock["x"][0], stock["y"][0], stock["x"][1], stock["y"][1]))
    tp = Toolpath(name, tool)
    if floor >= top:
        tp.notes.append(f"{name}: floor {floor:g} is not below the stock top, nothing to rough")
        return tp
    levels = list(np.arange(top - step_down, floor + 1e-9, -step_down))
    if not levels or levels[-1] > floor + 1e-9:
        levels.append(floor)
    stepover = tool.diameter * stepover_ratio
    z_prev = top
    for z in levels:
        loops = surface.waterline(model, tool, float(z), sampling, stock_to_leave,
                                  keep=lambda lp: Polygon(lp).intersects(region))
        allowed = region.difference(rings_to_geom([lp.tolist() for lp in loops]))
        if allowed.is_empty or allowed.area < 1e-6:
            tp.notes.append(f"{name}: nothing left to clear from Z{z:g} down")
            break
        centre = offset_loops(allowed, 0.0, stepover, climb)
        if not centre:
            continue
        clear_levels(tp, centre, allowed, tool, [float(z)], z_prev, top, safe_z, feed, plunge, climb,
                     entry, ramp_angle, helix_diameter)
        z_prev = float(z)
    return tp


def surface_finish(model, tool: Tool, strategy: str = "raster", stepover: float | None = None,
                   stepover_ratio: float = 0.15, angle: float = 0.0, step_down: float | None = None,
                   feed: float = 1500, plunge: float = 300, safe_z: float = 5.0, floor: float | None = None,
                   boundary=None, z_range=None, sampling: float = 0.2, tolerance: float = 0.005,
                   entry_clearance: float = 1.0, ramp_angle: float = 10.0, max_descent: float = 30.0,
                   name: str = "surface-finish") -> Toolpath:
    """Finish the model surface (nothing left).

    strategy "raster": parallel passes at `angle` degrees from X, `stepover`
    apart (default stepover_ratio * diameter), linked zigzag along the boundary
    and dropped onto the model, so the links never gouge either. Best on
    shallow areas. Not centre-cutting tools descend no steeper than max_descent.

    strategy "waterline": closed contours at Z levels `step_down` apart
    (default: the stepover) from the top of `z_range` (default: the model)
    down, climb milled, each entered by a ramp along the contour. Best on steep
    walls; it leaves flat areas alone.

    `boundary` [x0, y0, x1, y1] limits the tool centre (default: the model's
    extent plus the tool radius); `floor` is the lowest tip Z (default: the
    model's lowest point).
    """
    if strategy not in ("raster", "waterline"):
        raise ValueError(f"unknown 3D finishing strategy {strategy!r}")
    s = stepover if stepover is not None else tool.diameter * stepover_ratio
    if not 0 < s <= tool.diameter:
        raise ValueError("stepover must be in (0, tool diameter]")
    mx0, my0, mz0, mx1, my1, mz1 = _model_box(model)
    if safe_z <= mz1:
        raise ValueError(f"safe_z {safe_z} must be above the model top {mz1:g}")
    floor = mz0 if floor is None else floor
    R = tool.radius
    region = _boundary(boundary, (mx0 - R, my0 - R, mx1 + R, my1 + R))
    tp = Toolpath(name, tool)
    if strategy == "raster":
        _raster(tp, model, tool, region, s, angle, floor, safe_z, feed, plunge, sampling, tolerance,
                entry_clearance, None if tool.center_cutting else max_descent)
    else:
        lo, hi = z_range if z_range is not None else (floor, mz1)
        dz = step_down if step_down is not None else s
        if dz <= 0 or hi < lo:
            raise ValueError("waterline needs step_down > 0 and z_range [low, high] with low <= high")
        _waterline(tp, model, tool, region, lo, hi, dz, safe_z, feed, plunge, sampling, tolerance, ramp_angle)
    return tp


def raster_lines(region, stepover: float, angle: float) -> list[list[tuple[float, float]]]:
    """Parallel passes across a convex region, `stepover` apart, alternating
    direction (zigzag). The first and last pass lie on the region's edges."""
    c = region.centroid
    turned = rotate(region, -angle, origin=c)
    x0, y0, x1, y1 = turned.bounds
    n = max(1, math.ceil((y1 - y0) / stepover - 1e-9))
    lines = []
    for k in range(n + 1):
        y = min(y0 + k * (y1 - y0) / n, y1)
        seg = rotate(LineString([(x0 - 1, y), (x1 + 1, y)]), angle, origin=c).intersection(region)
        if seg.is_empty or seg.length < 1e-9:
            continue
        pts = list(seg.coords) if seg.geom_type == "LineString" else list(max(seg.geoms, key=lambda g: g.length).coords)
        lines.append(pts if len(lines) % 2 == 0 else pts[::-1])
    return lines


def _raster(tp, model, tool, region, s, angle, floor, safe_z, feed, plunge, sampling, tolerance,
            entry_clearance, max_descent):
    lines = raster_lines(region, s, angle)
    poly = [p for line in lines for p in line]     # zigzag: each pass ends where the link to the next starts
    pts = surface.drop_path(model, tool, poly, floor, sampling, tolerance=tolerance)
    start = pts[0].copy()
    start[2] = min(start[2] + entry_clearance, safe_z)
    tp.rapid(z=safe_z)
    tp.rapid(x=float(start[0]), y=float(start[1]))
    tp.feed(z=float(start[2]), f=plunge)
    if max_descent is None:
        tp.feed(z=float(pts[0, 2]), f=plunge)       # centre-cutting: straight down the entry clearance
        path = pts
    else:
        path = limit_descent(np.vstack([start, pts]), max_descent)
    for x, y, z in decimate(path, tolerance, below=False)[1:]:
        tp.feed(x=float(x), y=float(y), z=float(z), f=feed)
    tp.rapid(z=safe_z)


def _oriented_loops(loops):
    """Closed rings (last point = first) in climb direction (wall on the right):
    a loop around material (even nesting depth) runs clockwise, a loop inside
    another one counter-clockwise."""
    polys = [Polygon(lp) for lp in loops]
    out = []
    for i, lp in enumerate(loops):
        depth = sum(1 for j, p in enumerate(polys) if j != i and p.contains(Point(lp[0])))
        ring = list(lp) + [lp[0]]
        if LinearRing(ring).is_ccw == (depth % 2 == 0):      # want clockwise at even depth
            ring.reverse()
        out.append(ring)
    return out


def _ramp_loop(ring, z_from: float, z_to: float, angle: float):
    """(x, y, z) points: down along the closed ring from z_from to z_to at no more
    than `angle` degrees (as many turns as that takes), then one full turn at z_to."""
    pts = np.asarray(ring[:-1] if ring[0] == ring[-1] else ring, dtype=float)
    n = len(pts)
    seg = np.hypot(*(np.roll(pts, -1, axis=0) - pts).T)
    total = float(seg.sum())
    need = (z_from - z_to) / math.tan(math.radians(angle))
    out, dist, i = [(*pts[0], z_from)], 0.0, 0
    while dist < need - 1e-9:
        dist += seg[i % n]
        i += 1
        z = max(z_to, z_from - (z_from - z_to) * min(1.0, dist / need)) if need > 0 else z_to
        out.append((*pts[i % n], z))
    for k in range(1, n + 1):
        out.append((*pts[(i + k) % n], z_to))
    return np.array(out), total


def _waterline(tp, model, tool, region, lo, hi, dz, safe_z, feed, plunge, sampling, tolerance, ramp_angle):
    levels = list(np.arange(hi - dz, lo - 1e-9, -dz)) if hi - dz >= lo - 1e-9 else []
    if not levels or levels[-1] > lo + 1e-9:
        levels.append(lo)
    bx0, by0, bx1, by1 = region.bounds
    reach = tool.radius + 1.0
    part = surface.crop(model, bx0 - reach, by0 - reach, bx1 + reach, by1 + reach)
    if part is None:
        tp.notes.append(f"{tp.name}: no model inside the boundary")
        return
    # fiber points lie up to sqrt(2) * spacing apart along a contour; tool-centre contours
    # bend no tighter than the cutter radius where they go round the model, so this
    # spacing keeps the chords between them within `tolerance` of the contour
    fibers = min(sampling, math.sqrt(4 * tool.radius * tolerance))
    skipped = 0
    for z in levels:
        crossing = []

        def keep(lp):
            ring = Polygon(lp).exterior
            if region.contains(ring):
                return True
            if ring.intersects(region.exterior):      # cut by the boundary: not milled, reported
                crossing.append(lp)
            return False

        inside = [[tuple(p) for p in lp] for lp in surface.waterline(part, tool, float(z), fibers, keep=keep)]
        skipped += len(crossing)
        for ring in _oriented_loops(inside):
            path, _ = _ramp_loop(ring, min(float(z) + dz, safe_z), float(z), ramp_angle)
            tp.rapid(z=safe_z)
            tp.rapid(x=float(path[0, 0]), y=float(path[0, 1]))
            tp.feed(z=float(path[0, 2]), f=plunge)
            for x, y, zz in decimate(path, tolerance, below=False, left=False)[1:]:
                tp.feed(x=float(x), y=float(y), z=float(zz), f=feed)
        tp.rapid(z=safe_z)
    if skipped:
        tp.notes.append(f"{tp.name}: {skipped} waterline contour(s) cross the boundary and were skipped")
