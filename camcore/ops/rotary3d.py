"""Continuous rotary machining of a mesh (4th axis, A around X).

The model is placed with the rotary axis on its X axis (see stl.place). Both
operations work on the cylindrical heightmap of camcore.cylmap: r_tool(x, A)
is the lowest tip radius that does not gouge, sampled on a grid of tool
centre positions. Z is the tip radius (Z0 on the axis); moves carry X, A, Z
and never Y, and the LinuxCNC post writes them in G93 inverse time.

Every pass starts with a ramp ring: the tool comes down above the material at
the start and descends over one full turn of A, so nothing plunges.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np

from ..cylmap import radial_map, tool_map
from ..tools import Tool
from ..toolpath import Toolpath

_MAPS: dict = {}     # (id(mesh), grid) -> (mesh, CylMap): ops on one model share the ray casting


class _Grid:
    """Tool centre positions: columns x0..x1 (both on the grid) times m angles."""

    def __init__(self, mesh, x_range, resolution: float, a_step: float | None, stock_radius: float,
                 reach: float):
        x0, x1 = x_range if x_range is not None else (float(mesh.bounds[0][0]), float(mesh.bounds[1][0]))
        if x1 < x0:
            raise ValueError("x_range must be [start, end] with start <= end")
        if resolution <= 0:
            raise ValueError("resolution must be > 0")
        n = max(1, math.ceil((x1 - x0) / resolution - 1e-9))
        self.res = (x1 - x0) / n if x1 > x0 else resolution
        self.xc = x0 + np.arange(n + 1 if x1 > x0 else 1) * self.res
        a = a_step if a_step is not None else math.degrees(resolution / stock_radius)
        self.m = max(36, int(round(360.0 / a)))
        # the heightmap reaches past both ends so the cutter sees the model beside it
        self.pad = int(math.ceil(reach / self.res)) + 1
        key = (id(mesh), round(self.res, 9), self.m, round(x0, 9), len(self.xc), self.pad)
        hit = _MAPS.get(key)
        if hit is None or hit[0] is not mesh:
            lo, hi = x0 - self.pad * self.res, x0 + (len(self.xc) - 1 + self.pad) * self.res
            cmap = radial_map(mesh, self.res, 360.0 / self.m, (lo, hi + 1e-9))
            if len(_MAPS) > 8:
                _MAPS.clear()
            _MAPS[key] = hit = (mesh, cmap)
        self.cmap = hit[1]
        self.a_step = 360.0 / self.m

    def surface(self, tool: Tool, leave: float = 0.0) -> np.ndarray:
        """Tip radius per centre column and angle that keeps `leave` mm off the model."""
        t = dataclasses.replace(tool, diameter=tool.diameter + 2 * leave) if leave > 0 else tool
        r = tool_map(self.cmap, t).r + leave
        return r[self.pad:self.pad + len(self.xc)]


def _stepover(tool: Tool, stepover, stepover_ratio) -> float:
    s = stepover if stepover is not None else tool.diameter * stepover_ratio
    if not 0 < s <= tool.diameter:
        raise ValueError("stepover must be in (0, tool diameter]")
    return s


def _envelope(surf: np.ndarray) -> np.ndarray:
    """Max over each grid node and its 8 neighbours (angles wrap around).

    The model is sampled on the same grid as the tool centres, so between nodes
    the no-gouge tip height is at most the max of the surrounding nodes: one of
    them is at least as close, in X and in angle, to every model sample. Any
    blend of envelope values inside a cell is at least that max, so paths that
    interpolate the envelope cannot gouge the sampled model, whatever the cutter
    shape (a ball's height rises steeply next to an edge, where plain linear
    interpolation cuts in).
    """
    out = surf.copy()
    for dj in (-1, 1):
        out = np.maximum(out, np.roll(surf, dj, axis=1))
    rows = out.copy()
    out[1:] = np.maximum(out[1:], rows[:-1])
    out[:-1] = np.maximum(out[:-1], rows[1:])
    return out


class _Path:
    """Feed points (x, a, z) of one cut, built step by step on the grid.

    With max_descent (deg) set, the path never goes down steeper than that along
    its travel (for tools that are not centre-cutting); it only ever stays higher.
    """

    def __init__(self, g: _Grid, surf: np.ndarray, j: int, a: float, max_descent: float | None = None):
        self.g, self.surf, self.j, self.a = g, surf, j, a
        self.slope = math.tan(math.radians(max_descent)) if max_descent is not None else None
        self.pts: list[tuple[float, float, float]] = []

    def add(self, x: float, a: float, z: float):
        if self.slope is not None and self.pts:
            px, pa, pz = self.pts[-1]
            run = math.hypot(x - px, math.radians(a - pa) * max(pz, 1.0))
            z = max(z, pz - self.slope * run)
        self.pts.append((x, a, z))

    def z(self, xf: float, j: int) -> float:
        """Surface at fractional column xf, linear between columns."""
        i = min(int(math.floor(xf)), len(self.g.xc) - 1)
        w = xf - i
        lo = self.surf[i, j % self.g.m]
        if w <= 1e-9:
            return float(lo)
        return float(lo + (self.surf[i + 1, j % self.g.m] - lo) * w)

    def x(self, xf: float) -> float:
        return float(self.g.xc[0] + xf * self.g.res)

    def turn(self, xf0: float, xf1: float, revs: float, d: int, floor_fn=None):
        """A turns `revs` full turns in direction d while X goes xf0 -> xf1 (helix; ring if equal)."""
        steps = max(1, int(round(revs * self.g.m)))
        for t in range(1, steps + 1):
            self.j += d
            self.a += d * self.g.a_step
            xf = xf0 + (xf1 - xf0) * t / steps
            z = self.z(xf, self.j)
            if floor_fn is not None:
                z = max(z, floor_fn(t / steps))
            self.add(self.x(xf), self.a, z)

    def line_x(self, xf0: float, xf1: float):
        """Straight along X at the current angle, at least one point per column."""
        n = int(math.ceil(abs(xf1 - xf0) - 1e-9))
        for t in range(1, n + 1):
            xf = xf0 + (xf1 - xf0) * t / n
            self.add(self.x(xf), self.a, self.z(xf, self.j))

    def step_a(self, xf: float, steps: int, d: int):
        for _ in range(steps):
            self.j += d
            self.a += d * self.g.a_step
            self.add(self.x(xf), self.a, self.z(xf, self.j))


def _start(tp: Toolpath, p: _Path, xf: float, z_entry: float, d: int, safe: float,
           feed: float, plunge: float):
    """Move over the start, come down to z_entry and ramp down over one turn."""
    tp.rapid(z=safe)
    tp.rapid(x=p.x(xf), a=p.a)
    tp.feed(z=z_entry, f=plunge)
    z_end = p.z(xf, p.j)
    p.pts = [(p.x(xf), p.a, z_entry)]
    p.turn(xf, xf, 1, d, floor_fn=lambda t: z_entry + (z_end - z_entry) * t)


def _emit(tp: Toolpath, p: _Path, feed: float, tolerance: float):
    for x, a, z in decimate(np.array(p.pts), tolerance)[1:]:
        tp.feed(x=float(x), a=float(a), z=float(z), f=feed)


def decimate(pts: np.ndarray, tol: float) -> np.ndarray:
    """Drop points the straight (X, A, Z) move between their neighbours passes within tol of.

    The controller moves X, A and Z together linearly, so a dropped point is
    compared with where the move actually is at that point's X/A position (not
    with the nearest point of the move: on a steep flank that can hide a large
    Z error). A is measured as surface length at the point's radius, so tol is mm.
    """
    if len(pts) <= 2:
        return pts
    keep, s, n = [0], 0, len(pts)

    def ok(e):
        p = pts[s:e + 1]
        r = max(float(p[:, 2].max()), 1.0)     # A from the move start, as arc length at the outermost radius
        q = np.stack([p[:, 0] - p[0, 0], np.radians(p[:, 1] - p[0, 1]) * r, p[:, 2] - p[0, 2]], axis=1)
        seg, mid = q[-1], q[1:-1]
        L2 = float(seg[:2] @ seg[:2])          # parameter from the X/A position along the move
        if L2 < 1e-18:
            if abs(seg[2]) < 1e-12:
                return bool((np.einsum("ij,ij->i", mid, mid) <= tol * tol).all())
            t = np.clip(mid[:, 2] / seg[2], 0, 1)
        else:
            t = np.clip(mid[:, :2] @ seg[:2] / L2, 0, 1)
        dev = mid - t[:, None] * seg
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


def rotary_rough(model, tool: Tool, stock_diameter: float, step_down: float,
                 stepover_ratio: float = 0.5, stepover: float | None = None,
                 stock_to_leave: float = 0.5, feed: float = 1500, plunge: float = 300,
                 safe_z: float = 5.0, x_range=None, floor: float = 1.0, resolution: float = 0.5,
                 a_step: float | None = None, tolerance: float = 0.02, max_descent: float = 30.0,
                 name: str = "rotary-rough") -> Toolpath:
    """Z-level roughing from the stock radius inwards: one helical pass per level,
    each removing at most `step_down` and leaving `stock_to_leave` on the model.

    Each level only spans the X range where it still cuts. A alternates direction
    level by level, so the A position stays within about one level's turns.
    """
    if tool.kind not in ("flat", "ball"):
        raise ValueError("rotary roughing needs a flat or ball end mill")
    if step_down <= 0:
        raise ValueError("step_down must be > 0")
    rs = stock_diameter / 2
    tp = Toolpath(name, tool)
    s = _stepover(tool, stepover, stepover_ratio)
    g = _Grid(model, x_range, resolution, a_step, rs, tool.radius + stock_to_leave)
    target = np.maximum(g.surface(tool, stock_to_leave), floor)
    if target.min() >= rs:
        tp.notes.append(f"{name}: nothing to rough, the model fills the {stock_diameter} mm stock")
        return tp
    if target.max() > rs + 1e-6:
        tp.notes.append(f"{name}: the model sticks out of the {stock_diameter} mm stock")
    safe = rs + safe_z
    descent = None if tool.center_cutting else max_descent
    zmin = float(target.min())
    levels = list(np.arange(rs - step_down, zmin, -step_down)) + [zmin]
    prev = np.full_like(target, rs)
    j, a, d = 0, 0.0, 1
    for level in levels:
        cur = np.maximum(level, target)
        active = np.nonzero((cur < prev - 1e-6).any(axis=1))[0]
        if not len(active):
            continue
        i0, i1 = int(active[0]), int(active[-1])
        k = int(math.ceil(tool.radius / g.res))
        # above anything left within reach of the start, then a ramp ring down
        z_entry = min(float(prev[max(0, i0 - k):i0 + k + 1].max()) + 0.2, safe)
        p = _Path(g, _envelope(cur), j, a, descent)
        _start(tp, p, i0, z_entry, d, safe, feed, plunge)
        if i1 > i0:
            revs = math.ceil((g.xc[i1] - g.xc[i0]) / s - 1e-9)
            p.turn(i0, i1, revs, d)
            p.turn(i1, i1, 1, d)
        _emit(tp, p, feed, tolerance)
        tp.rapid(z=safe)
        j, a, d = p.j, p.a, -d
        prev = np.minimum(prev, cur)
    return tp


def rotary_finish(model, tool: Tool, stock_diameter: float, strategy: str = "helical",
                  stepover_ratio: float = 0.15, stepover: float | None = None,
                  stepover_deg: float | None = None, feed: float = 1500, plunge: float = 300,
                  safe_z: float = 5.0, x_range=None, floor: float = 1.0, resolution: float = 0.25,
                  a_step: float | None = None, tolerance: float = 0.005, entry_clearance: float = 1.0,
                  max_descent: float = 30.0, name: str = "rotary-finish") -> Toolpath:
    """Finish the model surface (no stock left).

    strategy: "helical" (continuous spiral, smoothest), "a_raster" (full turns of
    A at fixed X, stepping X by `stepover`) or "x_raster" (sweeps along X at fixed
    A, stepping A by `stepover_deg`, by default `stepover` at the model's largest
    radius).
    """
    if strategy not in ("helical", "a_raster", "x_raster"):
        raise ValueError(f"unknown rotary finishing strategy {strategy!r}")
    rs = stock_diameter / 2
    tp = Toolpath(name, tool)
    s = _stepover(tool, stepover, stepover_ratio)
    g = _Grid(model, x_range, resolution, a_step, rs, tool.radius)
    surf = np.maximum(g.surface(tool), floor)
    if surf.max() > rs + 1e-6:
        tp.notes.append(f"{name}: the model sticks out of the {stock_diameter} mm stock")
    safe = rs + safe_z
    last = len(g.xc) - 1
    p = _Path(g, _envelope(surf), 0, 0.0, None if tool.center_cutting else max_descent)
    _start(tp, p, 0, min(float(surf[0, 0]) + entry_clearance, safe), 1, safe, feed, plunge)
    span = float(g.xc[-1] - g.xc[0])
    if strategy == "helical" and last > 0:
        p.turn(0, last, math.ceil(span / s - 1e-9), 1)
        p.turn(last, last, 1, 1)
    elif strategy == "a_raster" and last > 0:
        n = math.ceil(span / s - 1e-9)
        for k in range(1, n + 1):
            xf0, xf1 = last * (k - 1) / n, last * k / n
            p.line_x(xf0, xf1)
            p.turn(xf1, xf1, 1, 1)
    elif strategy == "x_raster":
        deg = stepover_deg if stepover_deg is not None else math.degrees(s / max(float(surf.max()), s))
        dj = max(1, int(round(deg / g.a_step)))
        lines = math.ceil(g.m / dj)
        xf = 0
        for k in range(lines):
            to = last if xf == 0 else 0
            p.line_x(xf, to)
            xf = to
            if k < lines - 1:
                p.step_a(xf, dj, 1)
    _emit(tp, p, feed, tolerance)
    tp.rapid(z=safe)
    return tp
