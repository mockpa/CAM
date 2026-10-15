"""Minimal SVG importer: path (M L H V C Q Z, absolute and relative), polygon,
polyline, rect, circle. Curves are flattened to polylines. Transforms are not
applied yet; flatten them in your design tool before export.

SVG has Y pointing down; we flip so the design is upright on the machine.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from shapely.geometry import Polygon
from shapely.ops import unary_union

_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[MmLlHhVvCcQqZz]|{_NUM}")


def _bezier(p0, p1, p2, p3, n):
    return [((1-t)**3*p0[0] + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t**2*p2[0] + t**3*p3[0],
             (1-t)**3*p0[1] + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t**2*p2[1] + t**3*p3[1])
            for t in (i / n for i in range(1, n + 1))]


def parse_path(d: str, segs: int = 16) -> list[list[tuple[float, float]]]:
    toks = _TOKEN.findall(d)
    i, cmd = 0, None
    cur = (0.0, 0.0)
    start = cur
    subpaths, pts = [], []

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    while i < len(toks):
        if re.fullmatch("[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "M":
            if len(pts) > 1:
                subpaths.append(pts)
            cur = (ox + num(), oy + num()); start = cur; pts = [cur]
            cmd = "l" if rel else "L"
        elif c == "L":
            cur = (ox + num(), oy + num()); pts.append(cur)
        elif c == "H":
            cur = ((cur[0] if rel else 0) + num(), cur[1]); pts.append(cur)
        elif c == "V":
            cur = (cur[0], (cur[1] if rel else 0) + num()); pts.append(cur)
        elif c == "C":
            p1 = (ox + num(), oy + num()); p2 = (ox + num(), oy + num()); p3 = (ox + num(), oy + num())
            pts += _bezier(cur, p1, p2, p3, segs); cur = p3
        elif c == "Q":
            q = (ox + num(), oy + num()); p3 = (ox + num(), oy + num())
            p1 = (cur[0] + 2/3*(q[0]-cur[0]), cur[1] + 2/3*(q[1]-cur[1]))
            p2 = (p3[0] + 2/3*(q[0]-p3[0]), p3[1] + 2/3*(q[1]-p3[1]))
            pts += _bezier(cur, p1, p2, p3, segs); cur = p3
        elif c == "Z":
            if len(pts) > 1:
                subpaths.append(pts)
            cur = start; pts = [cur]
        else:
            raise ValueError(f"unsupported SVG path command {cmd}")
    if len(pts) > 1:
        subpaths.append(pts)
    return subpaths


def load_svg(path: str, scale: float = 1.0):
    """Return a shapely (Multi)Polygon. Nested subpaths become holes (even-odd)."""
    tree = ET.parse(path)
    rings = []
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "path":
            rings += parse_path(el.get("d", ""))
        elif tag in ("polygon", "polyline"):
            nums = [float(v) for v in re.findall(_NUM, el.get("points", ""))]
            rings.append(list(zip(nums[0::2], nums[1::2])))
        elif tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = float(el.get("width")), float(el.get("height"))
            rings.append([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        elif tag == "circle":
            cx, cy, r = (float(el.get(k, 0)) for k in ("cx", "cy", "r"))
            n = max(24, int(2 * math.pi * r / 0.5))
            rings.append([(cx + r*math.cos(2*math.pi*k/n), cy + r*math.sin(2*math.pi*k/n)) for k in range(n)])
    geom = Polygon()
    for ring in rings:
        if len(ring) < 3:
            continue
        p = Polygon([(x * scale, -y * scale) for x, y in ring]).buffer(0)
        geom = geom.symmetric_difference(p)   # even-odd fill: letters with holes work
    return unary_union(geom)
