"""Adaptive (constant engagement) pocket clearing.

Contour-parallel clearing cuts a full-width slot on its first loop and much
wider than its stepover in every corner. Here the tool instead steers through
a model of the material still left (a grid with cells of about D/50): every
step of R/6 goes in the direction whose new cut is `width` mm wide (area
removed per mm of travel), turning towards the material when it cuts too
little and away when it cuts too much. With M3 climb milling the material
stays on the right of travel. A pass ends where the material around the tool
runs out, or where it would have to cut more than OVERLOAD x width; the next
pass starts at the nearest place where the tool stands in cleared air just
beside the material, reached at depth through cleared air or by lifting.

Each part of the tool-centre region is entered by a helix at its widest
point. The plan is made once in XY and replayed on every Z level (each level
starts from the same material); then a wall pass along the tool-centre
boundary, ramped in along itself, takes off the thin rest at the walls. Parts
too narrow for a helix are cleared with contour-parallel loops instead (noted
on the toolpath).
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset, polygons, rings
from .entry import CLEARANCE, HELIX_MIN_RATIO, approach, helix_down, ramp_ring

TURN_STEP = math.radians(10)       # search step when looking for the right direction
MAX_TURN = math.radians(150)       # furthest a step may turn from the previous direction
OVERLOAD = 1.5                     # a pass ends rather than cut wider than this x width
AIR_END = 0.05                     # cutting less than this x width counts as air
SHIFTS = 4                         # sub-cell positions of the precomputed tool discs
MAX_PASSES = 20000
AIR_MARGIN = 3                     # cells: entries and links keep this much more than R from material


class Material:
    """Uncut material on a grid. The tool disc is looked up at 1/SHIFTS-cell positions."""

    def __init__(self, removable: BaseGeometry, centres: BaseGeometry, radius: float, res: float):
        self.res = res
        x0, y0, x1, y1 = removable.bounds
        pad = radius + 3 * res
        self.x0, self.y0 = x0 - pad, y0 - pad
        nx = int(math.ceil((x1 - x0 + 2 * pad) / res)) + 1
        ny = int(math.ceil((y1 - y0 + 2 * pad) / res)) + 1
        self.X, self.Y = np.meshgrid(self.x0 + np.arange(nx) * res, self.y0 + np.arange(ny) * res, indexing="ij")
        self.mat = shapely.contains_xy(removable, self.X, self.Y)
        self.centre_ok = shapely.contains_xy(centres, self.X, self.Y)
        k = int(math.ceil(radius / res)) + 1
        di, dj = np.meshgrid(np.arange(-k, k + 1), np.arange(-k, k + 1), indexing="ij")
        self.dirty = None                  # cell box changed since the distance field was made
        self.discs = {}
        for a in range(SHIFTS):
            for b in range(SHIFTS):
                m = np.hypot((di - a / SHIFTS) * res, (dj - b / SHIFTS) * res) <= radius
                self.discs[a, b] = (di[m], dj[m])

    def _cells(self, p):
        i, a = divmod(round((p[0] - self.x0) / self.res * SHIFTS), SHIFTS)
        j, b = divmod(round((p[1] - self.y0) / self.res * SHIFTS), SHIFTS)
        di, dj = self.discs[a, b]
        return i + di, j + dj

    def area(self, p) -> float:
        """Material inside the tool disc at p (mm^2)."""
        return float(self.mat[self._cells(p)].sum()) * self.res ** 2

    def clear(self, p):
        i, j = self._cells(p)
        self.mat[i, j] = False
        box = (int(i.min()), int(i.max()), int(j.min()), int(j.max()))
        d = self.dirty
        self.dirty = box if d is None else (min(d[0], box[0]), max(d[1], box[1]), min(d[2], box[2]), max(d[3], box[3]))

    def cell(self, p):
        return round((p[0] - self.x0) / self.res), round((p[1] - self.y0) / self.res)

    def air_distance(self, dist, cap):
        """Distance from each cell to the nearest material, exact up to `cap`
        (larger values are just > cap). Recomputes only around what changed."""
        from scipy.ndimage import distance_transform_edt

        if dist is None:
            self.dirty = None
            return np.minimum(distance_transform_edt(~self.mat) * self.res, cap + self.res)
        if self.dirty is None:
            return dist
        i0, i1, j0, j1 = self.dirty
        self.dirty = None
        k = int(math.ceil(cap / self.res)) + 2
        n, m = self.mat.shape
        wi0, wi1, wj0, wj1 = max(0, i0 - 2 * k), min(n, i1 + 2 * k + 1), max(0, j0 - 2 * k), min(m, j1 + 2 * k + 1)
        win = distance_transform_edt(~self.mat[wi0:wi1, wj0:wj1]) * self.res
        oi0, oi1, oj0, oj1 = max(0, i0 - k), min(n, i1 + k + 1), max(0, j0 - k), min(m, j1 + k + 1)
        dist[oi0:oi1, oj0:oj1] = np.minimum(win[oi0 - wi0:oi1 - wi0, oj0 - wj0:oj1 - wj0], cap + self.res)
        return dist

    def centre_allowed(self, p) -> bool:
        i, j = self.cell(p)
        return 0 <= i < self.mat.shape[0] and 0 <= j < self.mat.shape[1] and bool(self.centre_ok[i, j])


class Planner:
    def __init__(self, shape: BaseGeometry, tool: Tool, width: float, climb: bool, res: float | None):
        self.R = R = tool.radius
        self.res = res if res is not None else min(0.2, max(0.02, tool.diameter / 50))
        self.allowed = offset(shape, -R + 1e-6)
        removable = shape.intersection(self.allowed.buffer(R, quad_segs=16))
        self.mat = Material(removable, self.allowed.buffer(-2 * self.res), R, self.res)
        self.width, self.step = width, R / 6
        self.sgn = -1.0 if climb else 1.0              # turning this way meets more material
        self.climb = climb

    def _try(self, p, t):
        q = (p[0] + self.step * math.cos(t), p[1] + self.step * math.sin(t))
        return t, (self.mat.area(q) if self.mat.centre_allowed(q) else None), q

    def solve(self, p, theta, target):
        """(direction, area, point) of the next step: the direction whose cut is
        `target` mm^2, or the closest inside the region; None if every try leaves it."""
        first = self._try(p, theta)
        tried = [first]
        over = first[1] is None or first[1] > target
        turn = -self.sgn if over else self.sgn         # away from the material, or towards it
        prev = first
        bracket = None
        t = theta
        while abs(t - theta) < MAX_TURN:
            t += turn * TURN_STEP
            cur = self._try(p, t)
            tried.append(cur)
            if over and cur[1] is not None and cur[1] <= target:
                bracket = (prev, cur)
                break
            if not over and (cur[1] is None or cur[1] >= target):
                bracket = (prev, cur)
                break
            prev = cur
        inside = [c for c in tried if c[1] is not None]
        if bracket is None:
            if not inside:
                return None
            return (min if over else max)(inside, key=lambda c: c[1])
        bad, good = (bracket[0], bracket[1]) if over else (bracket[1], bracket[0])
        for _ in range(6):                             # bisect: `good` is inside and at most target
            mid = self._try(p, (bad[0] + good[0]) / 2)
            if mid[1] is not None and mid[1] <= target:
                good = mid
            else:
                bad = mid
        return good

    def run_pass(self, start, theta):
        target = self.width * self.step
        p, pts, last_cut, air = start, [start], 0, 0.0
        while True:
            s = self.solve(p, theta, target)
            if s is None or s[1] > OVERLOAD * target:
                break
            t, a, q = s
            if a < AIR_END * target:
                air += self.step
                if air > 2 * self.R:
                    break
            else:
                air, last_cut = 0.0, len(pts)
            self.mat.clear(q)
            pts.append(q)
            p, theta = q, t
        return pts[:last_cut + 1]

    def linkable(self, a, b, dist) -> bool:
        """May the tool feed straight from a to b at depth through cleared air?"""
        n = max(2, int(math.dist(a, b) / self.res) + 1)
        for k in range(n + 1):
            i, j = self.mat.cell((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
            if not self.mat.centre_ok[i, j] or dist[i, j] < self.R + AIR_MARGIN * self.res:
                return False
        return True

    def bore(self, steps, c, r):
        steps.append(("bore", (float(c[0]), float(c[1])), r))
        for k in range(73):                            # the helix's flat circle clears its disc
            a = 2 * math.pi * k / 72
            self.mat.clear((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
        return (c[0] + r, c[1]), (math.pi / 2 if self.climb else -math.pi / 2)

    def plan(self, helix_diameter: float | None):
        from scipy.ndimage import distance_transform_edt

        R, res, mat = self.R, self.res, self.mat
        want = (helix_diameter if helix_diameter else 0.8 * 2 * R) / 2
        steps, fallback = [], []
        for comp in polygons(self.allowed):
            c = polylabel(comp, tolerance=0.01)
            r = min(want, comp.boundary.distance(c) - 2 * res)
            if r < HELIX_MIN_RATIO * R:
                fallback.append(comp)
                continue
            in_comp = shapely.contains_xy(comp.buffer(-2 * res), mat.X, mat.Y)
            room = distance_transform_edt(in_comp) * res     # how far each cell is from the part's edge
            pos, theta = self.bore(steps, (c.x, c.y), r)
            bad = np.zeros_like(mat.mat)
            air = R + AIR_MARGIN * res
            band = air + max(self.width / 2, 2 * res)
            dist = None
            for _ in range(MAX_PASSES):
                pts = self.run_pass(pos, theta)
                if len(pts) > 1:
                    steps.append(("cut", pts))
                    pos = pts[-1]
                else:                                  # nothing to cut from here: don't start here again
                    i, j = mat.cell(pos)
                    k = int(R / res)
                    bad[max(0, i - k):i + k + 1, max(0, j - k):j + k + 1] = True
                # the grid only knows cells, and the cut lines get simplified: start a margin off
                dist = mat.air_distance(dist, band)
                cand = in_comp & mat.centre_ok & ~bad & (dist >= air) & (dist <= band)
                if not cand.any():
                    # material the passes cannot reach without overload (behind a narrow
                    # neck): bore again at its deepest point if a helix fits there
                    deep = np.where(in_comp & mat.centre_ok, distance_transform_edt(mat.mat) * res, 0.0)
                    deep = np.where(room >= HELIX_MIN_RATIO * R + res, deep, 0.0)
                    i, j = np.unravel_index(int(np.argmax(deep)), deep.shape)
                    if deep[i, j] <= R + 2 * res:
                        break
                    r = min(want, room[i, j] - res)
                    pos, theta = self.bore(steps, (float(mat.X[i, j]), float(mat.Y[i, j])), r)
                    continue
                idx = np.argwhere(cand)
                xy = np.stack([mat.X[cand], mat.Y[cand]], axis=1)
                k = int(np.argmin(np.hypot(*(xy - pos).T)))
                i, j = idx[k]
                s = (float(xy[k, 0]), float(xy[k, 1]))
                gx = dist[min(i + 1, dist.shape[0] - 1), j] - dist[max(i - 1, 0), j]
                gy = dist[i, min(j + 1, dist.shape[1] - 1)] - dist[i, max(j - 1, 0)]
                theta = math.atan2(gy, gx) + self.sgn * math.pi / 2   # material on the climb side
                steps.append(("link", [pos, s]) if self.linkable(pos, s, dist) else ("jump", s))
                pos = s
            else:
                raise RuntimeError("adaptive clearing did not finish")
        for k, st in enumerate(steps):
            if st[0] == "cut":
                line = shapely.LineString(st[1]).simplify(min(0.01, res / 4), preserve_topology=False)
                steps[k] = ("cut", [(float(x), float(y)) for x, y in line.coords])
        return steps, fallback


def plan(shape: BaseGeometry, tool: Tool, width: float, climb: bool = True,
         helix_diameter: float | None = None, res: float | None = None):
    """XY plan for adaptive clearing: (steps, fallback parts, tool-centre region).
    steps: ("bore", centre, r) | ("cut", pts) | ("link", pts) | ("jump", pt)."""
    p = Planner(shape, tool, width, climb, res)
    steps, fallback = p.plan(helix_diameter)
    return steps, fallback, p.allowed


def replay(tp: Toolpath, steps, allowed, levels, top_z: float, safe_z: float, feed: float,
           plunge: float, climb: bool, ramp_angle: float):
    """Cut the XY plan on every Z level, each followed by the wall pass.

    Moves to a new start hop just above the previous level's floor when the
    straight line stays inside the tool-centre region (everything there is cut
    down to that floor); otherwise they retract to safe_z."""
    inside = allowed.buffer(1e-6)
    z_prev = top_z

    def go(here, x, y, z_from, z_air):
        """To (x, y), down to z_air: the floor there is cut to z_air already, and
        everything above it inside the region down to z_from (the last level)."""
        if here is not None and z_from + CLEARANCE < safe_z and \
                shapely.LineString([here, (x, y)]).within(inside):
            tp.rapid(z=z_from + CLEARANCE)
            tp.rapid(x=x, y=y)
        else:
            tp.rapid(z=safe_z)
            tp.rapid(x=x, y=y)
            if top_z + CLEARANCE < safe_z:
                tp.rapid(z=top_z + CLEARANCE)
        if z_air + CLEARANCE < z_from:
            tp.rapid(z=z_air + CLEARANCE)
        tp.feed(z=z_air, f=plunge)

    for z in levels:
        here = None
        for st in steps:
            if st[0] == "bore":
                (cx, cy), r = st[1], st[2]
                go(here, cx + r, cy, z_prev, z_prev)
                helix_down(tp, (cx, cy), r, z_prev, z, ramp_angle, feed, climb)
                here = (cx + r, cy)
            elif st[0] == "jump":                      # into air this level has already cleared
                go(here, st[1][0], st[1][1], z_prev, z)
                here = st[1]
            else:
                for x, y in st[1][1:]:
                    tp.feed(x=x, y=y, f=feed)
                here = st[1][-1]
        for p in polygons(allowed):                    # the walls: ramp in from the last level's floor
            for ring in rings(p, climb, wall_outside=True):
                approach(tp, ring[0][0], ring[0][1], z_prev, top_z, safe_z, plunge)
                ramp_ring(tp, ring, z_prev, z, ramp_angle, feed)
        tp.rapid(z=safe_z)
        z_prev = z
