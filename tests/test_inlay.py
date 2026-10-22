"""Inlays: mirror shape, pocket and plug depths, validation, and the fit check."""
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box

from camcore import ops
from camcore.check import check
from camcore.inlay_fit import fit
from camcore.job import build_shape, run_job
from camcore.ops.common import offset
from camcore.ops.inlay import inlay_checks, sharp_notches
from camcore.shapes import mirror
from camcore.tools import Tool

EX = Path(__file__).parent.parent / "examples"
V60 = Tool("v60", 12.7, kind="vbit", v_angle=60, center_cutting=True)
V60_PLAIN = Tool("v60p", 12.7, kind="vbit", v_angle=60)
EM3 = Tool("em3", 3.175, center_cutting=True)
COT = 1 / math.tan(math.radians(30))
L_SHAPE = Polygon([(10, 10), (40, 10), (40, 20), (22, 20), (22, 40), (10, 40)])
SQUARE = box(10, 10, 30, 30)
P, G, M = 3.0, 0.5, 1.0                      # pocket_depth, glue_gap, surface_gap
S = P - G


def feed_points(tp):
    pts, pos = [], {"x": 0.0, "y": 0.0, "z": 10.0}
    for m in tp.moves:
        for a in "xyz":
            if getattr(m, a) is not None:
                pos[a] = float(getattr(m, a))
        if m.kind == "feed":
            pts.append((pos["x"], pos["y"], pos["z"]))
    return pts


def test_mirror_keeps_the_lower_left_corner_and_flips_each_point():
    cx, cy = 25, 25
    for axis in ("y", "x"):
        m = mirror(L_SHAPE, axis)
        assert m.bounds == pytest.approx(L_SHAPE.bounds)
        flip = (lambda x, y: (2 * cx - x, y)) if axis == "y" else (lambda x, y: (x, 2 * cy - y))
        for x, y in L_SHAPE.exterior.coords:
            fx, fy = flip(x, y)
            assert m.exterior.distance(Point(fx, fy)) < 1e-9
        assert not m.equals(L_SHAPE)
    with pytest.raises(ValueError, match="axis"):
        mirror(L_SHAPE, "z")


def test_mirror_shape_in_a_job():
    g = build_shape({"type": "mirror", "axis": "y",
                     "shape": {"type": "polygon", "points": list(L_SHAPE.exterior.coords)}}, Path("."))
    assert g.equals(mirror(L_SHAPE, "y"))


@pytest.mark.parametrize("shape", [SQUARE, L_SHAPE])
def test_pocket_is_the_flat_bottom_vcarve_to_pocket_depth(shape):
    tp = ops.inlay(shape, V60, P, part="pocket")
    pts = feed_points(tp)
    assert min(z for *_, z in pts) == pytest.approx(-P)
    for x, y, z in pts:                       # never deeper than the V allows at that point
        d = shape.boundary.distance(Point(x, y))
        assert z >= -min(P, d * COT) - 0.02
    region, depth = ops.inlay_clear_area(shape, V60, EM3, part="pocket", pocket_depth=P)
    assert depth == P
    assert region.equals(ops.vcarve_flat_bottom_area(shape, V60, P))


@pytest.mark.parametrize("shape", [SQUARE, L_SHAPE])
def test_plug_walls_start_at_s_and_the_flange_is_at_s_plus_m(shape):
    stock = {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-10, 0]}
    tp = ops.inlay(shape, V60, P, part="plug", glue_gap=G, surface_gap=M, stock=stock)
    design = mirror(shape, "y")
    pts = [p for p in feed_points(tp) if p[2] < 0]
    assert min(z for *_, z in pts) == pytest.approx(-(S + M))
    for x, y, z in pts:
        q = Point(x, y)
        assert not design.buffer(-1e-6).contains(q)              # the tip never enters the plug
        assert z >= -(S + design.distance(q) * COT) - 1e-6        # no deeper than the wall allows
        assert z >= -(S + M) - 1e-9
    r_m = M * math.tan(math.radians(30))           # the walls' foot: cut at s + m, r_m out
    assert any(abs(design.distance(Point(x, y)) - r_m) < 1e-3 and z == pytest.approx(-(S + M))
               for x, y, z in pts)
    region, depth = ops.inlay_clear_area(shape, V60, EM3, part="plug", pocket_depth=P, glue_gap=G,
                                         surface_gap=M, stock=stock)
    assert depth == pytest.approx(S + M)
    frame = box(-EM3.radius, -EM3.radius, 50 + EM3.radius, 50 + EM3.radius)
    assert region.symmetric_difference(frame.difference(offset(design, r_m))).area < 1e-6


def test_plug_with_a_margin_notes_the_trim():
    tp = ops.inlay(SQUARE, V60, P, part="plug", glue_gap=G, surface_gap=M, plug_margin=4)
    assert any("trim" in n for n in tp.notes)
    region, _ = ops.inlay_clear_area(SQUARE, V60, EM3, part="plug", pocket_depth=P, glue_gap=G,
                                     surface_gap=M, plug_margin=4)
    assert region.bounds == pytest.approx((6, 6, 34, 34))


@pytest.mark.parametrize("kw, msg", [
    (dict(glue_gap=3.0), "glue_gap must be"),
    (dict(glue_gap=-0.1), "glue_gap must be"),
    (dict(surface_gap=0), "surface_gap must be > 0"),
    (dict(pocket_depth=12), "cuts only"),
    (dict(part="plug", pocket_depth=10, glue_gap=0.5, surface_gap=2), "cuts only"),
    (dict(part="both"), "part must be"),
])
def test_settings_that_cannot_work_are_refused(kw, msg):
    args = dict(part="pocket", pocket_depth=P, glue_gap=G, surface_gap=M) | kw
    with pytest.raises(ValueError, match=msg):
        inlay_checks(V60, **args)


def test_medial_needs_a_centre_cutting_bit_and_plug_needs_room():
    with pytest.raises(ValueError, match="center_cutting"):
        ops.inlay(SQUARE, V60_PLAIN, P)
    with pytest.raises(ValueError, match="do not fit inside the plug stock"):
        ops.inlay(SQUARE, V60, P, part="plug", stock={"type": "box", "x": [10, 30], "y": [10, 30], "z": [-9, 0]})
    with pytest.raises(ValueError, match="box stock"):
        ops.inlay(SQUARE, V60, P, part="plug")


def test_sharp_notches_finds_the_tip_of_a_cusp_and_runs_out_along_it():
    # a notch of 20 degrees cut into the top of a square, tip at (20, 20)
    w = 10 * math.tan(math.radians(10))
    notched = box(10, 10, 30, 30).difference(Polygon([(20, 20), (20 + w, 30.1), (20 - w, 30.1)]))
    found = sharp_notches(notched)
    assert len(found) == 1
    (x, y), path = found[0]
    assert (x, y) == pytest.approx((20, 20), abs=1e-6)
    assert path and path[0][0] == pytest.approx(20, abs=1e-6) and path[0][1] > 20
    assert sharp_notches(L_SHAPE) == []          # a 90 degree inside corner is carved normally


def write_jobs(tmp_path, plug_tool="v60", pocket_depth=P, shape=None):
    shape = shape or {"type": "polygon", "points": list(L_SHAPE.exterior.coords)}
    common = {"post": "linuxcnc", "tool_library": str(EX / "tools.json"), "machine": str(EX / "machine.json"),
              "material": "hardwood", "shapes": {"art": shape}}
    pocket = {**common, "stock": {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-18, 0]},
              "operations": [{"op": "inlay", "part": "pocket", "shape": "art", "tool": "v60",
                              "clear_tool": "em3", "pocket_depth": pocket_depth, "glue_gap": G,
                              "surface_gap": M}]}
    plug = {**common, "stock": {"type": "box", "x": [5, 45], "y": [5, 45], "z": [-12, 0]},
            "operations": [{"op": "inlay", "part": "plug", "shape": "art", "tool": plug_tool,
                            "clear_tool": "em3", "pocket_depth": P, "glue_gap": G, "surface_gap": M,
                            "fit_with": "pocket_job.json"}]}
    (tmp_path / "pocket_job.json").write_text(json.dumps(pocket))
    (tmp_path / "plug_job.json").write_text(json.dumps(plug))
    return tmp_path / "pocket_job.json", tmp_path / "plug_job.json"


def test_both_parts_verify_clean_and_fit(tmp_path):
    pocket, plug = write_jobs(tmp_path)
    r = fit(pocket, plug)
    assert r.ok, r.lines()
    assert r.bottom_gap == pytest.approx(G, abs=0.1)
    assert r.flange_gap == pytest.approx(M, abs=0.1)
    assert abs(r.proud) < 0.1
    assert r.wall_contact > 0.9


def test_a_plug_cut_with_another_bit_does_not_fit(tmp_path):
    r = fit(*write_jobs(tmp_path, plug_tool="v90"))
    # its face edge seats on the pocket's rim at the right height, but the walls gape
    assert not r.ok
    assert abs(r.proud) < 0.05 and r.wall_contact < 0.1
    assert any("walls stand apart" in p for p in r.problems)


def test_a_pocket_cut_to_another_depth_does_not_fit(tmp_path):
    r = fit(*write_jobs(tmp_path, pocket_depth=P + 0.5))
    assert not r.ok
    assert r.bottom_gap == pytest.approx(G + 0.5, abs=0.05)


def test_check_runs_the_fit_for_a_plug_job(tmp_path):
    _, plug = write_jobs(tmp_path)
    out = tmp_path / "out"
    assert check(plug, out, sim=False, log=lambda s: None)
    report = (out / "plug_report.txt").read_text()
    assert "inlay fit: OK" in report
    _, plug = write_jobs(tmp_path, plug_tool="v90")
    assert not check(plug, out, sim=False, log=lambda s: None)
    assert "inlay fit: FAILED" in (out / "plug_report.txt").read_text()
    assert not (out / "plug.ngc").exists()


def test_inlay_needs_a_clear_tool():
    job = {"tool_library": str(EX / "tools.json"), "stock": {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-9, 0]},
           "shapes": {"a": {"type": "rect", "x": 10, "y": 10, "w": 20, "h": 20}},
           "operations": [{"op": "inlay", "shape": "a", "tool": "v60", "pocket_depth": 3}]}
    with pytest.raises(ValueError, match="clear_tool"):
        run_job(job, EX)


def test_a_clear_pass_with_nothing_the_clear_tool_fits_is_left_out(tmp_path):
    # 8 mm slot, 5 mm deep: the V-bit leaves a flat strip 8 - 2 * 5 * tan 30 = 2.2 mm
    # wide, narrower than em3. The empty pass used to put out a tool change and a
    # Z move with no X Y, which verify refuses.
    job = {"tool_library": str(EX / "tools.json"), "stock": {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-9, 0]},
           "shapes": {"a": {"type": "rect", "x": 10, "y": 10, "w": 8, "h": 30}},
           "operations": [{"op": "inlay", "shape": "a", "tool": "v60", "clear_tool": "em3", "pocket_depth": 5}]}
    region, _ = ops.inlay_clear_area(build_shape(job["shapes"]["a"], EX), V60, EM3, part="pocket", pocket_depth=5)
    assert not region.is_empty
    paths = run_job(job, EX)
    assert [tp.name for tp in paths] == ["inlay"]
    assert any("em3" in n and "nothing to clear" in n for n in paths[0].notes)
    path = tmp_path / "slot_job.json"
    path.write_text(json.dumps(job))
    assert check(path, tmp_path / "out", sim=False, log=lambda s: None)


@pytest.mark.parametrize("part", ["pocket", "plug"])
def test_example_fixtures_regenerate_and_verify_clean(part):
    from camcore.job import job_to_gcode
    from camcore.verify import Machine, machine_from_library, run
    job = EX / f"inlay_{part}_job.json"
    gcode = job_to_gcode(job)
    assert gcode == (EX / f"inlay_{part}.ngc").read_text()
    m = machine_from_library(Machine.load(EX / "machine.json"), EX / "tools.json")
    issues, _, _ = run(gcode, m, json.loads(job.read_text())["stock"])
    assert [str(i) for i in issues] == []


def test_example_inlay_fits():
    r = fit(EX / "inlay_pocket_job.json", EX / "inlay_plug_job.json")
    assert r.ok, r.lines()
    assert r.wall_contact > 0.9
