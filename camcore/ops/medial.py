"""Medial axis of a polygon, for medial-axis V-carving.

The medial axis is where the largest inscribed circles have their centres; a
V-bit whose tip runs along it at depth r / tan(angle/2) (r = distance to the
boundary, the clearance) touches the walls on both sides and cuts the whole
V-groove in one pass, with sharp corners.

Method: Voronoi diagram (scipy / qhull) of the boundary sampled every
`spacing` mm. Its ridges inside the polygon between samples that are not
neighbours on the same ring approximate the medial axis. Each ridge is kept
only if dropping it would leave more than `tolerance` of material at the wall:
at a ridge point with clearance r whose two boundary points subtend the angle
phi, the circle centre sits r / cos(phi/2) - r away from the wall it would
otherwise reach. That prunes the tiny branches every kink of a flattened curve
has, and shortens corner branches only where r is very small.

Chains between junctions and ends are then decimated with exact clearances
(distance to the boundary segments, not to the samples): a dropped point may
leave at most `tolerance` of material, and no point of a kept segment may sit
deeper than its exact clearance allows (no gouge into the walls).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

EPS = 0.001          # mm: how far a path point's radius may exceed its exact clearance


@dataclass
class Chain:
    """Polyline along the medial axis. pts: (n, 2) XY; r: (n,) clearance at
    each point; start/end: node keys (equal for a closed loop, None ends a
    single point)."""
    pts: np.ndarray
    r: np.ndarray
    start: object
    end: object


class Clearance:
    """Exact distance from points to a polygon's boundary segments."""

    def __init__(self, poly: Polygon):
        segs = []
        for ring in [poly.exterior, *poly.interiors]:
            c = np.asarray(ring.coords)
            segs.append(np.stack([c[:-1], c[1:]], axis=1))
        self.segs = np.concatenate(segs)
        self.tree = shapely.STRtree(shapely.linestrings(self.segs))

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if not len(xy):
            return np.zeros(0)
        idx = self.tree.query_nearest(shapely.points(xy), all_matches=False)[1]
        a, b = self.segs[idx, 0], self.segs[idx, 1]
        ab = b - a
        t = np.clip(np.einsum("ij,ij->i", xy - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300), 0, 1)
        return np.hypot(*(a + ab * t[:, None] - xy).T)


def _sample(poly: Polygon, spacing: float):
    """Boundary points every <= spacing mm, with their ring number and index on it."""
    pts, ring_id, index, sizes = [], [], [], []
    for k, ring in enumerate([poly.exterior, *poly.interiors]):
        c = np.asarray(ring.coords)[:, :2]
        out = []
        for a, b in zip(c[:-1], c[1:]):
            n = max(1, math.ceil(math.dist(a, b) / spacing))
            t = np.arange(n)[:, None] / n
            out.append(a + (b - a) * t)
        out = np.concatenate(out)
        pts.append(out)
        ring_id.append(np.full(len(out), k))
        index.append(np.arange(len(out)))
        sizes.append(len(out))
    return np.concatenate(pts), np.concatenate(ring_id), np.concatenate(index), np.array(sizes)


def medial_axis(poly: Polygon, tolerance: float = 0.01, spacing: float | None = None) -> list[Chain]:
    """Decimated medial-axis chains of one polygon (holes allowed)."""
    from scipy.spatial import Voronoi

    if spacing is None:
        spacing = 4 * tolerance
    pts, ring_id, index, sizes = _sample(poly, spacing)
    pts, first = np.unique(pts, axis=0, return_index=True)        # touching rings share points
    ring_id, index = ring_id[first], index[first]
    vor = Voronoi(pts)
    V = vor.vertices
    inside = shapely.contains_xy(poly, V[:, 0], V[:, 1])
    clear = Clearance(poly)

    rp = vor.ridge_points
    rv = np.array(vor.ridge_vertices, dtype=object)
    va = np.array([v[0] for v in rv])
    vb = np.array([v[1] for v in rv])
    ok = (va >= 0) & (vb >= 0)
    ok[ok] &= inside[va[ok]] & inside[vb[ok]]
    gi, gj = rp[:, 0], rp[:, 1]
    m = sizes[ring_id[gi]]
    gap = np.abs(index[gi] - index[gj])
    neighbours = (ring_id[gi] == ring_id[gj]) & ((gap == 1) | (gap == m - 1))
    ok &= ~neighbours
    keep = np.zeros(len(rp), bool)
    for ends in (va, vb):
        e = np.where(ok, ends, 0)
        a, b = pts[gi] - V[e], pts[gj] - V[e]
        la, lb = np.hypot(*a.T), np.hypot(*b.T)
        cos_phi = np.einsum("ij,ij->i", a, b) / np.maximum(la * lb, 1e-300)
        half = np.sqrt(np.clip((1 + cos_phi) / 2, 0, 1))          # cos(phi / 2)
        r = (la + lb) / 2
        keep |= ok & (r * (1 - half) >= tolerance * half)
    edges = [(int(va[k]), int(vb[k])) for k in np.flatnonzero(keep) if va[k] != vb[k]]

    if not edges:
        # no axis worth a line (a round dot): the deepest inscribed circle alone
        cand = np.flatnonzero(inside)
        if not len(cand):
            return []
        c = clear(V[cand])
        k = cand[int(np.argmax(c))]
        return [Chain(V[k:k + 1].copy(), np.array([c.max()]), k, None)]

    used = sorted({v for e in edges for v in e})
    r_all = dict(zip(used, clear(V[used])))
    chains = []
    for path in _chains(edges):
        xy = V[path]
        r = np.array([r_all[v] for v in path])
        xy, r = _refine(xy, r, clear, spacing)
        keep_idx = _decimate(xy, r, clear, tolerance, spacing)
        chains.append(Chain(xy[keep_idx], r[keep_idx], path[0], path[-1]))
    return chains


def _chains(edges):
    """Split a graph into vertex paths between nodes of degree != 2 (loops: start == end)."""
    adj: dict[int, list[tuple[int, int]]] = {}
    for k, (a, b) in enumerate(edges):
        adj.setdefault(a, []).append((b, k))
        adj.setdefault(b, []).append((a, k))
    seen = set()
    out = []

    def walk(start, nxt, k):
        path = [start]
        cur = nxt
        while True:
            seen.add(k)
            path.append(cur)
            if len(adj[cur]) != 2 or cur == start:
                return path
            (n1, k1), (n2, k2) = adj[cur]
            nxt_, k = (n2, k2) if k1 == k else (n1, k1)
            if k in seen:
                return path
            cur = nxt_

    for v, nb in adj.items():
        if len(nb) != 2:
            for n, k in nb:
                if k not in seen:
                    out.append(walk(v, n, k))
    for v, nb in adj.items():                    # what is left are closed loops
        for n, k in nb:
            if k not in seen:
                out.append(walk(v, n, k))
    return out


def _segment_samples(a, b, step):
    n = max(1, math.ceil(math.dist(a, b) / step))
    t = np.arange(1, n) / n
    return t, a + (b - a) * t[:, None]


def _refine(xy, r, clear, spacing):
    """Insert exact-clearance points where a straight segment between two chain
    points would run deeper than the clearance between them."""
    out_xy, out_r = [xy[0]], [r[0]]
    for i in range(len(xy) - 1):
        t, p = _segment_samples(xy[i], xy[i + 1], spacing / 4)
        if len(t):
            c = clear(p)
            if np.any(r[i] + (r[i + 1] - r[i]) * t > c + EPS):
                out_xy.extend(p)
                out_r.extend(c)
        out_xy.append(xy[i + 1])
        out_r.append(r[i + 1])
    return np.array(out_xy), np.array(out_r)


def _valid(xy, r, i, j, clear, tolerance, spacing):
    """May the straight segment i..j replace the chain points between them?"""
    a, b = xy[i], xy[j]
    ab = b - a
    L2 = float(ab @ ab)
    mid = xy[i + 1:j]
    if L2 < 1e-18:
        return bool(np.all(r[i + 1:j] <= max(r[i], r[j]) + tolerance))
    t = np.clip((mid - a) @ ab / L2, 0, 1)
    q = a + ab * t[:, None]
    rl = r[i] + (r[j] - r[i]) * t
    off = np.hypot(*(mid - q).T)
    if np.any(r[i + 1:j] + off - rl > tolerance):        # would leave material the point cut
        return False
    ts, ps = _segment_samples(a, b, spacing / 2)
    tt = np.concatenate([t, ts])
    pp = np.concatenate([q, ps])
    return bool(np.all(r[i] + (r[j] - r[i]) * tt <= clear(pp) + EPS))


def _decimate(xy, r, clear, tolerance, spacing):
    n = len(xy)
    keep = [0]
    i = 0
    while i < n - 1:
        step, good = 1, i + 1
        while i + step * 2 <= n - 1 and _valid(xy, r, i, i + step * 2, clear, tolerance, spacing):
            step *= 2
            good = i + step
        lo, hi = good, min(n - 1, i + step * 2)
        while hi - lo > 1:                     # largest valid end between good and the first failure
            mid = (lo + hi) // 2
            if _valid(xy, r, i, mid, clear, tolerance, spacing):
                lo = mid
            else:
                hi = mid
        if hi != lo and hi == n - 1 and _valid(xy, r, i, hi, clear, tolerance, spacing):
            lo = hi
        keep.append(lo)
        i = lo
    return keep
