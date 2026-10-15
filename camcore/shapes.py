"""Shared 2D import helpers: closed rings to polygons, open pieces to closed chains."""
from __future__ import annotations

import math
from collections import defaultdict

from shapely.geometry import Polygon
from shapely.ops import unary_union


def rings_to_geom(rings):
    """Even-odd fill of closed rings (lists of (x, y)): nested rings become holes,
    so letters like O and A come out right."""
    geom = Polygon()
    for ring in rings:
        if len(ring) < 3:
            continue
        p = Polygon(ring).buffer(0)
        geom = geom.symmetric_difference(p)
    return unary_union(geom)


def join_chains(pieces, tol: float):
    """Join open polylines whose ends meet within `tol` into chains.

    Returns (closed, open): closed rings (first point == last) and chains whose
    ends never met. Pieces may need reversing to fit; the first match wins.
    """
    pieces = [list(p) for p in pieces if len(p) >= 2]
    cell = max(tol, 1e-9)
    grid = defaultdict(set)                       # grid cell -> {(piece, end)}

    def key(p):
        return int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))

    for i, p in enumerate(pieces):
        grid[key(p[0])].add((i, 0))
        grid[key(p[-1])].add((i, 1))
    used = [False] * len(pieces)

    def take_near(pt):
        kx, ky = key(pt)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, end in sorted(grid[(kx + dx, ky + dy)]):
                    if not used[i] and math.dist(pt, pieces[i][-1 if end else 0]) <= tol:
                        used[i] = True
                        return pieces[i][::-1] if end else pieces[i]
        return None

    closed, open_ = [], []
    for i, p in enumerate(pieces):
        if used[i]:
            continue
        used[i] = True
        chain = list(p)
        for grow_tail in (True, False):
            while len(chain) < 3 or math.dist(chain[0], chain[-1]) > tol:
                nxt = take_near(chain[-1] if grow_tail else chain[0])   # starts near that end
                if nxt is None:
                    break
                chain = chain + nxt[1:] if grow_tail else nxt[::-1][:-1] + chain
        if len(chain) >= 4 and math.dist(chain[0], chain[-1]) <= tol:
            chain[-1] = chain[0]
            closed.append(chain)
        else:
            open_.append(chain)
    return closed, open_

