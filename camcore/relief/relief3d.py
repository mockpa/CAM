"""3D relief: a height field milled as a surface out of a board (relief plan phase 2).

Replaces Reliefwerk's `fr_*` (its own roughing and finishing G-code). The design
becomes a closed STL block in machine coordinates plus an ordinary job that
roughs it with `surface_rough` and finishes it with `surface_finish` rasters, so
camcore check, the ATC and verify handle it like any other 3D job.

Machine frame: X0 Y0 at the front-left corner of the piece, Z0 on the stock top.
The relief (the HeightField, W x H) sits inside an optional frame that stays at
full height, so the piece is (W + 2 frame) x (H + 2 frame). Height h (0..1)
becomes Z = -depth * (1 - h): white stays at the top, black is `depth` deep. The
block's sides go straight down to the stock bottom. Grid nodes are doubled on the
frame edge, so the frame wall is vertical in the model.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .heightfield import HeightField

MIN_FLOOR = 1.0          # mm of stock that must stay under the deepest point of the relief
DIRECTIONS = {"x": [0.0], "y": [90.0], "xy": [0.0, 90.0]}


class ReliefError(ValueError):
    pass


@dataclass
class Relief3D:
    """Settings of a 3D relief (the job-file keys under "relief")."""
    depth: float                       # mm below the stock top for height 0 (black)
    thickness: float                   # stock thickness, mm
    frame: float = 0.0                 # border left at full height around the relief, mm
    resolution: float = 0.5            # model grid, mm
    rough: bool = True
    rough_tool: str = "em6"
    finish_tool: str = "ball3"
    stock_to_leave: float = 0.5
    finish_stepover: float | None = None    # default: surface_finish's (cutting data or 0.15 x D)
    finish_dir: str = "x"              # raster passes along "x", "y" or both ("xy")


def axis_nodes(length: float, frame: float, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Grid positions along one axis of the piece and whether each lies on the relief.
    With a frame the nodes at the frame edge are doubled: the frame's (on the frame,
    full height) and the relief's (its edge value)."""
    def seg(a, b):
        return np.linspace(a, b, max(2, int(math.ceil((b - a) / cell - 1e-9)) + 1))

    inner = seg(frame, frame + length)
    if frame <= 0:
        return inner, np.ones(len(inner), dtype=bool)
    lo, hi = seg(0.0, frame), seg(frame + length, 2 * frame + length)
    xs = np.concatenate([lo, inner, hi])
    on = np.concatenate([np.zeros(len(lo), bool), np.ones(len(inner), bool), np.zeros(len(hi), bool)])
    return xs, on


def surface_grid(F: HeightField, depth: float, frame: float = 0.0, cell: float = 0.5):
    """(xs, ys, S): top heights S[j, i] at X = xs[i], Y = ys[j] (both ascending, Y up)."""
    if depth <= 0 or frame < 0 or cell <= 0:
        raise ReliefError("need depth > 0, frame >= 0 and resolution > 0")
    xs, onx = axis_nodes(F.width, frame, cell)
    ys, ony = axis_nodes(F.height, frame, cell)
    X, Y = np.meshgrid(xs - frame, ys - frame)
    S = np.where(onx[None, :] & ony[:, None], -depth * (1.0 - F.sample(X, Y)), 0.0)
    return xs, ys, S


def block_mesh(xs: np.ndarray, ys: np.ndarray, S: np.ndarray, bottom: float):
    """Closed trimesh: the top grid S, vertical sides and a flat bottom at Z = bottom."""
    import trimesh

    ny, nx = S.shape
    X, Y = np.meshgrid(xs, ys)
    top = np.stack([X, Y, S], -1).reshape(-1, 3)
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = [np.stack([a, b, c], 1), np.stack([a, c, d], 1)]                # top, facing up
    ring = np.concatenate([idx[0, :-1], idx[:-1, -1], idx[-1, :0:-1], idx[:0:-1, 0]])   # counter-clockwise
    o = ny * nx
    low = np.column_stack([top[ring, 0], top[ring, 1], np.full(len(ring), bottom)])
    centre = np.array([[(xs[0] + xs[-1]) / 2, (ys[0] + ys[-1]) / 2, bottom]])
    v = np.vstack([top, low, centre])
    k = np.arange(len(ring))
    p, q = ring, np.roll(ring, -1)
    pl, ql = o + k, o + np.roll(k, -1)
    faces += [np.stack([p, pl, ql], 1), np.stack([p, ql, q], 1)]           # sides, facing out
    faces.append(np.stack([np.full(len(k), o + len(ring)), ql, pl], 1))     # bottom fan, facing down
    return trimesh.Trimesh(v, np.vstack(faces), process=False)


def _tool(lib: dict, name: str) -> dict:
    if name not in lib:
        raise ReliefError(f"tool {name!r} is not in the tool library")
    return lib[name]


def _cut(lib: dict, name: str, material: str | None) -> dict:
    return (_tool(lib, name).get("cutting") or {}).get(material or "", {})


def check_design(d: Relief3D, lib: dict, material: str | None) -> list[str]:
    """Refuse settings that would break a cutter or cut through the board; return notes."""
    notes = []
    if d.thickness < d.depth + MIN_FLOOR:
        raise ReliefError(f"the stock ({d.thickness:g} mm) must be at least {MIN_FLOOR:g} mm thicker than "
                          f"the relief depth ({d.depth:g} mm)")
    if d.finish_dir not in DIRECTIONS:
        raise ReliefError(f"finish_dir must be one of {sorted(DIRECTIONS)}, not {d.finish_dir!r}")
    fin = _tool(lib, d.finish_tool)
    if d.rough:
        rt = _tool(lib, d.rough_tool)
        if rt.get("kind") not in ("flat", "ball", "bull"):
            raise ReliefError(f"roughing needs an end mill, {d.rough_tool} is a {rt.get('kind')}")
        fl = rt.get("flute_length")
        if fl is not None and d.depth > fl:
            raise ReliefError(f"the relief is {d.depth:g} mm deep but {d.rough_tool}'s flutes are only "
                              f"{fl:g} mm long: choose a longer roughing tool or a shallower relief")
    else:
        # Reliefwerk let the ball plunge into fresh stock at full depth and cut whole rows
        # there; allow finishing alone only where it takes no more than one normal step down.
        limit = _cut(lib, d.finish_tool, material).get("step_down")
        fl = fin.get("flute_length")
        if limit is None or d.depth > limit or (fl is not None and d.depth > fl):
            raise ReliefError(f"finishing without roughing would cut {d.depth:g} mm deep in one pass with "
                              f"{d.finish_tool} (step down {limit if limit is not None else '?'} mm): "
                              "turn roughing on")
        notes.append(f"no roughing: {d.finish_tool} cuts the whole {d.depth:g} mm in one pass")
    return notes


def build_job(F: HeightField, d: Relief3D, out_dir, name: str, library_path, material: str | None = "hardwood",
              machine_path=None, source: dict | None = None) -> dict:
    """Write `name`.stl into out_dir and return the job (paths relative to out_dir).
    `source` (the input file and its `prepare` settings) is recorded under "relief"."""
    from ..wizard import portable

    out = Path(out_dir)
    lib = json.loads(Path(library_path).read_text())
    notes = check_design(d, lib, material)
    xs, ys, S = surface_grid(F, d.depth, d.frame, d.resolution)
    out.mkdir(parents=True, exist_ok=True)
    stl = out / f"{name}.stl"
    block_mesh(xs, ys, S, -d.thickness).export(stl)
    pw, ph = float(xs[-1]), float(ys[-1])
    r = _tool(lib, d.finish_tool)["diameter"] / 2
    if d.frame > 0:          # the ball reaches the frame walls, the frame top stays as it is
        boundary = [d.frame - r, d.frame - r, pw - d.frame + r, ph - d.frame + r]
    else:
        boundary = [0.0, 0.0, pw, ph]
    ops, after = [], []
    if d.rough:
        # the last level lands on the deepest point plus the stock to leave, so the ball
        # never finds more than one step down plus the leave
        ops.append({"id": "rough", "op": "surface_rough", "model": name, "tool": d.rough_tool,
                    "stock_to_leave": d.stock_to_leave, "floor": -d.depth + d.stock_to_leave})
        after = ["rough"]
    for k, angle in enumerate(DIRECTIONS[d.finish_dir]):
        op = {"id": "finish" if k == 0 else "finish_y", "op": "surface_finish", "model": name,
              "tool": d.finish_tool, "strategy": "raster", "angle": angle,
              "boundary": [round(v, 4) for v in boundary], "floor": -float(d.depth)}
        if after:
            op["after"] = after
        if d.finish_stepover is not None:
            op["stepover"] = d.finish_stepover
        ops.append(op)
        after = [op["id"]]
    job = {
        "post": "linuxcnc",
        "tool_library": portable(Path(library_path), out),
        "material": material,
        "stock": {"type": "box", "x": [0.0, pw], "y": [0.0, ph], "z": [-float(d.thickness), 0.0]},
        "models": {name: {"file": stl.name}},
        "defaults": {"safe_z": 5.0},
        "operations": ops,
        "relief": {"mode": "3d", **asdict(d), "width": F.width, "height": F.height},
    }
    if source is not None:
        job["relief"]["source"] = source
    if notes:
        job["relief"]["notes"] = notes
    if machine_path is not None:
        job["machine"] = portable(Path(machine_path), out)
    return job


def write_job(F: HeightField, d: Relief3D, out_dir, name: str, library_path, material: str | None = "hardwood",
              machine_path=None, source: dict | None = None) -> Path:
    job = build_job(F, d, out_dir, name, library_path, material, machine_path, source)
    path = Path(out_dir) / f"{name}_job.json"
    path.write_text(json.dumps(job, indent=2) + "\n")
    return path
