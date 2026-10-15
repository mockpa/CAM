"""Cylindrical heightmap of a mesh for continuous rotary machining.

The rotary axis is X, Z0 is on its centreline, and the model frame is the
machine frame at A = 0. A turns about X with the ISO / RS274 sign: +A moves
the tool positively (right hand about +X) relative to the part, so the part
itself turns by -A. At machine angle A the tool, which points down -Z, looks
at the model along the direction

    u(A) = (0, -sin A, cos A)

(the viewer and verify's cylinder stock use the same mapping: stock cell A is
on top at machine angle A).

`radial_map` gives r(x, A): the outermost radius of the mesh along u(A) in
the plane at x (0 where the ray from the axis misses the model). `tool_map`
dilates it by the cutter shape: r_tool(x, A) is the lowest Z the tool tip can
reach at (x, A) without gouging the model, treating everything between the
axis and r(x, A) along each ray as material.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .tools import Tool


@dataclass
class CylMap:
    xs: np.ndarray          # (n,) X positions in mm, evenly spaced
    r: np.ndarray           # (n, m) radius per X and angle, mm
    hit: np.ndarray | None = None   # (n, m) True where the ray hit the model

    @property
    def a_step(self) -> float:
        return 360.0 / self.r.shape[1]

    @property
    def angles(self) -> np.ndarray:
        return np.arange(self.r.shape[1]) * self.a_step

    @property
    def x_step(self) -> float:
        return float(self.xs[1] - self.xs[0]) if len(self.xs) > 1 else 0.0


def grid_x(x0: float, x1: float, step: float) -> np.ndarray:
    """X samples from x0 in `step` increments, the last one at or just past x1."""
    if step <= 0 or x1 < x0:
        raise ValueError("need step > 0 and x1 >= x0")
    n = int(math.ceil((x1 - x0) / step - 1e-9)) + 1
    return x0 + np.arange(n) * step


def directions(m: int) -> np.ndarray:
    """(m, 3) unit vectors u(A) for A = 0, 360/m, ... (see module docstring)."""
    a = np.radians(np.arange(m) * 360.0 / m)
    return np.stack([np.zeros(m), -np.sin(a), np.cos(a)], axis=1)


def radial_map(mesh, x_step: float = 0.5, a_step: float = 1.0,
               x_range: tuple[float, float] | None = None, chunk: int = 4096) -> CylMap:
    """r(x, A) by casting rays from the axis outwards and keeping the farthest hit.

    `a_step` is rounded so that a whole number of steps fills 360 degrees.
    """
    m = max(4, int(round(360.0 / a_step)))
    x0, x1 = x_range if x_range is not None else (float(mesh.bounds[0][0]), float(mesh.bounds[1][0]))
    xs = grid_x(x0, x1, x_step)
    tri = np.asarray(mesh.triangles, dtype=float)          # (T, 3, 3)
    tx0, tx1 = tri[:, :, 0].min(axis=1), tri[:, :, 0].max(axis=1)
    d = directions(m)
    r = np.zeros((len(xs), m))
    hit = np.zeros((len(xs), m), dtype=bool)
    eps = 1e-9
    for i, x in enumerate(xs):
        idx = np.nonzero((tx0 <= x + eps) & (tx1 >= x - eps))[0]
        for c in range(0, len(idx), chunk):
            t = _cast(tri[idx[c:c + chunk]], np.array([x, 0.0, 0.0]), d)
            best = np.nanmax(np.where(np.isnan(t), -np.inf, t), axis=0) if len(t) else np.full(m, -np.inf)
            got = np.isfinite(best)
            r[i, got] = np.maximum(r[i, got], best[got])
            hit[i] |= got
    return CylMap(xs, r, hit)


def _cast(tri: np.ndarray, origin: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Moller-Trumbore for every (triangle, ray) pair from one origin: (T, m) of t, NaN = miss."""
    v0, e1, e2 = tri[:, 0], tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    p = np.cross(d[None, :, :], e2[:, None, :])                     # (T, m, 3)
    det = np.einsum("tk,tmk->tm", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin[None, :] - v0                                         # (T, 3)
    u = np.einsum("tk,tmk->tm", s, p) * inv
    q = np.cross(s, e1)                                              # (T, 3)
    v = np.einsum("mk,tk->tm", d, q) * inv
    t = np.einsum("tk,tk->t", e2, q)[:, None] * inv
    tol = 1e-9
    good = ok & (u >= -tol) & (v >= -tol) & (u + v <= 1 + tol) & (t >= -1e-9)
    return np.where(good, np.maximum(t, 0.0), np.nan)


def tool_map(cmap: CylMap, tool: Tool) -> CylMap:
    """r_tool(x, A): lowest tip radius at (x, A) where the tool does not cut into the model.

    The tool axis is radial at the tip angle. A model point on the ray at angle
    phi from the tool axis and distance rho from the rotary axis sits at
    height rho cos(phi) and lateral offset rho sin(phi) in the tool frame. The
    tip must stay above every such point minus the cutter profile h(d) at the
    point's distance d from the tool axis. Along each ray rho runs from 0 to
    r(x, A); the best rho is found in closed form (the bound is concave in rho).
    Points behind the axis (cos(phi) <= 0) never limit the tip, which is never
    below the axis.
    """
    R = tool.radius
    if R <= 0:
        raise ValueError("tool diameter must be positive")
    kind = tool.kind
    if kind not in ("flat", "ball", "vbit"):
        raise ValueError(f"tool kind {kind!r} not supported for rotary")
    kv = 1.0 / math.tan(math.radians(tool.v_angle) / 2.0) if kind == "vbit" else 0.0
    n, m = cmap.r.shape
    dx_step = cmap.x_step
    kmax = int(math.floor(R / dx_step + 1e-9)) if dx_step > 0 else 0
    out = np.zeros((n, m))
    rmax_all = float(cmap.r.max())

    def bound(src, dx, c, s):
        """Highest tip-limiting value over rho in [0, src] for one (dx, phi) offset."""
        rr = math.sqrt(max(R * R - dx * dx, 0.0))
        rho = np.minimum(src, rr / s) if s > 0 else src      # stay inside the footprint
        if kind == "flat":
            return rho * c
        if kind == "ball":
            if s > 0:
                rho = np.minimum(rho, c * rr / s)
            return rho * c - (R - np.sqrt(np.clip(R * R - dx * dx - (rho * s) ** 2, 0.0, None)))
        g = kv * kv * s * s - c * c                          # vbit: h(d) = kv * d
        if g > 0:
            rho = np.minimum(rho, c * abs(dx) / (s * math.sqrt(g)))
        return rho * c - kv * np.sqrt(dx * dx + (rho * s) ** 2)

    # angle offsets by increasing |phi|, so the cut-off below takes effect early
    order = [0] + [q for p in range(1, m // 2 + 1) for q in dict.fromkeys((p, m - p))]
    for q in order:
        phi = math.radians(q * cmap.a_step)
        c, s = math.cos(phi), abs(math.sin(phi))
        if c <= 1e-12:
            continue
        # no point at this angle sits higher than min(r, R / s) cos(phi)
        if min(rmax_all, R / s if s > 1e-12 else math.inf) * c <= out.min():
            continue
        if s <= 1e-12:
            s = 0.0
        src_a = np.roll(cmap.r, -q, axis=1)       # out[:, j] looks at angle j + q
        for k in range(-kmax, kmax + 1):
            lo, hi = max(0, -k), n - max(0, k)
            if lo >= hi:
                continue
            val = bound(src_a[lo + k:hi + k], k * dx_step, c, s)
            np.maximum(out[lo:hi], val, out=out[lo:hi])
    return CylMap(cmap.xs.copy(), out)
