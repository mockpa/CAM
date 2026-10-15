"""Adaptive pocket clearing: the cut width it keeps (measured independently on
a fine grid, against the offset pocket), clearing and wall clearance, boring
again behind a narrow neck, the fallback for parts too narrow for a helix,
verify on every level, and the example job."""
import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from camcore import ops
from camcore.job import job_to_gcode, load_job, run_job
from camcore.ops.adaptive import plan
from camcore.post import get_post, job_post
from camcore.tools import Tool
from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
EM = Tool("em6", 6.0, number=1)                        # not centre-cutting: must never plunge
ISLAND = box(10, 10, 60, 50).union(box(55, 22, 80, 38)).difference(Point(35, 30).buffer(6))
NECK = box(0, 0, 30, 30).union(box(30, 12, 45, 19)).union(box(45, 0, 75, 30))   # 7 mm neck, too narrow to cut through


def feed_xyz(tp):
    pts, pos = [], {"x": 0, "y": 0, "z": 10}
    for m in tp.moves:
        for a in "xyz":
            if getattr(m, a) is not None:
                pos[a] = getattr(m, a)
        if m.kind == "feed":
            pts.append((pos["x"], pos["y"], pos["z"]))
    return pts


def cut_widths(shape, tp, z, res=0.05, ds=0.05, window=1.0):
    """Width of cut (area removed per mm of travel, averaged over `window` mm)
    along the flat feeds at level z. Every feed below the top removes material
    (one level only), so the helix's turns count as cleared air."""
    R = tp.tool.radius
    x0, y0, x1, y1 = shape.bounds
    X, Y = np.meshgrid(np.arange(x0 - R, x1 + R, res), np.arange(y0 - R, y1 + R, res), indexing="ij")
    mat = shapely.contains_xy(shape, X, Y)
    k = int(math.ceil(R / res)) + 1
    pos, area, length = {"x": 0, "y": 0, "z": 10}, [], []
    for m in tp.moves:
        new = {a: getattr(m, a) if getattr(m, a) is not None else pos[a] for a in "xyz"}
        if m.kind == "feed" and new["z"] < 0:
            flat = abs(pos["z"] - z) < 1e-9 and abs(new["z"] - z) < 1e-9
            n = max(1, int(math.dist((pos["x"], pos["y"]), (new["x"], new["y"])) / ds))
            for s in range(1, n + 1):
                px = pos["x"] + (new["x"] - pos["x"]) * s / n
                py = pos["y"] + (new["y"] - pos["y"]) * s / n
                i, j = round((px - X[0, 0]) / res), round((py - Y[0, 0]) / res)
                sl = (slice(max(0, i - k), i + k + 1), slice(max(0, j - k), j + k + 1))
                disc = np.hypot(X[sl] - px, Y[sl] - py) <= R
                if flat:
                    area.append((mat[sl] & disc).sum() * res * res)
                    length.append(math.dist((pos["x"], pos["y"]), (new["x"], new["y"])) / n)
                mat[sl] &= ~disc
        pos = new
    w = max(1, int(window / ds))
    return np.convolve(area, np.ones(w), "valid") / np.convolve(length, np.ones(w), "valid")


def test_adaptive_keeps_the_cut_width_where_offset_slots():
    width = 0.15 * EM.diameter
    adaptive = cut_widths(ISLAND, ops.pocket(ISLAND, EM, depth=4, step_down=4, stepover_ratio=0.15,
                                             method="adaptive"), -4)
    offset = cut_widths(ISLAND, ops.pocket(ISLAND, EM, depth=4, step_down=4, stepover_ratio=0.4), -4)
    assert np.percentile(adaptive, 50) == pytest.approx(width, rel=0.25)
    assert np.percentile(adaptive, 99) <= 1.2 * width
    assert adaptive.max() <= 2.2 * width             # wall pass in the corners; never a slot
    assert offset.max() == pytest.approx(EM.diameter, rel=0.02)   # the offset pocket's first loop is one


@pytest.mark.parametrize("shape", [ISLAND, NECK], ids=["island", "neck"])
def test_adaptive_clears_as_much_as_offset_and_stays_off_the_walls(shape):
    tp = ops.pocket(shape, EM, depth=3, step_down=3, stepover_ratio=0.15, method="adaptive")
    pts = feed_xyz(tp)
    assert min(shape.boundary.distance(Point(x, y)) for x, y, _ in pts) >= EM.radius - 0.01
    assert min(z for *_, z in pts) == -3.0
    reference = ops.pocket(shape, EM, depth=3, step_down=3, stepover_ratio=0.4)
    def uncut(t):
        return shape.difference(LineString([(x, y) for x, y, _ in feed_xyz(t)]).buffer(EM.radius)).area
    # only corner cusps and the neck the tool cannot enter remain, as with offset loops
    assert uncut(tp) <= uncut(reference) + 0.002 * shape.area


def test_adaptive_bores_again_behind_a_narrow_neck():
    steps, fallback, _ = plan(NECK, EM, 0.9)
    assert not fallback
    assert len([s for s in steps if s[0] == "bore"]) == 2
    for s in steps:
        if s[0] == "bore":
            assert NECK.buffer(-EM.radius).contains(Point(s[1]).buffer(s[2]))


def test_adaptive_clears_parts_too_narrow_for_a_helix_with_offset_loops():
    shape = box(0, 0, 30, 30).union(box(40, 0, 49, 30))     # the 9 mm slot leaves a 3 mm wide centre region
    tp = ops.pocket(shape, EM, depth=2, step_down=2, stepover_ratio=0.15, method="adaptive")
    assert any("too narrow for a helix" in n for n in tp.notes)
    slot = [(x, y) for x, y, z in feed_xyz(tp) if x > 35 and z == -2.0]
    assert slot and min(x for x, _ in slot) <= 43.01 and max(x for x, _ in slot) >= 45.99
    zs = [z for x, _, z in feed_xyz(tp) if x > 35]
    assert all(z >= -2.0 for z in zs) and len({round(z, 6) for z in zs}) > 2   # ramped in, not plunged


def test_adaptive_method_is_checked():
    with pytest.raises(ValueError, match="adaptive"):
        ops.pocket(ISLAND, EM, depth=2, step_down=2, method="spiral")


def test_adaptive_passes_verify_on_every_level():
    tp = ops.pocket(NECK, EM, depth=6, step_down=3, stepover_ratio=0.15, method="adaptive")
    m = Machine(tools={1: {"diameter": 6, "kind": "flat", "flute_length": 20}},
                work_offset={"x": 50, "y": 50, "z": -100})
    stock = {"type": "box", "x": [-5, 80], "y": [-5, 35], "z": [-10, 0]}
    issues, _, sim = run(get_post("linuxcnc").emit([tp]), m, stock, res=0.25)
    assert [str(i) for i in issues] == []
    def h(x, y):
        return sim.h[round((x + 5) / 0.25), round((y + 5) / 0.25)]
    for x, y in ((3, 3), (15, 15), (27, 27), (60, 15), (72, 28), (37.5, 15.5)):
        assert h(x, y) == pytest.approx(-6.0, abs=0.01), (x, y)
    assert h(-2, 15) == 0.0 and h(37.5, 5) == 0.0      # beside the pocket and beside the neck


def test_adaptive_example_is_pinned_and_verifies_clean():
    assert job_to_gcode(EX / "adaptive_job.json") == (EX / "adaptive.ngc").read_text()
    job, base = load_job(EX / "adaptive_job.json")
    m = machine_from_library(Machine.load(EX / "machine.json"), EX / "tools.json")
    issues, _, _ = run(job_post(job).emit(run_job(job, base)), m, job["stock"])
    assert [str(i) for i in issues] == []
