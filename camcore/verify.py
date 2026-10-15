"""Virtual dry run: parse LinuxCNC G-code, replay it against a machine model
and a stock model, and report anything that would hurt the real machine.

Checks
- travel limits (machine coordinates = work coordinates + work offset)
- rapid moves (G0) through stock that has not been cut yet  -> crash
- cutting with the spindle stopped                          -> crash
- cutting deeper than the tool's flute length               -> warning
- feeding into material steeper than 45 deg with a tool that
  is not marked center_cutting (a plunge)                   -> error
- tool numbers missing from the machine's tool table, the
  controller's tool.tbl, or without an ATC pocket          -> error
- spindle started with the touch probe loaded               -> error
- cutting without a tool or without G43 length offset       -> error / warning
- A-axis moves on a machine without one, G93 moves without F -> error
- Z moving down at the start or after a tool change before X and Y are set
  (the spindle is wherever the M6 remap left it)            -> error
- machine body (camcore/body.py, from the machine JSON's "body"): spindle,
  collet nut, Z plate and the tool against the fixed obstacles (ATC rack,
  tool setter, table, the rotary unit on rotary jobs), the gantry beam
  against them too, and the spindle and gantry parts against the stock as
  cut so far                                                -> error
  The parts are swept along each move in steps of BODY_STEP; a tool without
  a "gauge_length" gets the body's default_tool_length       -> warning
- G2/G3 arcs (XY plane, I J centre, helical Z allowed) are replayed in short
  chords through all of the above; an end radius that differs from the start
  radius, R-format arcs and G18/G19 planes are errors

Stock removal is simulated on a heightmap (box stock) or a radius map over
(X, A) (cylinder stock), so rapids over already-cut areas are not flagged.
Cylinder cells are rays from the axis, filled with material out to their
radius; the tool body (profile plus shank) meets each ray in one interval, so
contact and cutting are exact at any depth, also beside the tool at radii
below the original surface. A ray the body only crosses keeps its full
length, so the model never holds less material than the real part.
- tool tip below the rotary centreline (Z < 0) on rotary stock  -> error
- bull-nose tools on rotary stock (not modelled there)          -> error

Box stock models flat, ball, bull-nose and V-bit profiles exactly at its cells.

Rotary stock is a cylinder or a rectangular bar along X; the tool may stand at
any Y (indexed 3+1 work), the rays are cut by its body exactly at any offset.

This is an independent model, not LinuxCNC's own interpreter; for that, run
the program through the LinuxCNC sim container in sim/ as well.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .body import Body, hull, overlaps

BODY_STEP = 1.0               # mm between machine body samples along a move
ARC_RADIUS_TOLERANCE = 0.002   # mm; LinuxCNC rejects arcs whose end radius differs more
_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")


@dataclass
class Issue:
    level: str          # error | warning
    line: int
    message: str

    def __str__(self):
        return f"{self.level.upper():7} line {self.line}: {self.message}"


@dataclass
class Motion:
    """One replayed move, also used by the viewer."""
    line: int
    kind: str            # rapid | feed
    start: tuple
    end: tuple           # (x, y, z, a) work coordinates
    tool: int
    spindle: bool
    seconds: float = 0.0   # estimated machine time for this move


@dataclass
class Machine:
    travel: dict = field(default_factory=lambda: {"x": [0, 600], "y": [0, 400], "z": [-120, 0]})
    work_offset: dict = field(default_factory=lambda: {"x": 0, "y": 0, "z": -100})
    has_a: bool = True
    rapid_rate: float = 5000.0                      # mm/min, used for time estimates
    tools: dict = field(default_factory=dict)       # number -> {"diameter", "kind", "v_angle", "flute_length", "center_cutting"}
    atc_pockets: int | None = None                  # tool numbers 1..atc_pockets (plus the probe) are valid
    probe_tool: int | None = None                   # touch probe: may be loaded, never with the spindle on
    table_numbers: set | None = None                # tool numbers in the controller's real tool.tbl
    body: Body | None = None                        # machine body for collision checks

    def offset_for(self, stock_spec: dict | None) -> dict:
        """The work offset a job runs with: the machine's, except that a rotary job
        on a machine with a rotary unit sits where the unit is (see body.py)."""
        if (self.body is not None and self.body.rotary and stock_spec is not None
                and stock_spec["type"] in ("cylinder", "bar") and "chuck_hold" in stock_spec):
            return self.body.rotary_offset(stock_spec)
        return self.work_offset

    @staticmethod
    def load(path) -> "Machine":
        path = Path(path)
        d = json.loads(path.read_text())
        tools = {int(k): v for k, v in d.get("tools", {}).items()}
        table = None
        if d.get("tool_table"):
            table = read_tool_table(path.parent / d["tool_table"])
        return Machine(d.get("travel", Machine().travel), d.get("work_offset", Machine().work_offset),
                       d.get("has_a", True), d.get("rapid_rate", 5000.0), tools,
                       d.get("atc_pockets"), d.get("probe_tool"), table,
                       Body(d["body"]) if d.get("body") else None)


def read_tool_table(path) -> set:
    """Tool numbers in a LinuxCNC tool table (lines like "T3 P3 Z0 D6 ;comment")."""
    nums = set()
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*T(\d+)\b", line.split(";")[0], re.I)
        if m:
            nums.add(int(m.group(1)))
    return nums


class Stock:
    """Box stock: heightmap of top Z over XY. Rotary stock (cylinder, or a
    rectangular bar along X): radius map over (X, A).

    Rotary cell j is the ray from the axis at part angle psi = j * a_res, direction
    (Y, Z) = (-sin psi, cos psi) in the part frame (A = 0). At machine A = a the
    part is turned so that ray psi points along (-sin(psi - a), cos(psi - a)).
    A bar {"x": [..], "y": [y0, y1], "z": [z0, z1]} is its cross-section at A = 0
    and must enclose the axis."""

    def __init__(self, spec: dict, res: float = 0.5):
        self.spec, self.res, self.kind = spec, res, spec["type"]
        self.rotary = self.kind in ("cylinder", "bar")
        if self.kind == "box":
            (self.x0, x1), (self.y0, y1) = spec["x"], spec["y"]
            self.bottom, self.top = spec["z"]
            nx, ny = int(math.ceil((x1 - self.x0) / res)) + 1, int(math.ceil((y1 - self.y0) / res)) + 1
            self.h = np.full((nx, ny), float(self.top))
        elif self.rotary:
            self.x0, x1 = spec["x"]
            if self.kind == "cylinder":
                self.radius = spec["diameter"] / 2
            else:
                (y0, y1), (z0, z1) = spec["y"], spec["z"]
                if not (y0 < 0 < y1 and z0 < 0 < z1):
                    raise ValueError("bar stock must enclose the rotary axis (its y and z ranges must contain 0)")
                self.radius = math.hypot(max(-y0, y1), max(-z0, z1))    # farthest corner
            na = int(math.ceil(360 / math.degrees(res / self.radius)))
            self.a_res = 360 / na                      # cells divide the full turn exactly
            nx = int(math.ceil((x1 - self.x0) / res)) + 1
            self.h = np.full((nx, na), float(self.radius))
            if self.kind == "bar":
                psi = np.radians(np.arange(na) * self.a_res)
                self.h[:] = bar_radii(spec, -np.sin(psi), np.cos(psi))[None, :]
        else:
            raise ValueError(f"unknown stock type {self.kind}")

    def surface(self, y, a) -> float:
        """Top of the uncut stock at machine Y = y with the part at A = a."""
        if self.kind == "box":
            return self.top
        if self.kind == "cylinder":
            return math.sqrt(max(self.radius ** 2 - y * y, 0.0))
        (y0, y1), (z0, z1) = self.spec["y"], self.spec["z"]
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        pts = [(py * ca + pz * sa, -py * sa + pz * ca) for py, pz in ((y0, z0), (y1, z0), (y1, z1), (y0, z1))]
        top = -math.inf
        for (ya, za), (yb, zb) in zip(pts, pts[1:] + pts[:1]):
            if min(ya, yb) - 1e-9 <= y <= max(ya, yb) + 1e-9:
                top = max(top, za if abs(yb - ya) < 1e-12 else za + (zb - za) * (y - ya) / (yb - ya),
                          zb if abs(yb - ya) < 1e-12 else -math.inf)
        return top

    def _cells(self, x, y_or_a, r):
        """Index ranges and centre offsets of grid cells within distance r."""
        i0 = int(math.floor((x - r - self.x0) / self.res)); i1 = int(math.ceil((x + r - self.x0) / self.res))
        i0, i1 = max(i0, 0), min(i1, self.h.shape[0] - 1)
        if i0 > i1:
            return None
        if self.kind == "box":
            j0 = int(math.floor((y_or_a - r - self.y0) / self.res)); j1 = int(math.ceil((y_or_a + r - self.y0) / self.res))
            j0, j1 = max(j0, 0), min(j1, self.h.shape[1] - 1)
            if j0 > j1:
                return None
            xs = self.x0 + np.arange(i0, i1 + 1) * self.res
            ys = self.y0 + np.arange(j0, j1 + 1) * self.res
            d = np.hypot(*np.meshgrid(xs - x, ys - y_or_a, indexing="ij"))
            return (slice(i0, i1 + 1), slice(j0, j1 + 1)), d, None
        raise AssertionError("rotary stock uses _ray_cells")

    def _ray_cells(self, x, a, z, r, y=0.0):
        """Rotary cells whose ray can meet a tool of radius r with its tip at (Y, Z) = (y, z):
        index, dx and the angle phi (rad) of each cell's ray from the vertical."""
        i0 = max(int(math.floor((x - r - self.x0) / self.res)), 0)
        i1 = min(int(math.ceil((x + r - self.x0) / self.res)), self.h.shape[0] - 1)
        if i0 > i1:
            return None
        # the tool body lies above its tip, within y - r .. y + r: rays with
        # -tan(phi) between (y - r) / z and (y + r) / z
        if z > 0:
            lo_deg, hi_deg = -math.degrees(math.atan2(y + r, z)), -math.degrees(math.atan2(y - r, z))
        else:
            lo_deg, hi_deg = -90.0, 90.0
        mid, half = (lo_deg + hi_deg) / 2, (hi_deg - lo_deg) / 2
        span = int(math.ceil(half / self.a_res)) + 1
        jc = int(round(((a + mid) % 360) / self.a_res))
        js = np.arange(jc - span, jc + span + 1) % self.h.shape[1]
        phi = np.radians((js * self.a_res - a + 180) % 360 - 180)
        dx = self.x0 + np.arange(i0, i1 + 1) * self.res - x
        dxg, phig = np.meshgrid(dx, phi, indexing="ij")
        return (slice(i0, i1 + 1), js), dxg, phig

    @staticmethod
    def _ray_interval(dx, phi, z, tool: dict, y=0.0):
        """Where each ray from the rotary axis passes through the tool body.

        In the plane at axial offset dx from the tip, the ray at angle phi from the
        vertical runs through (Y, Z) = rho * (-sin phi, cos phi); the tool axis
        stands at Y = y with the tip at Z = z. The body (cutter profile, shank
        straight up) is convex, so the ray meets it in one interval [lo, hi] of
        rho >= 0; lo > hi where it misses. Every part of the body is cut out of the
        ray exactly: the footprint |Y - y| <= w (w = half chord at dx) and Z >= z
        are linear in rho, the ball's end is a disc of radius w around (y, z + r)
        and the V-bit's cone a quadratic.
        """
        r = tool.get("diameter", 3) / 2
        kind = tool.get("kind")
        sg, c = -np.sin(phi), np.cos(phi)
        w2 = r * r - dx * dx
        w = np.sqrt(np.clip(w2, 0, None))
        eps, inf = 1e-12, np.inf

        def lin(a, b):                     # {rho : a * rho >= b}
            with np.errstate(divide="ignore", invalid="ignore"):
                q = b / np.where(np.abs(a) > eps, a, 1.0)
            flat = np.where(b <= 0, -inf, inf)
            return (np.where(a > eps, q, np.where(a < -eps, -inf, flat)),
                    np.where(a > eps, inf, np.where(a < -eps, q, -flat)))

        def meet(*ivs):
            return np.maximum.reduce([iv[0] for iv in ivs]), np.minimum.reduce([iv[1] for iv in ivs])

        def hull(*ivs):                    # the pieces of a convex set along the ray: their hull
            lo = np.minimum.reduce([np.where(iv[0] <= iv[1], iv[0], inf) for iv in ivs])
            hi = np.maximum.reduce([np.where(iv[0] <= iv[1], iv[1], -inf) for iv in ivs])
            return lo, hi

        ones = np.ones_like(sg * dx)
        base = meet(lin(sg, y - w), lin(-sg, -(y + w)), (0 * ones, inf * ones))
        if kind == "ball":
            upper = meet(base, lin(c, z + r))
            b = sg * y + c * (z + r)
            disc = b * b - (y * y + (z + r) ** 2 - w2)
            sq = np.sqrt(np.clip(disc, 0, None))
            ok = disc >= 0
            lo, hi = hull(upper, meet((np.where(ok, b - sq, inf), np.where(ok, b + sq, -inf)), base))
        elif kind == "vbit":
            kv = 1 / math.tan(math.radians(tool.get("v_angle", 90)) / 2)
            above = meet(base, lin(c, z))
            # cone: (rho c - z)^2 >= kv^2 (dx^2 + (rho sg - y)^2), on the side rho c >= z
            qa = c * c - kv * kv * sg * sg
            qb = -2 * z * c + 2 * kv * kv * sg * y
            qc = z * z - kv * kv * (dx * dx + y * y)
            disc = qb * qb - 4 * qa * qc
            sq = np.sqrt(np.clip(disc, 0, None))
            quad = np.abs(qa) > eps
            with np.errstate(divide="ignore", invalid="ignore"):
                ra, rb = (-qb - sq) / (2 * np.where(quad, qa, 1.0)), (-qb + sq) / (2 * np.where(quad, qa, 1.0))
            r1, r2 = np.minimum(ra, rb), np.maximum(ra, rb)
            lin_lo, lin_hi = lin(qb, -qc)
            real = disc >= 0
            # qa > 0: Q >= 0 outside the roots (everywhere without them); qa < 0: between them
            p1 = (np.where(quad, np.where(qa > 0, -inf, np.where(real, r1, inf)), lin_lo),
                  np.where(quad, np.where(qa > 0, np.where(real, r1, inf), np.where(real, r2, -inf)), lin_hi))
            p2 = (np.where(quad & (qa > 0) & real, r2, inf), np.where(quad & (qa > 0) & real, inf, -inf))
            lo, hi = hull(meet(above, p1), meet(above, p2))
        else:
            lo, hi = meet(base, lin(c, z))
        miss = (w2 < 0) | (c <= eps) | ~(lo <= hi)
        return np.where(miss, inf, np.maximum(lo, 0.0)), np.where(miss, -inf, hi)

    @staticmethod
    def _profile(d, tool: dict):
        """Height of the cutter surface above its tip at radial distance d."""
        r = tool.get("diameter", 3) / 2
        if tool.get("kind") == "vbit":
            return d / math.tan(math.radians(tool.get("v_angle", 90)) / 2)
        if tool.get("kind") == "ball":
            return r - np.sqrt(np.clip(r * r - d * d, 0, None))
        if tool.get("kind") == "bull":
            rc = tool.get("corner_radius", 0.0)
            e = np.clip(d - (r - rc), 0, None)
            return rc - np.sqrt(np.clip(rc * rc - e * e, 0, None))
        return np.zeros_like(d)

    def interference(self, x, y_or_a, z, tool: dict, y: float = 0.0) -> float:
        """How far material pokes into the cutter body at this position (<= 0: clear).
        Box stock: y_or_a is Y. Rotary stock: y_or_a is A and y the tool's Y.

        Rotary stock: each cell holds the material from the axis out to h along
        its ray; the result is how far along a ray it reaches into the body.
        """
        r = tool.get("diameter", 3) / 2
        if self.rotary:
            c = self._ray_cells(x, y_or_a, z, r, y)
            if c is None:
                return -math.inf
            idx, dx, phi = c
            lo, hi = self._ray_interval(dx, phi, z, tool, y)
            hit = lo <= hi
            if not hit.any():
                return -math.inf
            return float((np.minimum(self.h[idx], hi) - lo)[hit].max())
        c = self._cells(x, y_or_a, r)
        if c is None:
            return -math.inf
        idx, d, _ = c
        m = d <= r
        if not m.any():
            return -math.inf
        return float((self.h[idx] - (z + self._profile(d, tool)))[m].max())

    def cut(self, x, y_or_a, z, tool: dict, y: float = 0.0):
        r = tool.get("diameter", 3) / 2
        if self.rotary:
            # a ray whose outer end lies in the body is cut back to where it enters;
            # one the body only crosses keeps its full length (conservative)
            c = self._ray_cells(x, y_or_a, z, r, y)
            if c is None:
                return
            idx, dx, phi = c
            lo, hi = self._ray_interval(dx, phi, z, tool, y)
            sub = self.h[idx]
            self.h[idx] = np.where((lo < sub) & (hi >= sub - 1e-9), lo, sub)
            return
        c = self._cells(x, y_or_a, r)
        if c is None:
            return
        idx, d, _ = c
        sub = self.h[idx]
        self.h[idx] = np.where(d <= r, np.minimum(sub, z + self._profile(d, tool)), sub)


def bar_radii(spec: dict, uy, uz):
    """Distance from the axis to the edge of a bar's cross-section along directions (uy, uz)."""
    (y0, y1), (z0, z1) = spec["y"], spec["z"]
    with np.errstate(divide="ignore"):
        ty = np.where(uy > 1e-12, y1 / uy, np.where(uy < -1e-12, y0 / uy, np.inf))
        tz = np.where(uz > 1e-12, z1 / uz, np.where(uz < -1e-12, z0 / uz, np.inf))
    return np.minimum(ty, tz)


def parse(text: str):
    """Yield (line_no, words dict, raw line) with comments stripped."""
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"\(.*?\)|;.*$", "", raw).strip().upper()
        if not line or line == "%":
            continue
        words = {}
        for letter, val in _WORD.findall(line):
            words.setdefault(letter, []).append(float(val))
        yield n, words, raw


def run(text: str, machine: Machine, stock_spec: dict | None, res: float = 0.5):
    issues: list[Issue] = []
    motions: list[Motion] = []
    stock = Stock(stock_spec, res) if stock_spec else None
    pos = {"x": 0.0, "y": 0.0, "z": 0.0, "a": 0.0}
    tool, prepped, offset_on, spindle, inverse_time = 0, None, False, False, False
    motion_mode = None
    feed = 1000.0
    rotary = stock is not None and stock.rotary
    warned = set()
    wo = machine.offset_for(stock_spec)
    body = machine.body
    if body is not None and rotary and body.rotary and "chuck_hold" not in stock_spec:
        issues.append(Issue("error", 0, "rotary stock has no \"chuck_hold\" (mm held in the jaws): without it the "
                                        "job cannot be placed in the chuck, so the machine body is not checked"))
        body = None
    obstacles = body.obstacles(rotary) if body is not None else []
    known = set()              # axes set since the start or the last tool change
    spindle_parts = {}

    def once(level, n, msg, key):
        if key not in warned:
            warned.add(key)
            issues.append(Issue(level, n, msg))

    def parts_for(n, t):
        """Spindle parts plus the tool, relative to the tip, for tool number t."""
        if t not in spindle_parts:
            tinfo = machine.tools.get(t, {})
            length = tinfo.get("gauge_length")
            if length is None:
                length = body.default_tool_length
                if t:
                    once("warning", n, f"T{t} has no \"gauge_length\" (spindle nose to tip): the machine body "
                         f"check assumes {length:g} mm", ("length", t))
            d = max(tinfo.get("diameter", 3.0), tinfo.get("shank", 0.0))
            spindle_parts[t] = body.spindle_parts(length, d)
        return spindle_parts[t]

    def stock_hit(prt, a):
        """Does a part (in work coordinates) reach into the stock as it is now?"""
        (x0, y0, z0), (x1, y1, z1) = prt.lo, prt.hi
        if prt.kind == "cyl_z":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            flat = {"diameter": x1 - x0, "kind": "flat"}
            return stock.interference(cx, a if rotary else cy, z0, flat, cy) > 0.01
        # the cells strictly inside the box's footprint
        i0 = max(int(math.ceil((x0 + 0.01 - stock.x0) / stock.res)), 0)
        i1 = min(int(math.floor((x1 - 0.01 - stock.x0) / stock.res)), stock.h.shape[0] - 1)
        if i0 > i1:
            return False
        if rotary:        # any material within the box's distance from the axis, at any angle
            dist = math.hypot(max(y0, -y1, 0.0), max(z0, -z1, 0.0))
            return float(stock.h[i0:i1 + 1].max()) > dist + 0.01
        j0 = max(int(math.ceil((y0 + 0.01 - stock.y0) / stock.res)), 0)
        j1 = min(int(math.floor((y1 - 0.01 - stock.y0) / stock.res)), stock.h.shape[1] - 1)
        return j0 <= j1 and float(stock.h[i0:i1 + 1, j0:j1 + 1].max()) > z0 + 0.01

    def body_check(n, pos, new):
        """Machine body along one straight move: obstacles first, then the stock."""
        s0 = np.array([pos[k] + wo[k] for k in "xyz"])
        s1 = np.array([new[k] + wo[k] for k in "xyz"])
        groups = [(parts_for(n, tool), s0, s1)]
        if body.gantry:              # machine X and Z, Y with the tool
            groups.append((body.gantry, s0 * (0, 1, 0), s1 * (0, 1, 0)))
        steps = max(1, math.ceil(float(np.linalg.norm(s1 - s0)) / BODY_STEP))
        t = np.linspace(0.0, 1.0, steps + 1)[:, None]

        def at(f):
            """Where the tool tip is (work coordinates) a fraction f along the move."""
            return " ".join(f"{k.upper()}{pos[k] + (new[k] - pos[k]) * f:.2f}" for k in "xyz")
        for grp, a, b in groups:
            lo, hi = hull(grp)
            for ob in obstacles:
                if not all(min(a[i], b[i]) + lo[i] < ob.hi[i] and ob.lo[i] < max(a[i], b[i]) + hi[i]
                           for i in range(3)):
                    continue
                for prt in grp:
                    if prt.name == "tool" and ob.tool_may_enter or ("body", prt.name, ob.name) in warned:
                        continue
                    hit = overlaps(prt, a + (b - a) * t, ob)
                    if hit.any():
                        once("error", n, f"COLLISION: {prt.name} hits {ob.name} with the tool at work "
                             f"{at(float(t[int(np.argmax(hit)), 0]))}", ("body", prt.name, ob.name))
        if stock is None:
            return
        # the spindle and the gantry against the stock, in work coordinates
        w = np.array([wo["x"], wo["y"], wo["z"]])
        if rotary:
            r = stock.radius
            slo, shi = (stock.x0, -r, -r), (stock.x0 + stock.h.shape[0] * stock.res, r, r)
        else:
            slo = (stock.x0, stock.y0, stock.bottom)
            shi = (stock.x0 + stock.h.shape[0] * stock.res, stock.y0 + stock.h.shape[1] * stock.res, stock.top)
        for grp, a, b in groups:
            grp = [p for p in grp if p.name != "tool"]
            if not grp:
                continue
            a, b = a - w, b - w
            lo, hi = hull(grp)
            if not all(min(a[i], b[i]) + lo[i] < shi[i] and slo[i] < max(a[i], b[i]) + hi[i] for i in range(3)):
                continue
            seg = steps if not rotary else max(steps, math.ceil(abs(new["a"] - pos["a"]) / 360 * 2 * math.pi
                                                                 * stock.radius / BODY_STEP))
            for k in range(seg + 1):
                f = k / seg
                o = a + (b - a) * f
                ang = pos["a"] + (new["a"] - pos["a"]) * f
                for prt in grp:
                    if ("body", prt.name, "stock") not in warned and stock_hit(prt.moved(o), ang):
                        once("error", n, f"COLLISION: {prt.name} hits the stock with the tool at work {at(f)}",
                             ("body", prt.name, "stock"))

    def move(n, kind, pos, new, total, scale=1.0):
        """One straight move (a G0/G1, or one chord of an arc): travel, time, stock checks."""
        for k in ("x", "y", "z"):
            lo, hi = machine.travel[k]
            mval = new[k] + wo[k]
            if not lo - 1e-6 <= mval <= hi + 1e-6:
                once("error", n, f"{k.upper()} {new[k]:.3f} is outside machine travel "
                     f"({lo - wo[k]:.1f} .. {hi - wo[k]:.1f} in work coordinates)",
                     ("travel", k))
        if body is not None and {"x", "y"} <= known:
            body_check(n, pos, new)
        if rotary and min(pos["z"], new["z"]) < 0:
            once("error", n, f"Z {min(pos['z'], new['z']):.3f} is below the rotary centreline (Z0)", "belowaxis")
        dist = math.dist([pos[k] for k in "xyz"], [new[k] for k in "xyz"])
        if new["a"] != pos["a"]:
            r = abs(pos["z"] + new["z"]) / 2 if rotary else 0.0
            dist = math.hypot(dist, math.radians(new["a"] - pos["a"]) * r)
        if kind == "rapid":
            secs = dist / machine.rapid_rate * 60
        elif inverse_time:                       # F = moves per minute for the whole G-code move
            secs = 60.0 / feed * (dist / total if total else 1.0) if feed else 0.0
        else:                                    # scale: true arc length / chord length
            secs = dist * scale / feed * 60 if feed else 0.0
        motions.append(Motion(n, kind, tuple(pos.values()), tuple(new.values()), tool, spindle, secs))
        if stock is not None:
            tinfo = machine.tools.get(tool, {"diameter": 3})
            if rotary and tinfo.get("kind") == "bull":
                once("error", n, f"T{tool} is a bull nose: not modelled on rotary stock, cannot be checked",
                     ("bullrotary", tool))
                return
            retract = all(abs(new[k] - pos[k]) < 1e-9 for k in "xya") and new["z"] >= pos["z"]
            seg = math.dist([pos[k] for k in "xyz"], [new[k] for k in "xyz"])
            if rotary:
                seg = max(seg, abs(new["a"] - pos["a"]) / 360 * 2 * math.pi * max(math.hypot(new["y"], new["z"]), 1))
            steps = max(1, int(seg / (res / 2)))
            horiz = math.hypot(new["x"] - pos["x"], new["y"] - pos["y"])
            if rotary:
                horiz = math.hypot(horiz, math.radians(new["a"] - pos["a"]) * max(math.hypot(new["y"], new["z"]), 1))
            plunging = kind == "feed" and pos["z"] - new["z"] > horiz + 1e-9   # steeper than 45 deg down
            for s in range(1, steps + 1):
                if retract:      # moving straight up out of the cut never hits anything
                    break
                t = s / steps
                p = {k: pos[k] + (new[k] - pos[k]) * t for k in pos}
                u = p["a"] if rotary else p["y"]
                if stock.interference(p["x"], u, p["z"], tinfo, p["y"]) > 0.01:
                    if kind == "rapid":
                        issues.append(Issue("error", n, f"CRASH: rapid move into material at "
                                            f"X{p['x']:.2f} {'A' if rotary else 'Y'}{u:.2f} Z{p['z']:.2f}"))
                        break
                    if tool == 0:
                        once("error", n, "cutting with no tool loaded (missing T M6)", "notool")
                    if not spindle:
                        issues.append(Issue("error", n, "CRASH: cutting with the spindle stopped"))
                        break
                    if plunging and not tinfo.get("center_cutting"):
                        once("error", n, f"T{tool} plunges into material but is not marked center_cutting "
                             f"(ramp or helix in, or mark the tool)", ("plunge", tool))
                    if not offset_on:
                        once("warning", n, f"cutting with T{tool} but no G43 tool length offset", ("g43", tool))
                    fl = tinfo.get("flute_length")
                    surface = stock.surface(p["y"], p["a"])
                    if fl and surface - p["z"] > fl + 1e-6:
                        once("warning", n, f"T{tool} cuts {surface - p['z']:.2f} mm deep, "
                             f"more than its {fl} mm flute length", ("flute", tool))
                    stock.cut(p["x"], u, p["z"], tinfo, p["y"])

    for n, w, raw in parse(text):
        gs = [int(g) if g == int(g) else g for g in w.get("G", [])]
        ms = [int(m) for m in w.get("M", [])]
        if 93 in gs:
            inverse_time = True
        if 94 in gs:
            inverse_time = False
        if 53 in gs:     # machine-coordinate move: only used for retracts to the tool change height
            motion_mode = 0 if 0 in gs else motion_mode
            if "Z" in w:
                pos["z"] = w["Z"][0] - wo["z"]
            continue
        if "T" in w:
            prepped = int(w["T"][0])
            if machine.tools and prepped not in machine.tools:
                issues.append(Issue("error", n, f"T{prepped} is not in the machine's tool table"))
            if machine.table_numbers is not None and prepped not in machine.table_numbers:
                issues.append(Issue("error", n, f"T{prepped} is not in the controller's tool.tbl"))
            if (machine.atc_pockets and prepped != machine.probe_tool
                    and not 1 <= prepped <= machine.atc_pockets):
                issues.append(Issue("error", n, f"T{prepped} has no ATC pocket (pockets 1..{machine.atc_pockets}"
                                    + (f", probe T{machine.probe_tool})" if machine.probe_tool else ")")))
        if 6 in ms:
            tool, offset_on = prepped or 0, False
            known.clear()
        if 43 in gs:
            h = int(w.get("H", [tool])[0])
            if h != tool:
                issues.append(Issue("warning", n, f"G43 H{h} does not match loaded tool T{tool}"))
            offset_on = True
        if 49 in gs:
            offset_on = False
        if 3 in ms or 4 in ms:
            spindle = True
            if machine.probe_tool is not None and tool == machine.probe_tool:
                issues.append(Issue("error", n, f"spindle started with the touch probe T{tool} loaded"))
        if 5 in ms or 2 in ms or 30 in ms:
            spindle = False
        for g in gs:
            if g in (0, 1, 2, 3):
                motion_mode = g
            if g in (18, 19):
                once("error", n, f"G{g}: only XY-plane (G17) arcs are supported", "plane")
        if "F" in w:
            feed = w["F"][0]
        axes = {k.lower(): w[k][0] for k in "XYZA" if k in w}
        if not axes or motion_mode is None:
            continue
        if "a" in axes and not machine.has_a:
            once("error", n, "A-axis move but the machine has no rotary axis", "noa")
        if motion_mode != 0 and inverse_time and "F" not in w:
            issues.append(Issue("error", n, "G93 inverse-time move without F word"))
        new = {**pos, **axes}
        if new["z"] < pos["z"] - 1e-9 and not {"x", "y"} <= known | set(axes):
            once("error", n, "Z moves down before X and Y are set (at the start or after a tool change the "
                 "spindle is wherever the tool change left it)", ("unknownxy", tool))
        known.update(axes)
        if motion_mode in (2, 3):
            arc = arc_points(n, w, pos, new, motion_mode == 2, res, issues)
            if arc is None:
                pos = new
                continue
            pts, arc_len = arc
        else:
            pts = [new]
        kind = "rapid" if motion_mode == 0 else "feed"
        # an arc is replayed as chords; with G93 its one F covers all of them
        total = (sum(math.dist([a[k] for k in "xyz"], [b[k] for k in "xyz"]) for a, b in zip([pos] + pts, pts))
                 if motion_mode in (2, 3) else None)
        scale = arc_len / total if total else 1.0
        for p_end in pts:
            move(n, kind, pos, p_end, total, scale)
            pos = p_end
        pos = new

    return issues, motions, stock


def arc_points(n, w, start, end, cw, res, issues):
    """(points along a G2 (cw) / G3 arc in the XY plane, helical in Z, true path length), or None."""
    if "R" in w:
        issues.append(Issue("error", n, "R-format arcs are not supported (use I J)"))
        return None
    if "A" in w:
        issues.append(Issue("error", n, "arc with an A-axis move is not supported"))
        return None
    if "I" not in w and "J" not in w:
        issues.append(Issue("error", n, "arc without I/J centre"))
        return None
    cx, cy = start["x"] + w.get("I", [0.0])[0], start["y"] + w.get("J", [0.0])[0]
    r0 = math.hypot(start["x"] - cx, start["y"] - cy)
    r1 = math.hypot(end["x"] - cx, end["y"] - cy)
    if r0 < 1e-9:
        issues.append(Issue("error", n, "arc with zero radius"))
        return None
    if abs(r1 - r0) > ARC_RADIUS_TOLERANCE:
        issues.append(Issue("error", n, f"arc end radius {r1:.4f} differs from start radius {r0:.4f}"))
        return None
    a0 = math.atan2(start["y"] - cy, start["x"] - cx)
    a1 = math.atan2(end["y"] - cy, end["x"] - cx)
    sweep = (a0 - a1) % (2 * math.pi) if cw else (a1 - a0) % (2 * math.pi)
    if sweep < 1e-12:
        sweep = 2 * math.pi                  # same start and end: a full circle
    # chords no more than res/4 off the true arc
    seg = 2 * math.acos(max(-1.0, 1 - (res / 4) / r0)) if r0 > res / 4 else math.pi / 2
    steps = max(1, math.ceil(sweep / min(seg, math.radians(10))))
    sign = -1 if cw else 1
    out = []
    for k in range(1, steps + 1):
        a = a0 + sign * sweep * k / steps
        r = r0 + (r1 - r0) * k / steps
        out.append({**end, "x": cx + r * math.cos(a), "y": cy + r * math.sin(a),
                    "z": start["z"] + (end["z"] - start["z"]) * k / steps})
    out[-1] = dict(end)
    return out, math.hypot(sweep * (r0 + r1) / 2, end["z"] - start["z"])


def machine_from_library(machine: Machine, library) -> Machine:
    """Fill the machine's tool table from a camcore tool library (path or dict)."""
    lib = library if isinstance(library, dict) else json.loads(Path(library).read_text())
    for name, t in lib.items():
        if "number" in t:
            machine.tools.setdefault(int(t["number"]), {**t, "name": name})
    return machine
