"""Entry moves: how the tool gets down to cutting depth.

ramp   descend along the contour itself, no steeper than ramp_angle (degrees)
helix  (pockets) descend on a small circle inside the pocket, then link to the loop;
       falls back to ramp where the circle or the link does not fit
plunge straight down; only for tools marked center_cutting, and only when the
       operation asks for it with entry="plunge"
"""
from __future__ import annotations

import math

from shapely.geometry import LinearRing, LineString, Point, Polygon
from shapely.ops import polylabel

from ..tools import Tool
from ..toolpath import Toolpath

CLEARANCE = 1.0          # mm above the stock top where rapids stop before an entry
HELIX_SEG_DEG = 10.0     # chord angle when flattening the helix into lines
MAX_RAMP_ANGLE = 30.0    # steeper is a plunge in disguise (verify flags > 45 deg)
HELIX_MIN_RATIO = 0.5    # smallest helix path diameter, as a fraction of the tool diameter


def check_entry(entry: str, tool: Tool, allowed=("ramp", "plunge")):
    if entry not in allowed:
        raise ValueError(f"entry must be one of {', '.join(allowed)}, not {entry!r}")
    if entry == "plunge" and not tool.center_cutting:
        raise ValueError(f"tool {tool.name} is not marked center_cutting, so it can't plunge; "
                         f"use entry 'ramp' or mark the tool \"center_cutting\": true")
    return entry


def approach(tp: Toolpath, x: float, y: float, z_from: float, top_z: float, safe_z: float, plunge: float):
    """Rapid over (x, y), rapid down to just above the stock, feed down to z_from.

    z_from is the floor already cut at (x, y) (top_z for the first level), so the
    feed only travels through air; verify reports it if that is not true.
    """
    tp.rapid(z=safe_z)
    tp.rapid(x=x, y=y)
    if top_z + CLEARANCE < safe_z:
        tp.rapid(z=top_z + CLEARANCE)
    tp.feed(z=z_from, f=plunge)


def rotate_ring(ring, dist: float):
    """The closed ring re-started at the point `dist` mm along it (wrapping)."""
    lr = LinearRing(ring)
    dist %= lr.length
    pts = list(ring)
    acc = 0.0
    for i in range(len(pts) - 1):
        seg = math.dist(pts[i], pts[i + 1])
        if acc + seg >= dist:
            t = (dist - acc) / seg if seg else 0.0
            p = (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
            body = pts[i + 1:-1] + pts[:i + 1]
            out = [p] + [q for q in body if q != p]
            return out + [p]
        acc += seg
    return pts


def ramp_ring(tp: Toolpath, ring, z_from: float, z_to: float, angle: float, feed: float):
    """From ring[0] at z_from: ramp down along the ring to z_to, then cut one full
    loop at z_to. Returns the ring re-started where the ramp ended (the tool is there)."""
    if not 0 < angle <= MAX_RAMP_ANGLE:
        raise ValueError(f"ramp_angle must be in (0, {MAX_RAMP_ANGLE:g}] degrees")
    length = LinearRing(ring).length
    if length <= 1e-9:
        raise ValueError("cannot ramp along a zero-length contour")
    ramp = (z_from - z_to) / math.tan(math.radians(angle)) if z_from > z_to else 0.0
    pts = list(ring)
    walked = 0.0
    while walked < ramp - 1e-9:
        for i in range(len(pts) - 1):
            seg = math.dist(pts[i], pts[i + 1])
            if walked + seg >= ramp - 1e-9:
                t = (ramp - walked) / seg if seg else 0.0
                tp.feed(x=pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                        y=pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t, z=z_to, f=feed)
                walked = ramp
                break
            walked += seg
            tp.feed(x=pts[i + 1][0], y=pts[i + 1][1], z=z_from - (z_from - z_to) * walked / ramp, f=feed)
    loop = rotate_ring(ring, ramp)
    for x, y in loop[1:]:
        tp.feed(x=x, y=y, z=z_to, f=feed)
    return loop


def plan_helix(ring, allowed, tool: Tool, helix_diameter: float | None):
    """Centre and radius of a helix for this pocket loop, or None if none fits.

    The circle must lie inside `allowed` (where the tool centre may go), and so
    must the straight link from the helix to the nearest point of the loop.
    """
    region = Polygon(ring)
    if not region.is_valid or region.area <= 0:
        return None
    c = polylabel(region, tolerance=0.01)
    room = region.exterior.distance(c)      # stay inside this loop, which gets cleared anyway
    want = (helix_diameter if helix_diameter else 0.8 * tool.diameter) / 2
    r = min(want, room - 0.01)
    if r < HELIX_MIN_RATIO * tool.diameter / 2:
        return None
    if not Point(c.x, c.y).buffer(r, quad_segs=32).within(allowed.buffer(1e-6)):
        return None
    start = (c.x + r, c.y)
    lr = LinearRing(ring)
    link_to = lr.interpolate(lr.project(Point(start)))
    if not LineString([start, (link_to.x, link_to.y)]).within(allowed.buffer(1e-6)):
        return None
    return (c.x, c.y), r, lr.project(Point(start))


def helix_down(tp: Toolpath, centre, r: float, z_from: float, z_to: float, angle: float,
               feed: float, climb: bool = True):
    """From (cx + r, cy) at z_from: helix down to z_to in whole turns (so no steeper
    than `angle`), then one flat circle at z_to. Ends back at (cx + r, cy)."""
    cx, cy = centre
    needed = (z_from - z_to) / math.tan(math.radians(angle)) / (2 * math.pi * r)
    turns = max(1, math.ceil(needed - 1e-9))
    per_turn = math.ceil(360 / HELIX_SEG_DEG)
    sign = -1 if climb else 1               # clockwise for climb with a CW spindle
    down = turns * per_turn
    for k in range(1, down + per_turn + 1):
        a = sign * 2 * math.pi * k / per_turn
        z = z_from - (z_from - z_to) * min(k, down) / down
        tp.feed(x=cx + r * math.cos(a), y=cy + r * math.sin(a), z=z, f=feed)
