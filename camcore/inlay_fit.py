"""Inlay fit check: do the pocket and the plug, as verify simulates them, fit?

Both programs are replayed by camcore.verify on a fine grid (FIT_RES). The plug is
flipped about its design's mirror line onto the pocket and slid down until it first
touches. Then:
- bottom gap: under the uncut plug face, over the pocket floor (should be glue_gap)
- flange gap: under the plug's flange, over the uncut base surface (surface_gap)
- proud: how far the plug's face stops above where the design puts it (> 0: the
  walls touch early; < 0: the plug sinks in further)
- wall contact: the share of cells where both parts have a wall that touch, and
  the median gap there (a plug cut with a steeper bit seats on the pocket's rim
  at the right height, but its walls stand apart)

The programs are replayed as G1 lines (the post's arcs lie within its
arc_tolerance, 0.01 mm, of them): verify replays G2/G3 with chords up to res/4
inside the arc, which on a 60 degree wall would read as 0.09 mm of height.

Only cells inside the plug's frame and stock count: outside a plug_margin frame
the board is trimmed off before gluing. The fit passes when both gaps are within
FIT_TOLERANCE of what the plug job asks for and the median wall gap is below it. The measuring uses only verify's
stock model, never the toolpaths.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely

FIT_RES = 0.2           # mm, verify grid for the fit
FIT_TOLERANCE = 0.1     # mm, allowed error of each gap
LEVEL = 0.01            # mm, how close a cell must be to a flat level to count as on it
CONTACT = 0.05          # mm, a wall gap below this counts as contact


@dataclass
class Fit:
    glue_gap: float
    surface_gap: float
    bottom_gap: float | None = None
    flange_gap: float | None = None
    proud: float = 0.0
    wall_contact: float | None = None
    wall_gap: float | None = None
    first_touch: tuple[float, float] | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        f = lambda v: "not measured" if v is None else f"{v:.3f} mm"
        out = [f"bottom gap {f(self.bottom_gap)} (glue_gap {self.glue_gap:g})",
               f"flange gap {f(self.flange_gap)} (surface_gap {self.surface_gap:g})",
               f"plug face {self.proud:+.3f} mm from its design height (+ = stands proud, walls touch early)"]
        if self.wall_contact is not None:
            out.append(f"walls in contact (< {CONTACT:g} mm): {100 * self.wall_contact:.0f}% of wall cells, "
                       f"median wall gap {self.wall_gap:.3f} mm")
        if self.first_touch is not None:
            out.append(f"first touch near X{self.first_touch[0]:.1f} Y{self.first_touch[1]:.1f} (pocket job coordinates)")
        return out + [f"FIT PROBLEM: {p}" for p in self.problems]


def inlay_op(job: dict, part: str) -> dict:
    found = [op for op in job.get("operations", []) if op.get("op") == "inlay"
             and op.get("part", "pocket") == part]
    if len(found) != 1:
        raise ValueError(f"the {part} job needs exactly one inlay operation with \"part\": \"{part}\"")
    return found[0]


def machine_for(job: dict, base: Path, machine_path=None):
    """The verify machine for a job: --machine, else the job's "machine", else the default profile."""
    from .check import DEFAULT_MACHINE, job_library
    from .verify import Machine, machine_from_library
    if machine_path is None:
        machine_path = base / job["machine"] if "machine" in job else DEFAULT_MACHINE
    return machine_from_library(Machine.load(Path(machine_path)), job_library(job, base))


def simulate(job: dict, base: Path, machine_path=None, res: float = FIT_RES, grid_origin=None):
    """(simulated stock, verify issues) of an inlay job. grid_origin (x, y), at or
    below the stock's low corner, moves verify's grid so its cells land there."""
    from .job import run_job
    from .post import job_post
    from .verify import run
    stock = job.get("stock")
    if not stock or stock.get("type") != "box":
        raise ValueError("an inlay part needs a box stock")
    if grid_origin is not None:
        stock = {**stock, "x": [grid_origin[0], stock["x"][1]], "y": [grid_origin[1], stock["y"][1]]}
    post = job_post(job)
    if hasattr(post, "arcs"):
        post.arcs = False    # see the module notes: verify's arc chords would read as a fit error
    gcode = post.emit(run_job(job, base))
    issues, _, sim = run(gcode, machine_for(job, base, machine_path), stock, res=res)
    return sim, issues


def fit(pocket_job, plug_job, machine_path=None, res: float = FIT_RES) -> Fit:
    from .job import build_shape, load_job
    from .ops.inlay import plug_frame
    from .shapes import mirror
    pjob, pbase = load_job(pocket_job)
    qjob, qbase = load_job(plug_job)
    pop, qop = inlay_op(pjob, "pocket"), inlay_op(qjob, "plug")
    pdesign = build_shape(pjob["shapes"][pop["shape"]], pbase)
    qdesign = build_shape(qjob["shapes"][qop["shape"]], qbase)
    g, m = qop.get("glue_gap", 0.5), qop.get("surface_gap", 1.5)
    s = qop["pocket_depth"] - g
    result = Fit(g, m)

    # Pocket point (x, y) meets the flipped plug at (cx - x, y + dy): the plug turned
    # over about its mirror line, its design laid on the pocket's.
    pb, qb = pdesign.bounds, qdesign.bounds
    cx = (pb[0] + pb[2]) / 2 + (qb[0] + qb[2]) / 2
    dy = (qb[1] + qb[3]) / 2 - (pb[1] + pb[3]) / 2
    psim, pissues = simulate(pjob, pbase, machine_path, res)
    # the plug's grid is moved (by less than a cell, off the board) so that every
    # pocket cell lands on a plug cell: both heights are exact, nothing interpolated
    qs = qjob["stock"]
    ax, ay = cx - psim.x0, psim.y0 + dy
    origin = (qs["x"][0] - (qs["x"][0] - ax) % res, qs["y"][0] - (qs["y"][0] - ay) % res)
    qsim, qissues = simulate(qjob, qbase, machine_path, res, origin)
    for name, issues in (("pocket", pissues), ("plug", qissues)):
        errors = [i for i in issues if i.level == "error"]
        if errors:
            result.problems.append(f"the {name} program has {len(errors)} verify errors")

    xs = psim.x0 + np.arange(psim.h.shape[0]) * res
    ys = psim.y0 + np.arange(psim.h.shape[1]) * res
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    QX, QY = cx - X, Y + dy
    qi = np.rint((QX - qsim.x0) / res).astype(int)
    qj = np.rint((QY - qsim.y0) / res).astype(int)
    assert np.allclose(qsim.x0 + qi * res, QX, atol=1e-6) and np.allclose(qsim.y0 + qj * res, QY, atol=1e-6)
    frame = plug_frame(mirror(qdesign, "y"), qs, qop.get("plug_margin"))
    eps = 1e-6
    inside = (shapely.contains_xy(frame.buffer(eps), QX, QY)
              & (QX >= qs["x"][0] - eps) & (QX <= qs["x"][1] + eps)
              & (QY >= qs["y"][0] - eps) & (QY <= qs["y"][1] + eps)
              & (qi >= 0) & (qi < qsim.h.shape[0]) & (qj >= 0) & (qj < qsim.h.shape[1]))
    if not inside.any():
        result.problems.append("the flipped plug does not overlap the pocket board")
        return result
    dp = (psim.top - psim.h)[inside]                  # cut depths, >= 0
    dq = qsim.top - qsim.h[qi[inside], qj[inside]]
    t = -float((dp + dq).min())                       # plug top height with z = 0 on the base surface
    gap = t + dp + dq
    result.proud = t + s
    k = int(np.argmin(dp + dq))
    result.first_touch = (float(X[inside][k]), float(Y[inside][k]))

    floor, flange = dp.max(), dq.max()
    bottom = (dq < LEVEL) & (dp > floor - LEVEL)
    over_base = (dq > flange - LEVEL) & (dp < LEVEL)
    if bottom.any():
        result.bottom_gap = float(np.median(gap[bottom]))
    if over_base.any():
        result.flange_gap = float(np.median(gap[over_base]))
    walls = (dp > LEVEL) & (dp < floor - LEVEL) & (dq > LEVEL) & (dq < flange - LEVEL)
    if walls.any():
        result.wall_contact = float((gap[walls] < CONTACT).mean())
        result.wall_gap = float(np.median(gap[walls]))

    if result.bottom_gap is not None and abs(result.bottom_gap - g) > FIT_TOLERANCE:
        result.problems.append(f"bottom gap {result.bottom_gap:.3f} mm is more than {FIT_TOLERANCE:g} mm "
                               f"off glue_gap {g:g}")
    if result.wall_gap is not None and result.wall_gap > FIT_TOLERANCE:
        result.problems.append(f"the walls stand apart (median gap {result.wall_gap:.3f} mm): "
                               f"were both parts cut with the same V-bit?")
    if result.flange_gap is None:
        result.problems.append("no flange over the base surface found: the plug does not sit on the pocket")
    elif abs(result.flange_gap - m) > FIT_TOLERANCE:
        result.problems.append(f"flange gap {result.flange_gap:.3f} mm is more than {FIT_TOLERANCE:g} mm "
                               f"off surface_gap {m:g}")
    return result


def fit_for(plug_job_path, job: dict, base: Path, machine_path=None) -> Fit | None:
    """The fit for a plug job that names its pocket job with "fit_with", else None."""
    ops_ = [op for op in job.get("operations", []) if op.get("op") == "inlay" and op.get("fit_with")]
    if not ops_:
        return None
    return fit(base / ops_[0]["fit_with"], plug_job_path, machine_path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="camcore inlay-fit",
                                 description="Simulate an inlay's pocket and plug jobs and check that they fit")
    ap.add_argument("pocket_job")
    ap.add_argument("plug_job")
    ap.add_argument("--machine", help="machine JSON (default: the plug job's \"machine\", else "
                                      "machine/printnc_provisional.json)")
    args = ap.parse_args(argv)
    result = fit(args.pocket_job, args.plug_job, args.machine)
    for line in result.lines():
        print(line)
    print("FIT OK" if result.ok else "FIT FAILED")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
