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
- tool tip below the rotary centreline (Z < 0) on cylinder stock -> error

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
                       d.get("atc_pockets"), d.get("probe_tool"), table)


def read_tool_table(path) -> set:
    """Tool numbers in a LinuxCNC tool table (lines like "T3 P3 Z0 D6 ;comment")."""
    nums = set()
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*T(\d+)\b", line.split(";")[0], re.I)
        if m:
            nums.add(int(m.group(1)))
    return nums


class Stock:
    """Box stock: heightmap of top Z over XY. Cylinder: radius map over (X, A)."""

    def __init__(self, spec: dict, res: float = 0.5):
        self.spec, self.res, self.kind = spec, res, spec["type"]
        if self.kind == "box":
            (self.x0, x1), (self.y0, y1) = spec["x"], spec["y"]
            self.bottom, self.top = spec["z"]
            nx, ny = int(math.ceil((x1 - self.x0) / res)) + 1, int(math.ceil((y1 - self.y0) / res)) + 1
            self.h = np.full((nx, ny), float(self.top))
        elif self.kind == "cylinder":
            self.x0, x1 = spec["x"]
            self.radius = spec["diameter"] / 2
            na = int(math.ceil(360 / math.degrees(res / self.radius)))
            self.a_res = 360 / na                      # cells divide the full turn exactly
            nx = int(math.ceil((x1 - self.x0) / res)) + 1
            self.h = np.full((nx, na), float(self.radius))
        else:
            raise ValueError(f"unknown stock type {self.kind}")

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
        raise AssertionError("cylinder stock uses _ray_cells")

    def _ray_cells(self, x, a, z, r):
        """Cylinder cells whose ray can meet a tool of radius r with its tip at height z:
        index, dx and the angle phi (rad) of each cell's ray from the tool axis."""
        i0 = max(int(math.floor((x - r - self.x0) / self.res)), 0)
        i1 = min(int(math.ceil((x + r - self.x0) / self.res)), self.h.shape[0] - 1)
        if i0 > i1:
            return None
        # the tool body lies above its tip and within r of its axis: tan(phi) <= r / z
        span_deg = math.degrees(math.atan2(r, z)) if z > 0 else 90.0
        span = int(math.ceil(span_deg / self.a_res)) + 1
        jc = int(round((a % 360) / self.a_res))
        js = np.arange(jc - span, jc + span + 1) % self.h.shape[1]
        phi = np.radians((js * self.a_res - a + 180) % 360 - 180)
        dx = self.x0 + np.arange(i0, i1 + 1) * self.res - x
        dxg, phig = np.meshgrid(dx, phi, indexing="ij")
        return (slice(i0, i1 + 1), js), dxg, phig

    @staticmethod
    def _ray_interval(dx, phi, z, tool: dict):
        """Where each ray from the rotary axis passes through the tool body.

        The ray at angle phi from the tool axis, in the plane at axial offset dx
        from the tip, has points at lateral rho*|sin(phi)| and height rho*cos(phi)
        in the tool frame. The body (cutter profile, shank straight up) is convex,
        so the ray meets it in one interval [lo, hi] of rho; lo > hi where it
        misses. Its ends are rho = 0, the footprint exit, or roots of
        g(rho) = rho cos(phi) - profile(d(rho)) = z.
        """
        r = tool.get("diameter", 3) / 2
        kind = tool.get("kind")
        c, s = np.cos(phi), np.abs(np.sin(phi))
        rr2 = r * r - dx * dx
        fp = np.where(s > 1e-12, np.sqrt(np.clip(rr2, 0, None)) / np.where(s > 1e-12, s, 1), np.inf)
        if kind == "vbit":
            kv = 1 / math.tan(math.radians(tool.get("v_angle", 90)) / 2)
            prof = lambda d: kv * d
        elif kind == "ball":
            prof = lambda d: r - np.sqrt(np.clip(r * r - d * d, 0, None))
        else:
            prof = lambda d: np.zeros_like(d)

        def g(rho):
            with np.errstate(invalid="ignore"):
                return rho * c - prof(np.hypot(dx, rho * s))

        with np.errstate(divide="ignore", invalid="ignore"):
            if kind == "vbit":
                qa, qb, qc = c * c - kv * kv * s * s, -2 * z * c, z * z - kv * kv * dx * dx
                disc = qb * qb - 4 * qa * qc
                sq = np.sqrt(np.clip(disc, 0, None))
                lin = np.abs(qa) < 1e-12
                r1 = np.where(lin, -qc / np.where(qb != 0, qb, np.nan), (-qb - sq) / (2 * qa))
                r2 = np.where(lin, np.nan, (-qb + sq) / (2 * qa))
                r1, r2 = np.where(disc >= 0, r1, np.nan), np.where(disc >= 0, r2, np.nan)
            elif kind == "ball":
                w = z + r
                disc = rr2 - w * w * s * s
                sq = np.sqrt(np.clip(disc, 0, None))
                r1, r2 = np.where(disc >= 0, w * c - sq, np.nan), np.where(disc >= 0, w * c + sq, np.nan)
            else:
                r1, r2 = z / np.where(c > 1e-12, c, np.nan), np.full_like(c, np.nan)
        tol = 1e-6 * (1 + abs(z))
        lo, hi = np.full_like(c, np.inf), np.full_like(c, -np.inf)
        for root in (r1, r2):
            ok = np.isfinite(root) & (root >= -tol) & (root <= fp + tol)
            ok &= np.abs(np.where(ok, g(np.where(ok, root, 0)), np.inf) - z) <= tol
            lo, hi = np.where(ok, np.minimum(lo, root), lo), np.where(ok, np.maximum(hi, root), hi)
        at0 = g(np.zeros_like(c)) >= z
        lo, hi = np.where(at0, 0.0, lo), np.where(at0, np.maximum(hi, 0.0), hi)
        endless = ~np.isfinite(fp)                       # ray along the tool axis: the shank goes on forever
        atfp = np.where(endless, c > 1e-12, g(np.where(endless, 0, fp)) >= z)
        hi = np.where(atfp, fp, hi)
        lo = np.where(atfp, np.minimum(lo, fp), lo)
        miss = (rr2 < 0) | (c <= 1e-12)
        return np.where(miss, np.inf, np.maximum(lo, 0.0)), np.where(miss, -np.inf, hi)

    @staticmethod
    def _profile(d, tool: dict):
        """Height of the cutter surface above its tip at radial distance d."""
        r = tool.get("diameter", 3) / 2
        if tool.get("kind") == "vbit":
            return d / math.tan(math.radians(tool.get("v_angle", 90)) / 2)
        if tool.get("kind") == "ball":
            return r - np.sqrt(np.clip(r * r - d * d, 0, None))
        return np.zeros_like(d)

    def interference(self, x, y_or_a, z, tool: dict) -> float:
        """How far material pokes into the cutter body at this position (<= 0: clear).

        Cylinder stock: each cell holds the material from the axis out to h along
        its ray; the result is how far along a ray it reaches into the body.
        """
        r = tool.get("diameter", 3) / 2
        if self.kind == "cylinder":
            c = self._ray_cells(x, y_or_a, z, r)
            if c is None:
                return -math.inf
            idx, dx, phi = c
            lo, hi = self._ray_interval(dx, phi, z, tool)
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

    def cut(self, x, y_or_a, z, tool: dict):
        r = tool.get("diameter", 3) / 2
        if self.kind == "cylinder":
            # a ray whose outer end lies in the body is cut back to where it enters;
            # one the body only crosses keeps its full length (conservative)
            c = self._ray_cells(x, y_or_a, z, r)
            if c is None:
                return
            idx, dx, phi = c
            lo, hi = self._ray_interval(dx, phi, z, tool)
            sub = self.h[idx]
            self.h[idx] = np.where((lo < sub) & (hi >= sub - 1e-9), lo, sub)
            return
        c = self._cells(x, y_or_a, r)
        if c is None:
            return
        idx, d, _ = c
        sub = self.h[idx]
        self.h[idx] = np.where(d <= r, np.minimum(sub, z + self._profile(d, tool)), sub)


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
    rotary = stock is not None and stock.kind == "cylinder"
    warned = set()

    def once(level, n, msg, key):
        if key not in warned:
            warned.add(key)
            issues.append(Issue(level, n, msg))

    def move(n, kind, pos, new, total, scale=1.0):
        """One straight move (a G0/G1, or one chord of an arc): travel, time, stock checks."""
        for k in ("x", "y", "z"):
            lo, hi = machine.travel[k]
            mval = new[k] + machine.work_offset[k]
            if not lo - 1e-6 <= mval <= hi + 1e-6:
                once("error", n, f"{k.upper()} {new[k]:.3f} is outside machine travel "
                     f"({lo - machine.work_offset[k]:.1f} .. {hi - machine.work_offset[k]:.1f} in work coordinates)",
                     ("travel", k))
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
            retract = all(abs(new[k] - pos[k]) < 1e-9 for k in "xya") and new["z"] >= pos["z"]
            seg = math.dist([pos[k] for k in "xyz"], [new[k] for k in "xyz"])
            if rotary:
                seg = max(seg, abs(new["a"] - pos["a"]) / 360 * 2 * math.pi * max(new["z"], 1))
            steps = max(1, int(seg / (res / 2)))
            horiz = math.hypot(new["x"] - pos["x"], new["y"] - pos["y"])
            if rotary:
                horiz = math.hypot(horiz, math.radians(new["a"] - pos["a"]) * max(new["z"], 1))
            plunging = kind == "feed" and pos["z"] - new["z"] > horiz + 1e-9   # steeper than 45 deg down
            for s in range(1, steps + 1):
                if retract:      # moving straight up out of the cut never hits anything
                    break
                t = s / steps
                p = {k: pos[k] + (new[k] - pos[k]) * t for k in pos}
                u = p["a"] if rotary else p["y"]
                if stock.interference(p["x"], u, p["z"], tinfo) > 0.01:
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
                    surface = stock.radius if rotary else stock.top
                    if fl and surface - p["z"] > fl + 1e-6:
                        once("warning", n, f"T{tool} cuts {surface - p['z']:.2f} mm deep, "
                             f"more than its {fl} mm flute length", ("flute", tool))
                    stock.cut(p["x"], u, p["z"], tinfo)

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
                pos["z"] = w["Z"][0] - machine.work_offset["z"]
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
