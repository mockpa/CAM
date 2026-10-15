"""Fit G2/G3 arcs to runs of short line segments.

A run of points P0..Pn (feed moves at one feed rate) is replaced, greedily from
the start, by the longest arcs that stay within `tol` of the original polyline:

- every point lies within tol of the circle,
- every segment's midpoint lies within tol of the circle (catches the bulge
  between points, and rejects corners: three points always fit some circle),
- the points turn monotonically one way, by at most 180 degrees per arc,
- Z changes linearly with the turned angle (helical arcs), within tol.

Arcs need at least MIN_SEGMENTS segments; everything else stays a line.
"""
from __future__ import annotations

import math

import numpy as np

MIN_SEGMENTS = 3
MAX_RADIUS = 1000.0         # mm; flatter than this stays a straight line (keeps I/J well-conditioned)
MAX_SWEEP = math.pi         # radians per arc, keeps the centre unambiguous


def _circle(p, q, r):
    """Centre and radius of the circle through three XY points, or None if collinear."""
    ax, ay = p
    bx, by = q
    cx, cy = r
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def fit(pts: np.ndarray, i: int, j: int, tol: float):
    """Arc through pts[i]..pts[j] (rows x, y, z), or None. Returns (centre, cw)."""
    if j - i < MIN_SEGMENTS:
        return None
    circ = _circle(pts[i, :2], pts[(i + j) // 2, :2], pts[j, :2])
    if circ is None:
        return None
    (cx, cy), r = circ
    if r > MAX_RADIUS:
        return None
    seg = pts[i:j + 1]
    if np.abs(np.hypot(seg[:, 0] - cx, seg[:, 1] - cy) - r).max() > tol:
        return None
    mids = (seg[1:, :2] + seg[:-1, :2]) / 2
    if np.abs(np.hypot(mids[:, 0] - cx, mids[:, 1] - cy) - r).max() > tol:
        return None
    ang = np.unwrap(np.arctan2(seg[:, 1] - cy, seg[:, 0] - cx))
    d = np.diff(ang)
    if not ((d > 1e-12).all() or (d < -1e-12).all()):
        return None
    sweep = ang[-1] - ang[0]
    if abs(sweep) > MAX_SWEEP:
        return None
    z = seg[:, 2]
    z_lin = z[0] + (z[-1] - z[0]) * (ang - ang[0]) / sweep
    if np.abs(z - z_lin).max() > tol:
        return None
    return (cx, cy), sweep < 0


def fit_run(points, tol: float):
    """Split a polyline (list of (x, y, z), the first being the current position)
    into ("line", j) and ("arc", i, j, centre, cw) pieces; i, j index into points."""
    pts = np.asarray(points, dtype=float)
    out, i, n = [], 0, len(pts) - 1
    while i < n:
        # grow the arc exponentially, then binary-search the longest end that still fits
        best, j, step = None, i + MIN_SEGMENTS, MIN_SEGMENTS
        while j <= n:
            f = fit(pts, i, j, tol)
            if f is None:
                break
            best = (j, f)
            step *= 2
            j = i + step
        if best is not None:
            lo, hi = best[0] + 1, min(j, n + 1) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                f = fit(pts, i, mid, tol)
                if f is None:
                    hi = mid - 1
                else:
                    best, lo = (mid, f), mid + 1
            j, (centre, cw) = best
            out.append(("arc", i, j, centre, cw))
            i = j
        else:
            out.append(("line", i + 1))
            i += 1
    return out
