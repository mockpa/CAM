"""Medial-axis V-carving: the axis itself, no gouging, depth accuracy against the
ideal V-groove (measured by verify's independent stock model) and the offset
method, the depth cap, and the example job."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import Point, Polygon, box

from camcore import ops
from camcore.job import load_job, run_job
from camcore.ops.medial import Clearance, medial_axis
from camcore.post import job_post
from camcore.post.linuxcnc import LinuxCncPost
from camcore.tools import Tool
from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
V90 = Tool("v90", 12.0, kind="vbit", v_angle=90, number=5, center_cutting=True)
TOL = 0.01

SHAPES = {
    "rect": box(0, 0, 20, 8),
    "triangle": Polygon([(0, 0), (20, 0), (6, 12)]),
    "L with hole": box(0, 0, 20, 6).union(box(0, 0, 6, 15)).difference(box(2, 2, 4, 4)),
    "ellipse": affinity.scale(Point(0, 0).buffer(5, quad_segs=64), 1.0, 0.5),
    "ring": Point(0, 0).buffer(8, quad_segs=64).difference(Point(0, 0).buffer(5, quad_segs=64)),
}


def medial(shape, **kw):
    return ops.vcarve(shape, V90, method="medial", entry="plunge", **kw)


def path_points(tp, step=0.02):
    """Densely sampled tool tip positions along every feed move."""
    out, pos = [], {"x": 0.0, "y": 0.0, "z": 10.0}
    for m in tp.moves:
        new = {k: getattr(m, k) if getattr(m, k) is not None else pos[k] for k in pos}
        if m.kind == "feed":
            n = max(1, math.ceil(math.dist(pos.values(), new.values()) / step))
            t = np.arange(1, n + 1)[:, None] / n
            a, b = np.array(list(pos.values())), np.array(list(new.values()))
            out.append(a + (b - a) * t)
        pos = new
    return np.concatenate(out)


def groove_error(shape, tp, tool=V90, res=0.05, cap=None):
    """(overcut, undercut) in mm of depth against the ideal V-groove of shape,
    measured on verify's heightmap (linear G-code, so no arc fitting in between)."""
    g = LinuxCncPost(arcs=False).emit([tp])
    x0, y0, x1, y1 = shape.bounds
    stock = {"type": "box", "x": [x0 - 1, x1 + 1], "y": [y0 - 1, y1 + 1], "z": [-20, 0]}
    m = Machine.load(EX / "machine.json")
    m.tools = {tool.number: {"diameter": tool.diameter, "kind": "vbit", "v_angle": tool.v_angle,
                             "center_cutting": True}}
    issues, _, st = run(g, m, stock, res=res)
    assert [str(i) for i in issues if i.level == "error"] == []
    nx, ny = st.h.shape
    X, Y = np.meshgrid(st.x0 + np.arange(nx) * res, st.y0 + np.arange(ny) * res, indexing="ij")
    tan = math.tan(math.radians(tool.v_angle) / 2)
    inside = shapely.contains_xy(shape, X, Y)
    d = np.where(inside, shapely.distance(shape.boundary, shapely.points(X, Y)), 0.0)
    rl = (cap if cap is not None else tool.depth_for_radius(tool.radius)) * tan
    # beyond the cap the contour at the cap cuts an inner flank, as in the offset method
    want = np.where(d <= rl, d / tan, np.maximum((2 * rl - d) / tan, 0.0))
    err = -st.h - want
    return float(err.max()), float(-err[inside].min())


# --- the medial axis --------------------------------------------------------------

def test_medial_axis_of_a_rectangle_is_the_spine_and_four_corner_branches():
    chains = medial_axis(box(0, 0, 20, 8), TOL)
    pts = np.concatenate([c.pts for c in chains])
    r = np.concatenate([c.r for c in chains])
    spine = r > 4 - 1e-6
    assert spine.any() and r.max() == pytest.approx(4, abs=1e-9)
    assert np.allclose(pts[spine, 1], 4, atol=1e-6)
    assert pts[spine, 0].min() == pytest.approx(4, abs=1e-6) and pts[spine, 0].max() == pytest.approx(16, abs=1e-6)
    ends = [c.pts[k] for c in chains for k in (0, -1)]
    for corner in [(0, 0), (20, 0), (20, 8), (0, 8)]:      # branches run (almost) into each corner
        assert min(math.dist(corner, e) for e in ends) < 0.05


def test_medial_axis_points_carry_their_exact_clearance():
    for shape in SHAPES.values():
        clear = Clearance(shape)
        for c in medial_axis(shape, TOL):
            assert np.allclose(c.r, clear(c.pts), atol=1e-9)
            assert np.allclose(c.r, shapely.distance(shape.boundary, shapely.points(c.pts)), atol=1e-9)


def test_a_round_dot_is_one_plunge_at_its_centre():
    dot = Point(5, 5).buffer(1.5, quad_segs=64)
    tp = medial(dot)
    feeds = [m for m in tp.moves if m.kind == "feed"]
    xy = {(m.x, m.y) for m in tp.moves if m.x is not None}
    assert len(feeds) == 2 and len(xy) == 1                 # down to the top, then down to depth
    (x, y), = xy
    assert math.dist((x, y), (5, 5)) < 0.01
    assert feeds[-1].z == pytest.approx(-dot.boundary.distance(Point(x, y)), abs=1e-9)


# --- the cut ----------------------------------------------------------------------

@pytest.mark.parametrize("name", SHAPES)
def test_medial_path_never_runs_deeper_than_the_walls_allow(name):
    shape = SHAPES[name]
    p = path_points(medial(shape))
    clear = Clearance(shape)
    below = p[:, 2] < 0
    assert below.any()
    assert shapely.contains_xy(shape.buffer(1e-6), p[below, 0], p[below, 1]).all()
    tan = math.tan(math.radians(V90.v_angle) / 2)
    gouge = -p[below, 2] * tan - clear(p[below, :2])
    assert gouge.max() <= 0.002


@pytest.mark.parametrize("name", SHAPES)
def test_medial_cuts_the_ideal_groove_closer_and_shorter_than_offset(name):
    shape = SHAPES[name]
    mt, ot = medial(shape), ops.vcarve(shape, V90, step=0.1, entry="plunge")
    m_over, m_under = groove_error(shape, mt)
    o_over, o_under = groove_error(shape, ot)
    # verify samples moves every res/2 = 0.025 mm: a sharp groove bottom can show up to
    # that much too shallow on top of the tolerance (90 deg bit: depth error = width error)
    assert m_over <= 0.003
    assert m_under <= TOL + 0.025 + 0.002
    assert o_under > 2 * m_under
    assert len(mt.moves) * 3 < len(ot.moves)


def test_medial_with_a_cap_cuts_the_contour_at_the_cap_and_the_axis_outside_it():
    shape = box(0, 0, 30, 20)
    tp = medial(shape, max_depth=2)
    p = path_points(tp)
    assert p[:, 2].min() == pytest.approx(-2, abs=1e-9)
    at_cap = p[np.isclose(p[:, 2], -2)]
    d = shapely.distance(shape.boundary, shapely.points(at_cap[:, :2]))
    assert np.allclose(d, 2, atol=0.01)                     # the inset contour at the cap
    shallower = p[(p[:, 2] < -0.05) & (p[:, 2] > -2 + 1e-6)]
    assert len(shallower)                                   # corner branches up to the contour
    over, under = groove_error(shape, tp, cap=2)
    assert over <= 0.003 and under <= TOL + 0.025 + 0.002


def test_medial_needs_plunge_entry_and_a_centre_cutting_bit():
    with pytest.raises(ValueError, match="entry \"plunge\""):
        ops.vcarve(box(0, 0, 5, 5), V90, method="medial")
    no_cc = Tool("v", 12.0, kind="vbit", v_angle=90)
    with pytest.raises(ValueError, match="center_cutting"):
        ops.vcarve(box(0, 0, 5, 5), no_cc, method="medial", entry="plunge")
    with pytest.raises(ValueError, match="method"):
        ops.vcarve(box(0, 0, 5, 5), V90, method="voronoi", entry="plunge")


def test_medial_corners_come_out_sharp_where_offset_rounds_them():
    tri = SHAPES["triangle"]
    for tp, sharp in ((medial(tri), True), (ops.vcarve(tri, V90, step=0.1, entry="plunge"), False)):
        p = path_points(tp)
        cut = p[p[:, 2] < -1e-6]
        # how close the cone gets to each corner at the stock top (90 deg bit: radius = depth)
        tan = math.tan(math.radians(V90.v_angle) / 2)
        reach = max(float(np.min(np.hypot(*(cut[:, :2] - c).T) + cut[:, 2] * tan))
                    for c in tri.exterior.coords[:3])
        assert (reach < 0.05) == sharp


# --- the example ------------------------------------------------------------------

def test_medial_sign_example_matches_its_fixture_and_verifies_clean():
    job, base = load_job(EX / "sign_medial_job.json")
    g = job_post(job).emit(run_job(job, base))
    assert g == (EX / "sign_medial.ngc").read_text()
    m = machine_from_library(Machine.load(EX / "machine.json"), EX / "tools.json")
    issues, _, _ = run(g, m, job["stock"])
    assert [str(i) for i in issues] == []
