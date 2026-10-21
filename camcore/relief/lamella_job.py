"""Lamella sheets: parts nested onto boards, one ordinary job per board (relief plan phase 4).

Replaces Reliefwerk's `lam_layout` / `lam_gcode_sheet`. Each lamella becomes a
`profile` op (outside, ramp entry, cut `thickness + overcut` deep) with its tabs on
the bottom edge (the glued edge, not the visible top), so camcore check, verify
and the sim see every board.

Spacing. A part's cut is the tool diameter D wide all round it. If the cuts of two
neighbouring parts touched, the scrap between them would be cut free wherever their
outlines open up again (the valleys of a wavy top edge): loose pieces with no tab.
So part outlines stay at least 2 D + web apart and D + web inside the board's edge;
then the scrap is one connected skeleton, nowhere thinner than `web`.

Interlocking (optional): two parts become one packing unit, the second turned 180
deg in the board plane so its top edge faces down into the first's, as close as the
2 D + web clearance allows (true distance between the outlines, as the cutter
leaves them). A 180 deg turn keeps the grain direction.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from shapely.affinity import affine_transform
from shapely.geometry import Polygon

from .heightfield import HeightField
from .lamella import Lamella, LamellaSettings, cut_outline, lamellas
from .nesting import Placed, pack


class SheetError(ValueError):
    pass


@dataclass
class SheetSettings:
    """Board and cutting settings of a lamella design (the job-file keys under "sheet")."""
    width: float = 1000.0          # board X, mm
    height: float = 600.0          # board Y, mm
    tool: str = "em6"
    overcut: float = 0.3           # cut this far past the board's bottom (into the spoilboard), mm
    web: float = 2.0               # thinnest scrap left between two cuts / a cut and the edge, mm
    allow_turn: bool = True        # parts may turn 90 deg (grain across); 180 deg is always allowed
    interlock: bool = False        # pair parts head to tail
    tabs: int = 2                  # per part, on its bottom edge
    tab_width: float = 8.0
    tab_height: float = 2.0
    step_down: float | None = None # default: the tool's cutting data for the material


@dataclass
class Member:
    part: Lamella
    flipped: bool                  # turned 180 deg in the unit
    lift: float                    # unit Y of the flipped part's bottom edge (0 when not flipped)


@dataclass
class Unit:
    """One or two parts packed as a rectangle: X 0..length, Y 0..h in unit coordinates."""
    members: list
    w: float
    h: float


def flip(poly: Polygon, length: float, lift: float) -> Polygon:
    """Turned 180 deg: part (s, y) -> unit (length - s, lift - y)."""
    return affine_transform(poly, [-1, 0, 0, -1, length, lift])


def pair_lift(a: Polygon, b: Polygon, length: float, clearance: float, tol: float = 1e-3) -> float:
    """Lowest lift for part b turned 180 deg above part a (both bottom edge on Y0, X 0..length)
    with at least `clearance` between them. Raising b only moves it away, so bisect."""
    lo, hi = 0.0, a.bounds[3] + b.bounds[3] + clearance
    if a.distance(flip(b, length, lo)) >= clearance:
        return lo
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if a.distance(flip(b, length, mid)) >= clearance - 1e-9:
            hi = mid
        else:
            lo = mid
    return hi


def units(parts: list[Lamella], radius: float, clearance: float, interlock: bool, fits=None) -> list[Unit]:
    """Single parts, or (interlock) pairs: tallest first, each with the partner that gives the
    lowest pair, kept only when the pair is lower than the two parts packed separately and
    `fits(w, h)` (a whole board takes it; default: always)."""
    closed = {p.label: cut_outline(p.outline, radius) for p in parts}
    left = sorted(parts, key=lambda p: -p.height)
    out = []
    while left:
        a = left.pop(0)
        best = None
        if interlock:
            for b in left:
                lift = pair_lift(closed[a.label], closed[b.label], a.length, clearance)
                if lift < a.height + b.height + clearance - 1e-9 and (best is None or lift < best[0]) \
                        and (fits is None or fits(a.length, lift)):
                    best = (lift, b)
        if best is None:
            out.append(Unit([Member(a, False, 0.0)], a.length, a.height))
        else:
            lift, b = best
            left.remove(b)
            out.append(Unit([Member(a, False, 0.0), Member(b, True, lift)], a.length, lift))
    return out


@dataclass
class Box:
    """A unit grown by the margin on every side, as the packer sees it."""
    unit: Unit
    w: float
    h: float


def to_sheet(pl: Placed, margin: float, edge: float, m: Member):
    """Affine matrix [a, b, d, e, xoff, yoff] (shapely) from part to board coordinates."""
    u = [-1, 0, 0, -1, pl.item.unit.w, m.lift] if m.flipped else [1, 0, 0, 1, 0, 0]
    # unit -> unturned box (margin), then turned 90 deg counter-clockwise inside its box
    a, b, d, e, xo, yo = u
    xo, yo = xo + margin, yo + margin
    if pl.turned:                                  # (x, y) -> (h_box - y, x)
        a, b, d, e, xo, yo = -d, -e, a, b, pl.w - yo, xo
    return [a, b, d, e, xo + edge + pl.x, yo + edge + pl.y]


def apply(matrix, pts):
    a, b, d, e, xo, yo = matrix
    p = np.asarray(pts, dtype=float)
    return np.column_stack([a * p[:, 0] + b * p[:, 1] + xo, d * p[:, 0] + e * p[:, 1] + yo])


def tab_points(part: Lamella, n: int, half: float) -> list[float]:
    """Tab centres along the bottom edge: n evenly spaced (Reliefwerk's), each fully on it."""
    xs = [part.length * k / (n + 1) for k in range(1, n + 1)]
    if any(x - half < 1 or x + half > part.length - 1 for x in xs):
        raise SheetError(f"{part.label} is {part.length:g} mm long, too short for {n} tabs "
                         f"{2 * half:g} mm long on the tool path: use fewer or narrower tabs")
    return xs


def machine_window(mdata: dict) -> dict:
    """Work-coordinate X/Y range the machine reaches (travel minus the G54 offset)."""
    t, wo = mdata["travel"], mdata.get("work_offset", {"x": 0, "y": 0, "z": 0})
    return {k: (t[k][0] - wo[k], t[k][1] - wo[k]) for k in ("x", "y")}


def check_sheet(st: SheetSettings, thickness: float, mdata: dict):
    """Refuse a board the machine cannot cut before any toolpath is made: outside the
    travel (every cut stays inside the board) or on top of a fixed obstacle (ATC rack, ...)."""
    if st.width <= 0 or st.height <= 0 or st.web <= 0 or st.overcut < 0:
        raise SheetError("need a board with width, height > 0, web > 0 and overcut >= 0")
    win = machine_window(mdata)
    for k, size in (("x", st.width), ("y", st.height)):
        lo, hi = win[k]
        if lo > 1e-9 or size > hi + 1e-9:
            raise SheetError(f"a {st.width:g} x {st.height:g} mm board does not fit the machine: {k.upper()} "
                             f"reaches {lo:g}..{hi:g} in work coordinates (travel minus G54), the board "
                             f"needs 0..{size:g}")
    if mdata.get("body"):
        from ..body import Body, Part, overlaps

        wo = mdata.get("work_offset", {"x": 0, "y": 0, "z": 0})
        board = Part("board", "box", (wo["x"], wo["y"], wo["z"] - thickness),
                     (wo["x"] + st.width, wo["y"] + st.height, wo["z"]))
        for ob in Body(mdata["body"]).obstacles(False):
            if not ob.tool_may_enter and overlaps(board, [0, 0, 0], ob)[0]:
                raise SheetError(f"a {st.width:g} x {st.height:g} mm board at G54 would lie on the {ob.name}")


@dataclass
class OnBoard:
    label: str
    points: np.ndarray     # outline on the board (counter-clockwise; first two points = bottom edge)
    tabs: np.ndarray       # tab centres on the board
    turned: bool           # turned 90 deg (grain across the part)
    flipped: bool          # turned 180 deg in an interlocked pair


@dataclass
class Sheet:
    index: int
    placed: list           # Placed (item = Box)
    parts: list            # OnBoard, by label


def nest(parts: list[Lamella], st: SheetSettings, tool_diameter: float) -> list[Sheet]:
    """Parts on boards. Outlines end up >= 2 D + web apart and >= D + web inside the edges."""
    r = tool_diameter / 2
    margin = tool_diameter + st.web / 2           # around every unit: 2 margins = 2 D + web
    edge = st.web / 2                             # plus this at the board edge: D + web
    if st.tabs < 0 or st.tab_width <= 0 or st.tab_height <= 0:
        raise SheetError("need tabs >= 0, tab_width > 0 and tab_height > 0")
    bw, bh = st.width - 2 * edge, st.height - 2 * edge

    def fits(w, h):
        w, h = w + 2 * margin, h + 2 * margin
        return (w <= bw and h <= bh) or (st.allow_turn and h <= bw and w <= bh)

    us = units(parts, r, 2 * margin, st.interlock, fits)
    boxes = [Box(u, u.w + 2 * margin, u.h + 2 * margin) for u in us]
    sheets, too_big = pack(boxes, bw, bh, st.allow_turn)
    if too_big:
        labels = ", ".join(m.part.label for b in too_big for m in b.unit.members)
        raise SheetError(f"{labels} do(es) not fit a {st.width:g} x {st.height:g} mm board with "
                         f"{margin:g} mm around every part: use a bigger board or a smaller relief")
    out = []
    half = (st.tab_width + tool_diameter) / 2
    for k, placed in enumerate(sheets):
        rows = []
        for pl in placed:
            for m in pl.item.unit.members:
                mat = to_sheet(pl, margin, edge, m)
                pts = apply(mat, np.asarray(m.part.outline.exterior.coords)[:-1])
                tabs = apply(mat, [(x, 0.0) for x in tab_points(m.part, st.tabs, half)]) if st.tabs \
                    else np.zeros((0, 2))
                rows.append(OnBoard(m.part.label, pts, tabs, pl.turned, m.flipped))
        rows.sort(key=lambda r: r.label)
        out.append(Sheet(k, placed, rows))
    return out


@dataclass
class Design:
    parts: list            # Lamella
    sheets: list           # Sheet
    tool: dict             # the cutting tool's library entry
    depth: float           # cut depth


def plan_boards(F: HeightField, ls: LamellaSettings, st: SheetSettings, library_path, machine_path=None) -> Design:
    """Lamellas laid out on boards, after every check that needs no toolpath."""
    from ..check import DEFAULT_MACHINE

    lib = json.loads(Path(library_path).read_text())
    if st.tool not in lib:
        raise SheetError(f"tool {st.tool!r} is not in the tool library")
    tool = lib[st.tool]
    if tool.get("kind", "flat") not in ("flat", "bull"):
        raise SheetError(f"cutting out lamellas needs an end mill, {st.tool} is a {tool.get('kind')}")
    depth = ls.thickness + st.overcut
    if tool.get("flute_length") is not None and depth > tool["flute_length"]:
        raise SheetError(f"the cut is {depth:g} mm deep but {st.tool}'s flutes are only {tool['flute_length']:g} mm long")
    mpath = Path(machine_path) if machine_path is not None else DEFAULT_MACHINE
    check_sheet(st, ls.thickness, json.loads(mpath.read_text()))
    parts = lamellas(F, ls)
    if not parts:
        raise SheetError(f"no lamella {ls.thickness:g} mm thick fits across the artwork")
    return Design(parts, nest(parts, st, tool["diameter"]), tool, depth)


def build_jobs(F: HeightField, ls: LamellaSettings, st: SheetSettings, out_dir, name: str, library_path,
               material: str | None = "hardwood", machine_path=None, source: dict | None = None,
               design: Design | None = None) -> list[dict]:
    """One job per board (paths relative to out_dir)."""
    from ..wizard import portable

    out = Path(out_dir)
    d = design or plan_boards(F, ls, st, library_path, machine_path)
    depth = d.depth
    jobs = []
    for sh in d.sheets:
        shapes, ops = {}, []
        for ob in sh.parts:
            label, tabs = ob.label, ob.tabs
            shapes[label] = {"type": "polygon", "points": [[round(x, 4), round(y, 4)] for x, y in ob.points]}
            op = {"id": label, "op": "profile", "shape": label, "tool": st.tool, "side": "outside",
                  "depth": round(depth, 4)}
            if st.step_down is not None:
                op["step_down"] = st.step_down
            if st.tabs:
                op["tabs"] = {"at": [[round(x, 4), round(y, 4)] for x, y in tabs],
                              "width": st.tab_width, "height": st.tab_height}
            ops.append(op)
        job = {
            "post": "linuxcnc",
            "tool_library": portable(Path(library_path), out),
            "material": material,
            "stock": {"type": "box", "x": [0.0, float(st.width)], "y": [0.0, float(st.height)],
                      "z": [-float(ls.thickness), 0.0]},
            "defaults": {"safe_z": 5.0},
            "shapes": shapes,
            "operations": ops,
            "relief": {"mode": "lamellas", "sheet_index": sh.index + 1, "sheets": None,
                       "width": F.width, "height": F.height, "lamellas": asdict(ls), "sheet": asdict(st)},
        }
        if source is not None:
            job["relief"]["source"] = source
        if machine_path is not None:
            job["machine"] = portable(Path(machine_path), out)
        jobs.append(job)
    for job in jobs:
        job["relief"]["sheets"] = len(jobs)
    return jobs


def dumps(job: dict) -> str:
    """JSON with every list of numbers (a point, a range) on one line."""
    import re

    text = json.dumps(job, indent=1)
    return re.sub(r"\[\s*(-?[\d.e+-]+(?:,\s*-?[\d.e+-]+)*)\s*\]",
                  lambda m: "[" + ", ".join(v.strip() for v in m.group(1).split(",")) + "]", text)


def write_jobs(F: HeightField, ls: LamellaSettings, st: SheetSettings, out_dir, name: str, library_path,
               material: str | None = "hardwood", machine_path=None, source: dict | None = None,
               paperwork: bool = True) -> list[Path]:
    """Write a job per board, `name`_sheetN_job.json, and (`paperwork`) the parts list,
    assembly plan and board plans next to them (relief/sheets.py)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = plan_boards(F, ls, st, library_path, machine_path)
    if paperwork:
        from .sheets import write_lamella_paperwork
        write_lamella_paperwork(F, ls, st, d, out, name)
    paths = []
    for job in build_jobs(F, ls, st, out, name, library_path, material, machine_path, source, d):
        p = out / f"{name}_sheet{job['relief']['sheet_index']}_job.json"
        p.write_text(dumps(job) + "\n")
        paths.append(p)
    return paths
