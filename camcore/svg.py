"""SVG importer: path (M L H V C S Q T A Z, absolute and relative), polygon,
polyline, rect, circle, ellipse; `transform` on elements and nested groups
(matrix, translate, scale, rotate, skewX, skewY).

Units: if the root <svg> has a physical width/height (mm, cm, in, pt, pc) and a
viewBox, user units are converted to mm from them; otherwise one user unit is
one mm. `scale` multiplies on top of that.

Curves are flattened after transforming, with chords within CHORD_TOL of the
true curve, so the post can fit G2/G3 arcs back onto them.

SVG has Y pointing down; we flip so the design is upright on the machine.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from .ops.common import CHORD_TOL
from .shapes import rings_to_geom

_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[MmLlHhVvCcSsQqTtAaZz]|{_NUM}")
_UNITS_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6}
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)          # SVG matrix(a b c d e f)


# --- transforms --------------------------------------------------------------

def mul(m, n):
    """Matrix product m * n: n is applied first, then m."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def apply(m, p):
    a, b, c, d, e, f = m
    return a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f


def parse_transform(s: str | None):
    m = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.findall(_NUM, args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:                       # rotate(a, cx, cy)
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            raise ValueError(f"unsupported SVG transform {name}({args})")
        m = mul(m, t)
    return m


# --- flattening --------------------------------------------------------------

def _cubic(p0, p1, p2, p3):
    """Points (excluding p0) along a cubic Bezier, chords within CHORD_TOL."""
    dd = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
             math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    n = max(1, math.ceil(math.sqrt(6 * dd / (8 * CHORD_TOL))))   # |B''| <= 6 dd, error <= |B''| h^2 / 8
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]))
    return out


def _arc_to_cubics(p0, rx, ry, phi_deg, large, sweep, p1):
    """SVG elliptical arc (endpoint form, SVG spec F.6.5) as cubic Beziers (control points)."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [(p0, p0, p1, p1)]
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1**2 / rx**2 + y1**2 / ry**2
    if lam > 1:                                   # radii too small: scale up (spec F.6.6)
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx**2 * ry**2 - rx**2 * y1**2 - ry**2 * x1**2
    co = math.sqrt(max(0.0, num / (rx**2 * y1**2 + ry**2 * x1**2)))
    if large == sweep:
        co = -co
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2)))
    out, step = [], dt / n
    k = 4 / 3 * math.tan(step / 4)

    def pt(t):
        return (cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp, cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp)

    def deriv(t):
        return (-rx * math.sin(t) * cp - ry * math.cos(t) * sp, -rx * math.sin(t) * sp + ry * math.cos(t) * cp)

    for i in range(n):
        a, b = t1 + i * step, t1 + (i + 1) * step
        pa, pb, da, db = pt(a), pt(b), deriv(a), deriv(b)
        out.append((pa, (pa[0] + k * da[0], pa[1] + k * da[1]), (pb[0] - k * db[0], pb[1] - k * db[1]), pb))
    out[-1] = (*out[-1][:3], p1)
    return out


# --- paths -------------------------------------------------------------------

def parse_path(d: str, m=IDENTITY) -> list[list[tuple[float, float]]]:
    """Subpaths of a path's `d`, transformed by m and flattened."""
    toks = _TOKEN.findall(d)
    i, cmd = 0, None
    cur = start = (0.0, 0.0)
    last_ctrl, last_cmd = None, None
    subpaths, pts = [], []

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    def T(p):
        return apply(m, p)

    def cubic(p1, p2, p3):
        nonlocal cur
        pts.extend(_cubic(T(cur), T(p1), T(p2), T(p3)))   # affine maps keep Beziers Beziers
        cur = p3

    while i < len(toks):
        if re.fullmatch("[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        ctrl = None
        if c == "M":
            if len(pts) > 1:
                subpaths.append(pts)
            cur = (ox + num(), oy + num()); start = cur; pts = [T(cur)]
            cmd = "l" if rel else "L"
        elif c == "L":
            cur = (ox + num(), oy + num()); pts.append(T(cur))
        elif c == "H":
            cur = ((cur[0] if rel else 0) + num(), cur[1]); pts.append(T(cur))
        elif c == "V":
            cur = (cur[0], (cur[1] if rel else 0) + num()); pts.append(T(cur))
        elif c in "CS":
            if c == "C":
                p1 = (ox + num(), oy + num())
            else:                                 # reflect the previous cubic's second control point
                p1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "CS" else cur
            p2 = (ox + num(), oy + num()); p3 = (ox + num(), oy + num())
            cubic(p1, p2, p3)
            ctrl = p2
        elif c in "QT":
            if c == "Q":
                q = (ox + num(), oy + num())
            else:
                q = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "QT" else cur
            p3 = (ox + num(), oy + num())
            p0 = cur
            cubic((p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1])),
                  (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1])), p3)
            ctrl = q
        elif c == "A":
            rx, ry, rot = num(), num(), num()
            large, sweep = num() != 0, num() != 0
            p3 = (ox + num(), oy + num())
            for _, b1, b2, b3 in _arc_to_cubics(cur, rx, ry, rot, large, sweep, p3):
                cubic(b1, b2, b3)
            cur = p3
        elif c == "Z":
            if len(pts) > 1:
                subpaths.append(pts)
            cur = start; pts = [T(cur)]
        else:
            raise ValueError(f"unsupported SVG path command {cmd}")
        last_ctrl, last_cmd = ctrl, c
    if len(pts) > 1:
        subpaths.append(pts)
    return subpaths


def _ellipse(cx, cy, rx, ry, m):
    """Closed ellipse as four transformed quarter Beziers, flattened."""
    k = 4 / 3 * (math.sqrt(2) - 1)
    q = [((cx + rx, cy), (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
         ((cx, cy + ry), (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
         ((cx - rx, cy), (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
         ((cx, cy - ry), (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy))]
    pts = [apply(m, q[0][0])]
    for p0, p1, p2, p3 in q:
        pts += _cubic(*(apply(m, p) for p in (p0, p1, p2, p3)))
    return pts


def _length_mm(s: str | None):
    if not s:
        return None
    mt = re.fullmatch(rf"\s*({_NUM})\s*([a-z]*)\s*", s)
    if not mt or mt.group(2) not in _UNITS_MM:
        return None
    return float(mt.group(1)) * _UNITS_MM[mt.group(2)]


def root_matrix(root, scale: float):
    """User units -> mm (physical width + viewBox), times scale, then flip Y."""
    sx = sy = 1.0
    vb = [float(v) for v in re.findall(_NUM, root.get("viewBox", ""))]
    w, h = _length_mm(root.get("width")), _length_mm(root.get("height"))
    ox = oy = 0.0
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        ox, oy = vb[0], vb[1]
        if w is not None:
            sx = sy = w / vb[2]
        if h is not None:
            sy = h / vb[3]
            if w is None:
                sx = sy
    return (sx * scale, 0.0, 0.0, -sy * scale, -ox * sx * scale, oy * sy * scale)


def load_svg(path: str, scale: float = 1.0):
    """Return a shapely (Multi)Polygon. Nested subpaths become holes (even-odd)."""
    root = ET.parse(path).getroot()
    rings = []

    def walk(el, m):
        m = mul(m, parse_transform(el.get("transform")))
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("defs", "metadata", "clipPath", "mask", "symbol", "style", "title", "desc"):
            return
        if tag == "path":
            rings.extend(parse_path(el.get("d", ""), m))
        elif tag in ("polygon", "polyline"):
            nums = [float(v) for v in re.findall(_NUM, el.get("points", ""))]
            rings.append([apply(m, p) for p in zip(nums[0::2], nums[1::2])])
        elif tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = float(el.get("width")), float(el.get("height"))
            rings.append([apply(m, p) for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))])
        elif tag == "circle":
            cx, cy, r = (float(el.get(k, 0)) for k in ("cx", "cy", "r"))
            rings.append(_ellipse(cx, cy, r, r, m))
        elif tag == "ellipse":
            cx, cy, rx, ry = (float(el.get(k, 0)) for k in ("cx", "cy", "rx", "ry"))
            rings.append(_ellipse(cx, cy, rx, ry, m))
        for child in el:
            walk(child, m)

    walk(root, root_matrix(root, scale))
    return rings_to_geom(rings)
