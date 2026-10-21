"""3-axis 3D from a mesh: OpenCAMLib drop-cutter and waterline, roughing and finishing (guide step 11)."""
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest
import trimesh

from camcore import ops, surface
from camcore.job import load_job, run_job
from camcore.ops.surface3d import decimate, limit_descent, raster_lines
from camcore.post import get_post, job_post
from camcore.tools import Tool
from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
EM6 = Tool("em6", 6.0, "flat", flute_length=22, number=2)
EM6_CC = Tool("em6", 6.0, "flat", flute_length=22, number=2, center_cutting=True)
BALL3 = Tool("ball3", 3.0, "ball", flute_length=12, number=3, center_cutting=True)
BULL6 = Tool("bull6", 6.0, "bull", corner_radius=1.0, flute_length=20, number=6, center_cutting=True)
V90 = Tool("v90", 6.0, "vbit", v_angle=90, number=5, center_cutting=True)
STOCK = {"type": "box", "x": [0, 34], "y": [0, 24], "z": [-12, 0]}


def machine(*tools):
    t = {x.number: {"diameter": x.diameter, "kind": x.kind, "flute_length": x.flute_length,
                    "center_cutting": x.center_cutting, "corner_radius": x.corner_radius} for x in tools}
    return Machine(travel={"x": [-500, 500], "y": [-500, 500], "z": [-500, 500]},
                   work_offset={"x": 0, "y": 0, "z": 0}, has_a=False, tools=t)


def sphere_cap(centre, r, z_cut, rings=40, sections=96):
    """The part of a sphere above Z = z_cut, as an open surface."""
    cx, cy, cz = centre
    top = math.acos(max(-1.0, min(1.0, (z_cut - cz) / r)))     # polar angle of the cut
    v = [(cx, cy, cz + r)]
    for i in range(1, rings + 1):
        ph = top * i / rings
        v += [(cx + r * math.sin(ph) * math.cos(th), cy + r * math.sin(ph) * math.sin(th), cz + r * math.cos(ph))
              for th in np.linspace(0, 2 * math.pi, sections, endpoint=False)]
    f = [(0, 1 + j, 1 + (j + 1) % sections) for j in range(sections)]
    for i in range(rings - 1):
        a, b = 1 + i * sections, 1 + (i + 1) * sections
        for j in range(sections):
            k = (j + 1) % sections
            f += [(a + j, b + j, b + k), (a + j, b + k, a + k)]
    return trimesh.Trimesh(np.array(v), np.array(f), process=False)


@pytest.fixture(scope="module")
def part():
    """A plate (top Z-8) with a dome and a sharp-edged round boss (top Z-2) on it."""
    plate = trimesh.creation.box(extents=(26, 16, 3))
    plate.apply_translation((17, 12, -9.5))
    dome = sphere_cap((11, 12, -9), 5.0, -8.0)
    boss = trimesh.creation.cylinder(radius=3, height=7, sections=64)
    boss.apply_translation((24, 12, -5.5))
    return trimesh.util.concatenate([plate, dome, boss])


@pytest.fixture(scope="module")
def samples(part):
    """Dense points on the model surface (and its vertices) for brute-force checks."""
    pts, _ = trimesh.sample.sample_surface(part, int(part.area / 0.08 ** 2), seed=1)
    return np.vstack([pts, part.vertices])


def feed_points(tp, step=0.05):
    """Tool tip positions along the feed moves, every `step` mm."""
    out, pos = [], np.zeros(3)
    for m in tp.moves:
        new = np.array([v if v is not None else pos[k] for k, v in enumerate((m.x, m.y, m.z))])
        if m.kind == "feed":
            n = max(1, math.ceil(np.linalg.norm(new - pos) / step))
            out.append(pos + (new - pos) * (np.arange(1, n + 1) / n)[:, None])
        pos = new
    return np.vstack(out)


def feed_runs(tp):
    """Feed moves between rapids, as (n, 3) arrays of their end points (with the start)."""
    runs, pos, cur = [], np.zeros(3), None
    for m in tp.moves:
        new = np.array([v if v is not None else pos[k] for k, v in enumerate((m.x, m.y, m.z))])
        if m.kind == "feed":
            if cur is None:
                cur = [pos]
                runs.append(cur)
            cur.append(new)
        else:
            cur = None
        pos = new
    return [np.array(r) for r in runs]


def worst_depth(tp, pts, tool):
    """Deepest a model point gets inside the cutter body (shank straight up), by brute force.
    Measured along the surface normal, not vertically: on a steep wall a tiny sideways
    error is a large vertical one."""
    R = tool.radius
    bins = defaultdict(list)
    for i, k in enumerate(map(tuple, np.floor(pts[:, :2] / R).astype(int))):
        bins[k].append(i)
    bins = {k: np.array(v) for k, v in bins.items()}
    worst = -np.inf
    for x, y, z in feed_points(tp):
        kx, ky = int(math.floor(x / R)), int(math.floor(y / R))
        idx = [bins[(kx + a, ky + b)] for a in (-1, 0, 1) for b in (-1, 0, 1) if (kx + a, ky + b) in bins]
        if not idx:
            continue
        q = pts[np.concatenate(idx)]
        dxy = np.hypot(q[:, 0] - x, q[:, 1] - y)
        if tool.kind == "ball":
            c = z + R
            dep = np.where(q[:, 2] >= c, R - dxy, R - np.hypot(dxy, q[:, 2] - c))
        elif tool.kind == "flat":
            dep = np.where(q[:, 2] >= z, np.minimum(R - dxy, q[:, 2] - z), -np.inf)
        else:
            raise ValueError(tool.kind)
        dep = np.where(dxy <= R, dep, -np.inf)
        worst = max(worst, float(dep.max()))
    return worst


def gcode(*paths):
    return get_post("linuxcnc").emit(list(paths))


def errors(issues):
    return [str(i) for i in issues if i.level == "error"]


# --- tools ----------------------------------------------------------------------

def test_bull_nose_profile():
    assert BULL6.profile(0) == 0 and BULL6.profile(2.0) == 0
    assert BULL6.profile(2.5) == pytest.approx(1 - math.sqrt(0.75), abs=1e-12)
    assert BULL6.profile(3.0) == pytest.approx(1.0, abs=1e-12)
    assert BULL6.radius_at_depth(0) == 2.0 and BULL6.radius_at_depth(1.0) == 3.0
    assert BULL6.radius_at_depth(0.5) == pytest.approx(2 + math.sqrt(0.75), abs=1e-12)
    with pytest.raises(ValueError, match="corner_radius"):
        Tool("b", 6, "bull")
    with pytest.raises(ValueError, match="corner_radius"):
        Tool("b", 6, "bull", corner_radius=3.5)


# --- drop-cutter and waterline (OpenCAMLib) --------------------------------------

@pytest.fixture(scope="module")
def block():
    b = trimesh.creation.box(extents=(10, 10, 5))
    b.apply_translation((5, 5, -2.5))                  # top Z0 over X/Y 0..10
    return b


@pytest.mark.parametrize("tool, x, z", [
    (EM6, 12.0, 0.0),                                  # flat: the edge still under the cutter
    (EM6, 13.5, -20.0),                                # flat: clear of the block, down to the floor
    (BALL3, 11.0, -(1.5 - math.sqrt(1.5 ** 2 - 1))),   # ball: edge 1 mm from the axis
    (BULL6, 12.5, -(1 - math.sqrt(0.75))),             # bull: edge 0.5 mm into the corner radius
    (BULL6, 11.5, 0.0),                                # bull: edge under the flat bottom
    (V90, 11.0, -1.0),                                 # 90 deg V: h(d) = d
])
def test_drop_cutter_is_exact_at_a_block_edge(block, tool, x, z):
    p = surface.drop_path(block, tool, [(x, 5), (x, 5.5)], -20.0, 1.0)
    assert p[:, 2] == pytest.approx([z, z], abs=1e-9)


def test_drop_with_leave_keeps_the_offset_in_every_direction(block):
    assert surface.drop_path(block, BALL3, [(5, 5), (6, 5)], -20, 1, leave=0.5)[:, 2] == pytest.approx([0.5, 0.5])
    # beside the edge the grown ball (radius 2) touches the corner: 1 mm out, sqrt(4 - 1) - 1.5 below
    z = surface.drop_path(block, BALL3, [(11, 5), (11, 6)], -20, 1, leave=0.5)[0, 2]
    assert z == pytest.approx(math.sqrt(3) - 2 + 0.5, abs=1e-9)


def test_drop_path_refines_where_the_straight_move_would_cut(block):
    # crossing the edge at X10 the ball rises along an arc that 0.5 mm samples miss
    coarse = surface.drop_path(block, BALL3, [(8, 5), (14, 5)], -20, 0.5)
    fine = surface.drop_path(block, BALL3, [(8, 5), (14, 5)], -20, 0.5, tolerance=0.002)
    assert len(fine) > len(coarse)
    xs = np.linspace(8, 14, 3001)
    need = surface.drop_points(block, BALL3, np.column_stack([xs, np.full_like(xs, 5)]), -20)
    assert (np.interp(xs, coarse[:, 0], coarse[:, 2]) - need).min() < -0.05
    assert (np.interp(xs, fine[:, 0], fine[:, 2]) - need).min() > -0.0025


def test_waterline_of_a_block_is_the_offset_square(block):
    loops = surface.waterline(block, EM6, -1.0, 0.1)
    assert len(loops) == 1
    lp = loops[0]
    assert lp[:, 0].min() == pytest.approx(-3, abs=1e-6) and lp[:, 0].max() == pytest.approx(13, abs=1e-6)
    assert lp[:, 1].min() == pytest.approx(-3, abs=1e-6) and lp[:, 1].max() == pytest.approx(13, abs=1e-6)
    # every point is exactly the cutter radius off the block (rounded corners)
    d = np.hypot(np.clip(lp[:, 0], 0, 10) - lp[:, 0], np.clip(lp[:, 1], 0, 10) - lp[:, 1])
    assert d == pytest.approx(np.full(len(lp), 3.0), abs=1e-6)


@pytest.fixture(scope="module")
def boss():
    b = trimesh.creation.cylinder(radius=3, height=7, sections=64)     # vertical side facets
    b.apply_translation((24, 12, -5.5))
    return b


@pytest.mark.parametrize("tool", [BALL3, EM6, BULL6])
@pytest.mark.parametrize("z", [-4.0, -6.0, -8.5])
def test_waterline_beside_vertical_facets_has_no_spikes(boss, tool, z):
    # OpenCAMLib's XY inside-facet test divides by zero on vertical facets; unfiltered,
    # these contours had points up to 12 mm off the boss
    for sampling in (0.05, 0.1, 0.13):
        (loop,) = surface.waterline(boss, tool, z, sampling)
        r = np.hypot(loop[:, 0] - 24, loop[:, 1] - 12)
        assert r.max() <= 3 + tool.radius + 1e-6
        assert r.min() >= 3 * math.cos(math.pi / 64) + tool.radius - 1e-6     # the polygon's flats


def test_contour_check_refuses_points_off_the_contact(boss):
    (loop,) = surface.waterline(boss, EM6, -6.0, 0.1)
    surface._check_loop(boss, EM6, loop, -6.0, 0.0)                  # the real contour passes
    spike = loop.copy()
    spike[5] += (0, -6.0)                                          # out in the open
    with pytest.raises(RuntimeError, match="off the model contact"):
        surface._check_loop(boss, EM6, spike, -6.0, 0.0)
    inside = loop.copy()
    inside[5] = (24 + 4.0, 12)                                     # 2 mm into the boss's no-go zone
    with pytest.raises(RuntimeError, match="off the model contact"):
        surface._check_loop(boss, EM6, inside, -6.0, 0.0)


S = 0.25        # shrink


@pytest.mark.parametrize("tool, x, z", [
    (EM6, 12.5, -S),                                         # flat: edge still under the narrower bottom
    (EM6, 12.9, -20.0),                                      # flat: 2.9 > 2.75, falls past the edge
    (BALL3, 11.0, math.sqrt(1.25 ** 2 - 1) - 1.5),           # ball radius 1.25, same centre
    (BULL6, 12.5, math.sqrt(0.75 ** 2 - 0.5 ** 2) - 0.75 - S),   # corner 0.75 around radius 2.0
    (Tool("bull6", 6.0, "bull", corner_radius=0.2, number=6), 12.5, -S),   # corner gone: a flat 5.5
    (V90, 11.0, -1.0 - S / math.sin(math.radians(45))),     # cone side moved in S: point S/sin45 lower
])
def test_shrunk_cutter_is_the_inner_offset(block, tool, x, z):
    # the real-tip height of the cutter shrunk by S (every surface point moved S inwards)
    assert surface.drop_points(block, tool, [(x, 5)], -20.0, -S)[0] == pytest.approx(z, abs=1e-9)
    assert surface.drop_points(block, tool, [(5, 5)], -20.0, -S)[0] == pytest.approx(-S if tool.kind != "vbit"
                                                                                    else -S / math.sin(math.pi / 4))


def test_contour_check_accepts_a_hairpin_tip():
    # A V valley that gets shallower along X: the flat end mill fits into it at Z-3 up
    # to X29.85, so the contour runs in and back out as a hairpin whose sides are 5.7 deg
    # apart. OpenCAMLib can put two points right at the tip; the check used to probe
    # 0.01 mm across the contour, and with the tip's neighbour so close, "across" points
    # sideways into the valley walls on both sides: a correct contour was refused.
    from camcore.relief.relief3d import block_mesh

    xs, ys = np.arange(0, 40.01, 0.5), np.arange(0, 30.01, 0.5)
    X, Y = np.meshgrid(xs, ys)
    valley = block_mesh(xs, ys, np.minimum(0.0, -6 + 0.05 * X + 0.5 * np.abs(Y - 15)), -10)
    loops = surface.waterline(valley, EM6, -3.0, 0.1)
    (loop,) = [lp for lp in loops if ((lp[:, 0] > 29) & (np.abs(lp[:, 1] - 15) < 0.1)).any()]
    in_valley = (np.abs(loop[:, 1] - 15) < 1) & (loop[:, 0] > 1) & (loop[:, 0] < 35)
    i = int(np.argmax(np.where(in_valley, loop[:, 0], -np.inf)))
    tip, nxt = loop[i], loop[(i + 1) % len(loop)]
    assert tip == pytest.approx((29.85, 15), abs=0.01)
    twin = tip + 0.004 * (nxt - tip) / np.hypot(*(nxt - tip))
    pair = np.insert(loop, i + 1, twin, axis=0)
    t = pair[i + 1] - pair[i - 1]
    n = np.array([t[1], -t[0]]) / np.hypot(*t)
    across = surface.drop_points(valley, EM6, [tip + 0.01 * n, tip - 0.01 * n], -4.0)
    assert (across > -3 + 1e-6).all()                       # the old rule: no free side, refused
    surface._check_loop(valley, EM6, pair, -3.0, 0.0)       # passes now
    # The tolerance is how far the cutter is off touching the model, in 3D (the gouge it
    # would make), not sideways: the valley floor rises 0.05 mm per mm, so 0.05 mm further
    # in the cutter is only 0.0025 mm inside the model; 0.5 mm further, or 0.05 mm into a
    # side wall (rising 0.5 mm per mm), it is 0.025 mm inside.
    for push, ok in (((0.05, 0), True), ((0.5, 0), False), ((0, 0.05), False)):
        moved = pair.copy()
        moved[i] += push
        if ok:
            surface._check_loop(valley, EM6, moved, -3.0, 0.0)
        else:
            with pytest.raises(RuntimeError, match="off the model contact"):
                surface._check_loop(valley, EM6, moved, -3.0, 0.0)


def test_decimate_compares_at_the_same_xy():
    # a steep flank: dropping the middle point is a 1 mm Z error at its XY, although
    # the point is only ~0.1 mm from the straight move's line
    pts = np.array([[0, 0, 0], [1, 0, -1], [1.1, 0, -10]], dtype=float)
    assert len(decimate(pts, 0.2)) == 3
    line = np.column_stack([np.linspace(0, 10, 11), np.zeros(11), np.linspace(0, -5, 11)])
    assert decimate(line, 1e-6).tolist() == [line[0].tolist(), line[-1].tolist()]
    # one-sided: a point 0.004 above the straight move may not be dropped, one below may
    bump = np.array([[0, 0, 0], [1, 0, 0.004], [2, 0, 0]], dtype=float)
    assert len(decimate(bump, 0.005)) == 2 and len(decimate(bump, 0.005, below=False)) == 3
    dip = np.array([[0, 0, 0], [1, 0, -0.004], [2, 0, 0]], dtype=float)
    assert len(decimate(dip, 0.005, below=False)) == 2
    # travelling +X with the wall on the right (-Y): a point on the free side (+Y) stays
    free = np.array([[0, 0, 0], [1, 0.004, 0], [2, 0, 0]], dtype=float)
    wall = np.array([[0, 0, 0], [1, -0.004, 0], [2, 0, 0]], dtype=float)
    assert len(decimate(free, 0.005, left=False)) == 3 and len(decimate(wall, 0.005, left=False)) == 2


def test_limit_descent_only_raises():
    pts = np.array([[0, 0, 0], [1, 0, -5], [2, 0, -5], [12, 0, -5]], dtype=float)
    out = limit_descent(pts, 45)
    assert out[:, 2] == pytest.approx([0, -1, -2, -5])
    assert (out[:, 2] >= pts[:, 2]).all()


def test_raster_lines_cover_the_region_zigzag():
    from shapely.geometry import box
    lines = raster_lines(box(0, 0, 10, 4), 1.0, 0.0)
    assert [ln[0][1] for ln in lines] == pytest.approx([0, 1, 2, 3, 4])
    assert lines[0][0][0] < lines[0][-1][0] and lines[1][0][0] > lines[1][-1][0]
    turned = raster_lines(box(0, 0, 10, 10), 2.0, 90.0)
    assert all(abs(ln[0][0] - ln[-1][0]) < 1e-9 for ln in turned)           # along Y


# --- operations ----------------------------------------------------------------

def test_rough_keeps_the_stock_to_leave_and_verifies(part, samples):
    tp = ops.surface_rough(part, EM6_CC, STOCK, step_down=3.0, stock_to_leave=0.5)
    assert worst_depth(tp, samples, EM6_CC) <= -0.49
    z = feed_points(tp)[:, 2]
    assert z.min() == pytest.approx(-11.0 + 0.5)                           # floor: model bottom + leave
    assert any(abs(v + 3.0) < 1e-9 for v in z) and any(abs(v + 9.0) < 1e-9 for v in z)
    issues, _, sim = run(gcode(tp), machine(EM6_CC), STOCK)
    assert errors(issues) == []
    # the plate top (Z-8) stays covered, the open floor is cleared down to Z-10.5
    xs = sim.x0 + np.arange(sim.h.shape[0]) * sim.res
    ys = sim.y0 + np.arange(sim.h.shape[1]) * sim.res
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    # away from the dome and the boss by more than the cutter can reach past (the gap between
    # them is too narrow for the 6 mm cutter plus the leave and stays high)
    on_plate = ((X > 5) & (X < 29) & (Y > 5) & (Y < 19) & (np.hypot(X - 11, Y - 12) > 8.5)
                & (np.hypot(X - 24, Y - 12) > 7))
    assert sim.h[on_plate].min() >= -8.0 + 0.5 - 1e-6
    assert sim.h[on_plate].max() <= -8.0 + 0.5 + 3.0           # within one level above
    outside = (X > 1) & (X < 33) & ((Y < 2) | (Y > 22))
    assert sim.h[outside].max() == pytest.approx(-10.5)


def test_raster_finish_is_gouge_free_and_follows_the_model(part, samples):
    tp = ops.surface_finish(part, BALL3, "raster", stepover=0.5)
    # decimation never goes under a dropped point; what is left is the refinement's
    # midpoint test (0.005) between the dropped points
    assert worst_depth(tp, samples, BALL3) <= 0.0055
    pts = feed_points(tp, 0.25)
    # a strip of plate clear of the dome and the boss: the ball sits on the plate top
    strip = pts[(pts[:, 0] > 5) & (pts[:, 0] < 29) & (pts[:, 1] > 4.9) & (pts[:, 1] < 6.1)]
    assert len(strip) > 50 and strip[:, 2] == pytest.approx(np.full(len(strip), -8.0), abs=1e-6)
    top = pts[np.hypot(pts[:, 0] - 24, pts[:, 1] - 12) < 1.4]          # the ball wholly over the boss top
    assert len(top) and top[:, 2] == pytest.approx(np.full(len(top), -2.0), abs=1e-6)
    rough = ops.surface_rough(part, EM6_CC, STOCK, step_down=3.0, stock_to_leave=0.5)
    issues, _, _ = run(gcode(rough, tp), machine(EM6_CC, BALL3), STOCK)
    assert errors(issues) == []


def test_waterline_finish_climbs_around_the_boss(part, samples):
    tp = ops.surface_finish(part, BALL3, "waterline", step_down=1.0, z_range=[-7, -2],
                            boundary=[18, 6, 30, 18])
    # decimation keeps to the free side; left are the chords between contour points,
    # spaced to stay within the tolerance (0.005) where the contour bends round the boss
    assert worst_depth(tp, samples, BALL3) <= 0.005
    runs = feed_runs(tp)
    (loop,) = [r[np.abs(r[:, 2] + 5.0) < 1e-6] for r in runs if abs(r[:, 2].min() + 5.0) < 1e-6]
    r = np.hypot(loop[:, 0] - 24, loop[:, 1] - 12)
    assert r == pytest.approx(np.full(len(r), 3 + 1.5), abs=0.01)        # cutter radius off the wall
    turn = np.unwrap(np.arctan2(loop[:, 1] - 12, loop[:, 0] - 24))
    # clockwise (wall on the right), one turn; its first point may merge into the ramp
    assert -2 * math.pi - 0.01 < turn[-1] - turn[0] < -2 * math.pi + 0.15
    assert tp.notes == []


def test_non_center_cutting_raster_never_descends_steeper_than_allowed(part):
    tp = ops.surface_finish(part, EM6, "raster", stepover=2.0, boundary=[16, 4, 32, 20])
    pts = feed_points(tp, 1e9)                    # the feed moves' end points
    run_len = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    assert (-np.diff(pts[:, 2]) <= math.tan(math.radians(30)) * run_len + 1e-6).all()


def test_refused_inputs(part):
    with pytest.raises(ValueError, match="box stock"):
        ops.surface_rough(part, EM6, {"type": "cylinder", "diameter": 30, "x": [0, 30]}, 2)
    with pytest.raises(ValueError, match="flat, ball or bull"):
        ops.surface_rough(part, V90, STOCK, 2)
    with pytest.raises(ValueError, match="safe_z"):
        ops.surface_finish(part, BALL3, safe_z=-3)
    with pytest.raises(ValueError, match="strategy"):
        ops.surface_finish(part, BALL3, strategy="spiral")


# --- verify --------------------------------------------------------------------

def test_verify_cuts_a_bull_nose_profile_into_box_stock():
    stock = {"type": "box", "x": [0, 20], "y": [0, 20], "z": [-10, 0]}
    g = "G21 G90\nT6 M6\nG43 H6\nM3 S10000\nG0 X10 Y10 Z5\nG1 Z-2 F100\nG0 Z5\nM5\nM30\n"
    issues, _, sim = run(g, machine(BULL6), stock, res=0.5)
    assert errors(issues) == []
    j = int(round((10 - sim.y0) / sim.res))
    row = {round(sim.x0 + i * sim.res, 6): sim.h[i, j] for i in range(sim.h.shape[0])}
    assert row[10.0] == -2.0 and row[12.0] == -2.0                       # flat bottom out to radius 2
    assert row[12.5] == pytest.approx(-2 + 1 - math.sqrt(0.75), abs=1e-12)
    assert row[13.0] == pytest.approx(-1.0, abs=1e-12) and row[13.5] == 0.0


def test_verify_refuses_a_bull_nose_on_rotary_stock():
    g = "G21 G90\nT6 M6\nG43 H6\nM3 S10000\nG0 X5 Z20\nG1 Z8 F100\nM5\nM30\n"
    issues, _, _ = run(g, machine(BULL6), {"type": "cylinder", "diameter": 20, "x": [0, 20]})
    assert any("bull nose" in e and "rotary" in e for e in errors(issues))


# --- jobs ------------------------------------------------------------------------

def test_job_refuses_3d_ops_on_rotary_and_indexed_jobs():
    base = {"tools": {"em6": {"number": 2, "diameter": 6}}, "models": {"m": {"file": "relief.stl"}},
            "stock": {"type": "box", "x": [0, 80], "y": [0, 60], "z": [-15, 0]},
            "operations": [{"op": "surface_finish", "model": "m", "tool": "em6"}]}
    with pytest.raises(ValueError, match="wrapped"):
        run_job({**base, "rotary": {"diameter": 30}}, EX)
    with pytest.raises(ValueError, match="cannot be indexed"):
        run_job({**base, "operations": [{**base["operations"][0], "index": [0, 90]}]}, EX)


def test_model_placement_for_flat_jobs():
    from camcore.job import build_model
    m = build_model({"file": "relief.stl", "xy_start": [5, 6], "z_top": 0}, EX)
    assert m.bounds[0][:2] == pytest.approx([5, 6]) and m.bounds[1][2] == pytest.approx(0)


def test_relief_example_matches_its_fixture_and_verifies():
    job, base = load_job(EX / "relief_job.json")
    g = job_post(job).emit(run_job(job, base))
    assert g == (EX / "relief.ngc").read_text()
    m = machine_from_library(Machine.load(EX / "machine.json"), EX / "tools.json")
    issues, _, sim = run(g, m, job["stock"])
    assert [str(i) for i in issues] == []
    assert sim.h.min() < -10                       # cleared down around the block
