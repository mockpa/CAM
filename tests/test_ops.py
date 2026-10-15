import math
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box

from camcore import ops
from camcore.job import job_to_gcode
from camcore.post import get_post
from camcore.tools import Tool

EM = Tool("em6", 6.0)
VBIT = Tool("v90", 12.0, kind="vbit", v_angle=90)


def feed_xy(tp):
    """Points where the tool is cutting (at or below z=0)."""
    pts, pos = [], {"x": 0, "y": 0, "z": 10}
    for m in tp.moves:
        for a in "xyz":
            if getattr(m, a) is not None:
                pos[a] = getattr(m, a)
        if m.kind == "feed":
            pts.append((pos["x"], pos["y"], pos["z"]))
    return pts


def test_profile_outside_stays_one_radius_away():
    sq = box(0, 0, 50, 50)
    tp = ops.profile(sq, EM, depth=3, step_down=1, side="outside")
    for x, y, z in feed_xy(tp):
        assert sq.exterior.distance(Point(x, y)) == pytest.approx(3.0, abs=0.05)
    zs = sorted({round(z, 6) for *_, z in feed_xy(tp)})
    assert zs == [-3.0, -2.0, -1.0]


def test_profile_inside_and_on():
    sq = box(0, 0, 50, 50)
    for side, d in (("inside", 3.0), ("on", 0.0)):
        tp = ops.profile(sq, EM, depth=1, step_down=1, side=side)
        for x, y, _ in feed_xy(tp):
            assert sq.exterior.distance(Point(x, y)) == pytest.approx(d, abs=0.05)
            assert sq.buffer(1e-6).contains(Point(x, y))


def test_pocket_clears_whole_area_and_never_gouges_wall():
    shape = box(0, 0, 40, 25).difference(Point(20, 12.5).buffer(5))   # island in the middle
    tp = ops.pocket(shape, EM, depth=2, step_down=2, stepover_ratio=0.4)
    pts = [(x, y) for x, y, _ in feed_xy(tp)]
    for x, y in pts:
        assert shape.boundary.distance(Point(x, y)) >= 3.0 - 0.05   # tool stays inside
    swept = LineString(pts).buffer(EM.radius)
    uncut = shape.difference(swept)
    # only corner cusps a round tool cannot reach remain
    assert uncut.area < 0.02 * shape.area


def test_vcarve_depth_matches_distance_to_edge():
    sq = box(0, 0, 10, 10)
    tp = ops.vcarve(sq, VBIT, step=0.25)
    pts = feed_xy(tp)
    assert pts
    for x, y, z in pts:
        r = sq.exterior.distance(Point(x, y))
        assert -z == pytest.approx(r / math.tan(math.radians(45)), abs=0.05)
    # deepest point is near the centre, half width 5 -> depth 5 for a 90 deg bit
    assert min(z for *_, z in pts) == pytest.approx(-5.0, abs=0.3)
    # finishing (shallowest) pass runs last
    assert pts[-1][2] == pytest.approx(-0.25, abs=1e-6)


def test_vcarve_respects_bit_size_and_max_depth():
    big = box(0, 0, 100, 100)
    tp = ops.vcarve(big, VBIT, step=0.5)
    assert min(z for *_, z in feed_xy(tp)) >= -VBIT.depth_for_radius(VBIT.radius) - 1e-9
    tp = ops.vcarve(big, VBIT, step=0.5, max_depth=2)
    assert min(z for *_, z in feed_xy(tp)) >= -2 - 1e-9


def test_vcarve_rejects_flat_tool():
    with pytest.raises(ValueError):
        ops.vcarve(box(0, 0, 5, 5), EM)


def test_drill_pecks():
    tp = ops.drill([(1, 2)], EM, depth=5, peck=2)
    depths = [m.z for m in tp.moves if m.kind == "feed"]
    assert depths == [-2, -4, -5]


def test_grbl_output_is_well_formed():
    tp = ops.profile(box(0, 0, 10, 10), EM, depth=1, step_down=1)
    g = get_post("grbl").emit([tp])
    lines = g.splitlines()
    assert lines[:2] == ["G21", "G90"]
    assert any(l.startswith("M3 S") for l in lines)
    assert lines[-1] == "M30"
    assert all(l.split()[0] in {"G21", "G90", "G17", "G94", "G0", "G1", "M3", "M5", "M0", "M30", "G4"}
               or l.startswith("(") for l in lines)


def test_grbl_pauses_on_tool_change():
    a = ops.profile(box(0, 0, 10, 10), EM, depth=1, step_down=1)
    b = ops.vcarve(box(0, 0, 10, 10), VBIT, step=1)
    g = get_post("grbl").emit([a, b])
    assert g.count("M0") == 1


def test_example_job_runs():
    g = job_to_gcode(Path(__file__).parent.parent / "examples" / "sign_job.json")
    assert g.count("\n") > 1000


def test_flat_bottom_vcarve_clears_the_middle(tmp_path):
    import json
    job = {
        "tools": {"em": {"diameter": 3}, "v": {"diameter": 12.7, "kind": "vbit", "v_angle": 60}},
        "shapes": {"s": {"type": "rect", "x": 0, "y": 0, "w": 60, "h": 30}},
        "operations": [{"op": "vcarve", "shape": "s", "tool": "v", "step": 0.3,
                        "max_depth": 2, "clear_tool": "em"}],
    }
    f = tmp_path / "j.json"
    f.write_text(json.dumps(job))
    from camcore.job import load_job, run_job
    paths = run_job(*load_job(f))
    assert [p.name for p in paths] == ["vcarve-clear", "vcarve"]
    clear = feed_xy(paths[0])
    assert {round(z, 6) for *_, z in clear} == {-2.0}
    assert LineString([(x, y) for x, y, _ in clear]).distance(Point(30, 15)) < 1.5   # centre gets cut


def test_linuxcnc_tool_changes_and_offsets():
    a = ops.profile(box(0, 0, 10, 10), EM, depth=1, step_down=1)
    b = ops.vcarve(box(0, 0, 10, 10), VBIT, step=1)
    c = ops.drill([(5, 5)], EM, depth=2)
    lines = get_post("linuxcnc").emit([a, b, c]).splitlines()
    assert lines[0] == "%" and lines[-1] == "%" and "M2" in lines
    changes = [l.split(" (")[0] for l in lines if " M6" in l]
    assert changes == ["T1 M6", "T2 M6", "T1 M6"]
    assert "G43 H2" in lines


def test_linuxcnc_emits_a_axis_and_grbl_refuses_it():
    from camcore.toolpath import Toolpath
    tp = Toolpath("wrap", EM)
    tp.rapid(z=5)
    tp.feed(x=10, a=90, f=500)
    assert "G1 X10 A90" in get_post("linuxcnc").emit([tp])
    with pytest.raises(ValueError):
        get_post("grbl").emit([tp])


def test_explicit_tool_number_is_used():
    t = Tool("em6", 6.0, number=7)
    g = get_post("linuxcnc").emit([ops.drill([(0, 0)], t, depth=1)])
    assert "T7 M6" in g and "G43 H7" in g


def test_atc_prefetches_next_tool():
    a = ops.profile(box(0, 0, 10, 10), EM, depth=1, step_down=1)
    b = ops.vcarve(box(0, 0, 10, 10), VBIT, step=1)
    lines = get_post("linuxcnc").emit([a, a, b]).splitlines()
    i = lines.index("G43 H1")
    assert lines[i + 1].startswith("T2 ")              # stage the V-bit while T1 cuts
    assert sum(" M6" in l for l in lines) == 2


def test_wrap_maps_y_to_a_and_depth_to_radius():
    flat = ops.profile(box(0, 0, 10, math.pi * 20 / 4), EM, depth=1, step_down=1, side="on")
    w = ops.wrap(flat, diameter=20, safe_z=5)
    assert all(m.y is None for m in w.moves)
    a_vals = [m.a for m in w.moves if m.a is not None]
    assert max(a_vals) == pytest.approx(90.0)         # quarter of circumference -> 90 deg
    cut_z = {round(m.z, 6) for m in w.moves if m.kind == "feed" and m.z is not None}
    assert cut_z == {9.0}                              # radius 10 minus 1 mm depth
    g = get_post("linuxcnc").emit([w])
    assert "A90" in g and "G93" in g and g.index("G93") < g.index("A90") < g.rindex("G94\n")
    # 1/4 turn at r=9 mm and 1000 mm/min: 14.137 mm -> F = 1000 / 14.137 = 70.74
    feeds = [float(l.split("F")[1]) for l in g.splitlines() if l.startswith("G1") and "A90" in l]
    assert feeds[0] == pytest.approx(1000 / (9 * math.pi / 2), rel=1e-3)


def test_tool_library_and_grouping(tmp_path):
    import json
    from camcore.job import load_job, run_job
    (tmp_path / "lib.json").write_text(json.dumps({
        "a": {"number": 11, "diameter": 3}, "b": {"number": 12, "diameter": 6}}))
    job = {"tool_library": "lib.json", "tool_order": "minimize_changes",
           "shapes": {"s": {"type": "rect", "x": 0, "y": 0, "w": 20, "h": 20}},
           "operations": [{"op": "profile", "shape": "s", "tool": t, "depth": 1, "step_down": 1}
                          for t in ("a", "b", "a")]}
    (tmp_path / "j.json").write_text(json.dumps(job))
    paths = run_job(*load_job(tmp_path / "j.json"))
    assert [p.tool.name for p in paths] == ["a", "a", "b"]
    g = get_post("linuxcnc").emit(paths)
    assert "T11 M6" in g and "T12 M6" in g and g.count(" M6") == 2


def test_wrap_example_runs():
    from camcore.job import job_to_gcode
    g = job_to_gcode(Path(__file__).parent.parent / "examples" / "wrap_job.json")
    assert " A" in g and " Y" not in g
