"""Rotary roughing and finishing from a mesh (guide step 8B)."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from camcore import ops
from camcore.cylmap import radial_map
from camcore.job import continue_a, load_job, run_job
from camcore.ops.rotary3d import decimate
from camcore.post import get_post, job_post
from camcore.stl import place, revolve
from camcore.tools import Tool
from camcore.verify import Machine, run

EX = Path(__file__).parent.parent / "examples"
EM6 = Tool("em6", 6.0, "flat", flute_length=22, number=2)
BALL3 = Tool("ball3", 3.0, "ball", flute_length=12, number=3, center_cutting=True)
STOCK = {"type": "cylinder", "diameter": 22, "x": [-2, 20]}


def machine():
    tools = {t.number: {"diameter": t.diameter, "kind": t.kind, "flute_length": t.flute_length,
                        "center_cutting": t.center_cutting} for t in (EM6, BALL3)}
    # G53 Z0, where tool changes happen, is 100 mm above the rotary axis
    return Machine(travel={"x": [-500, 500], "y": [-500, 500], "z": [-500, 500]},
                   work_offset={"x": 0, "y": 0, "z": -100}, has_a=True, tools=tools)


@pytest.fixture(scope="module")
def part():
    """Shaft with a sharp-edged groove and an off-axis bump at A = 90 (the -Y side)."""
    shaft = revolve([(0, 8), (5, 8), (5.01, 5), (11, 5), (12, 9), (18, 9)], 120)
    bump = trimesh.creation.icosphere(subdivisions=3, radius=2.5)
    bump.apply_translation((8, -6, 0))
    return trimesh.util.concatenate([shaft, bump])


def feed_runs(tp):
    """Feed moves as polylines (x, a, z), each starting where the preceding rapid ended."""
    runs, pos, cur, prev = [], {}, None, None
    for m in tp.moves:
        pos.update({k: getattr(m, k) for k in "xza" if getattr(m, k) is not None})
        if m.kind == "feed":
            if cur is None:
                cur = [prev] if prev is not None else []
                runs.append(cur)
            cur.append((pos["x"], pos["a"], pos["z"]))
        else:
            cur = None
        prev = (pos["x"], pos["a"], pos["z"]) if len(pos) == 3 else None
    return [np.array(r) for r in runs]


def feed_points(tp):
    return np.vstack(feed_runs(tp))


def worst_gouge(tp, mesh, tool):
    """Deepest model penetration along the feed moves (densely subdivided), by brute force."""
    cm = radial_map(mesh, x_step=0.1, a_step=1.0, x_range=(-4, 22))
    X, A = np.meshgrid(cm.xs, cm.angles, indexing="ij")
    R, worst = tool.radius, -np.inf
    segs = [s for pts in feed_runs(tp) for s in zip(pts[:-1], pts[1:])]
    for (x0, a0, z0), (x1, a1, z1) in segs:
        n = max(1, int(max(abs(x1 - x0) / 0.05, abs(a1 - a0) / 1.0)))
        for t in np.linspace(0, 1, n + 1):
            x, a, z = x0 + (x1 - x0) * t, a0 + (a1 - a0) * t, z0 + (z1 - z0) * t
            sel = np.abs(cm.xs - x) <= R
            phi = np.radians(A[sel] - a)
            d = np.hypot(X[sel] - x, cm.r[sel] * np.sin(phi))
            ins = (d <= R) & (np.cos(phi) > 0)
            if not ins.any():
                continue
            prof = R - np.sqrt(np.clip(R * R - d * d, 0, None)) if tool.kind == "ball" else 0 * d
            worst = max(worst, float((cm.r[sel] * np.cos(phi) - z - prof)[ins].max()))
    return worst


def gcode(*paths):
    return get_post("linuxcnc").emit(continue_a(list(paths), {id(p) for p in paths}))


# --- roughing -------------------------------------------------------------------

def test_rough_cylinder_levels_are_exact():
    # radius-5 shaft in 22 mm stock, 3 mm levels, 0.5 left: levels 8, 5.5 (the floor of the model)
    shaft = revolve([(0, 5), (10, 5)], 120)
    tp = ops.rotary_rough(shaft, EM6, 22, step_down=3, stock_to_leave=0.5, x_range=(2, 8), floor=1)
    z = feed_points(tp)[:, 2]
    assert z.min() == pytest.approx(5.5, abs=1e-9)
    assert any(abs(v - 8.0) < 1e-9 for v in z)
    assert all(m.y is None for m in tp.moves)
    a = feed_points(tp)[:, 1]
    assert a.max() <= 360 * 4 + 1e-6 and abs(a[-1]) < 1e-6   # level 2 turns A back: no wind-up
    issues, _, sim = run(gcode(tp), machine(), STOCK)
    assert [str(i) for i in issues if i.level == "error"] == []
    xs = sim.x0 + np.arange(sim.h.shape[0]) * sim.res
    mid = (xs >= 3) & (xs <= 7)
    assert np.allclose(sim.h[mid], 5.5, atol=0.02)


def test_rough_then_finish_part_verifies_and_never_gouges(part):
    rough = ops.rotary_rough(part, EM6, 22, step_down=2.5, stock_to_leave=0.5, x_range=(0, 18), floor=2,
                             resolution=0.5)
    assert worst_gouge(rough, part, EM6) <= -0.45                  # the stock to leave is kept
    for strategy in ("helical", "a_raster", "x_raster"):
        fin = ops.rotary_finish(part, BALL3, 22, strategy=strategy, stepover=0.6, x_range=(0, 18), floor=2,
                                resolution=0.3)
        # bound: 0.005 decimation tolerance plus the model between 0.3 mm samples (the paths
        # are exact against the sampled model); a perpendicular-distance decimation was 0.13 here
        assert worst_gouge(fin, part, BALL3) <= 0.02, strategy
        issues, _, sim = run(gcode(rough, fin), machine(), STOCK)
        assert [str(i) for i in issues if i.level == "error"] == [], strategy
        # verify's stock ends on the model: not below it, and close above where the ball reaches
        xs = sim.x0 + np.arange(sim.h.shape[0]) * sim.res
        sel = (xs >= 1) & (xs <= 17)
        cm = radial_map(part, x_step=sim.res, a_step=sim.a_res, x_range=(xs[sel][0], xs[sel][-1]))
        d = sim.h[sel] - cm.r
        assert d.min() > -0.05, strategy
        assert np.median(d) < 0.2, strategy


def test_bump_is_cut_at_the_right_angle(part):
    # the bump faces the tool at A = 90; at A = 270 the tool reaches down to the groove floor
    fin = ops.rotary_finish(part, BALL3, 22, strategy="a_raster", stepover=0.6, x_range=(0, 18), floor=2,
                            tolerance=1e-9)                    # every grid point, no decimation
    pts = feed_points(fin)
    xr = pts[np.argmin(np.abs(pts[:, 0] - 8.0)), 0]                 # the ring nearest X8
    ring = pts[np.abs(pts[:, 0] - xr) < 1e-9]
    a = ring[:, 1] % 360
    near = lambda ang: ring[np.abs(((a - ang + 180) % 360) - 180) < 3, 2]
    assert near(90).min() > 8.0                      # over the bump (top at radius 8.5)
    assert near(270).max() < 5.5                     # opposite side: groove floor at radius 5


def test_non_center_cutting_tool_never_descends_steeper_than_allowed(part):
    tp = ops.rotary_rough(part, EM6, 22, step_down=2.5, x_range=(0, 18), floor=2)
    for pts in feed_runs(tp):
        pts = pts[1:]                                  # from the first feed point (the entry is above material)
        run_len = np.hypot(np.diff(pts[:, 0]), np.radians(np.diff(pts[:, 1])) * np.maximum(pts[:-1, 2], 1))
        drop = -np.diff(pts[:, 2])
        assert (drop <= np.tan(np.radians(30)) * run_len + 1e-6).all()


def test_vbit_roughing_and_unknown_strategy_are_refused(part):
    with pytest.raises(ValueError, match="flat or ball"):
        ops.rotary_rough(part, Tool("v", 6, "vbit"), 22, step_down=2)
    with pytest.raises(ValueError, match="strategy"):
        ops.rotary_finish(part, BALL3, 22, strategy="spiral")


# --- helpers ----------------------------------------------------------------------

def test_decimate_keeps_shape_within_tolerance():
    a = np.linspace(0, 720, 721)
    flat = np.stack([np.linspace(0, 10, 721), a, np.full(721, 12.0)], axis=1)
    assert len(decimate(flat, 0.005)) == 2                       # one helix line
    wavy = flat.copy()
    wavy[:, 2] += 0.5 * np.sin(np.radians(a) * 3)
    kept = decimate(wavy, 0.01)
    assert 2 < len(kept) < 721
    # every dropped point lies within tolerance of the kept polyline (A as surface length)
    q = lambda p: np.stack([p[:, 0], np.radians(p[:, 1]) * 12.5, p[:, 2]], axis=1)
    kq, wq = q(kept), q(wavy)
    for p in wq:
        seg_a, seg_b = kq[:-1], kq[1:]
        v = seg_b - seg_a
        t = np.clip(((p - seg_a) * v).sum(1) / np.maximum((v * v).sum(1), 1e-18), 0, 1)
        assert np.min(np.linalg.norm(seg_a + t[:, None] * v - p, axis=1)) <= 0.0101


def test_place_centres_and_turns_the_model():
    box = trimesh.creation.box(extents=(10, 4, 2))
    box.apply_translation((50, 30, 20))
    m = place(box, rotate_x=90, center_yz=True, x_start=0)
    assert np.allclose(m.bounds, [[0, -1, -2], [10, 1, 2]])


def test_continue_a_shifts_whole_turns_only():
    a = ops.rotary_finish(revolve([(0, 5), (4, 5)], 60), BALL3, 22, x_range=(0, 4), resolution=0.5)
    b = ops.rotary_finish(revolve([(0, 5), (4, 5)], 60), BALL3, 22, x_range=(0, 4), resolution=0.5)
    end = feed_points(a)[-1, 1]
    first_b = [m.a for m in b.moves if m.a is not None][0]
    continue_a([a, b], {id(a), id(b)})
    shifted = [m.a for m in b.moves if m.a is not None][0]
    assert (shifted - first_b) % 360 == pytest.approx(0) and abs(shifted - end) <= 180


# --- the example ------------------------------------------------------------------

def test_pawn_example_matches_its_fixture_and_verifies_clean():
    job, base = load_job(EX / "pawn_job.json")
    g = job_post(job).emit(run_job(job, base))
    assert g == (EX / "pawn.ngc").read_text()
    from camcore.verify import machine_from_library
    m = machine_from_library(Machine.load(EX.parent / "machine" / "printnc_provisional.json"), EX / "tools.json")
    issues, _, sim = run(g, m, job["stock"])
    assert [str(i) for i in issues] == []
    assert sim.h.min() < 5.5                                     # the neck was really cut
