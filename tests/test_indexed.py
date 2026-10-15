"""Indexed 3+1: 2.5D / V-carve ops on faces turned up by fixed A angles."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from camcore import ops
from camcore.job import load_job, run_job
from camcore.ops.rotary import index_angles, stock_reach, stock_top
from camcore.post import job_post
from camcore.toolpath import Toolpath
from camcore.tools import Tool
from camcore.verify import Machine, Stock, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
BAR = {"type": "bar", "x": [0, 40], "y": [-10, 10], "z": [-10, 10]}
D3 = {"number": 1, "diameter": 3, "kind": "flat", "flute_length": 15, "center_cutting": True}
V60 = {"number": 4, "diameter": 12.7, "kind": "vbit", "v_angle": 60, "center_cutting": True}
TOOLS = {1: {"diameter": 6, "kind": "flat", "flute_length": 30, "center_cutting": True}}


def machine(tools=TOOLS):
    # G53 Z0, where tool changes happen, is 100 mm above the rotary axis
    return Machine(travel={"x": [-500, 500], "y": [-500, 500], "z": [-500, 500]},
                   work_offset={"x": 0, "y": 0, "z": -100}, has_a=True, tools=tools)


def errors(issues):
    return [str(i) for i in issues if i.level == "error"]


def ray(sim, x, psi):
    """Radius of verify's stock along the part ray nearest angle psi (deg), at X = x."""
    return sim.h[int(round((x - sim.x0) / sim.res)), int(round(psi / sim.a_res)) % sim.h.shape[1]]


def cell_psi(sim, psi):
    """Angle of the grid ray nearest psi."""
    return round(psi / sim.a_res) * sim.a_res


# ---------- job format ----------

def test_index_angles_forms():
    assert index_angles(90) == [90.0]
    assert index_angles([0, 180]) == [0.0, 180.0]
    assert index_angles({"angles": [45]}) == [45.0]
    assert index_angles({"sides": 4}) == [0.0, 90.0, 180.0, 270.0]
    assert index_angles({"sides": 3, "start": 30}) == [30.0, 150.0, 270.0]
    for bad in ([], {"sides": 0}, {"sides": 2.5}, "top", True, [0, "x"]):
        with pytest.raises(ValueError):
            index_angles(bad)


def test_stock_top_and_reach():
    assert stock_top({"type": "cylinder", "diameter": 40}, 37) == 20
    assert stock_top(BAR, 0) == pytest.approx(10)
    assert stock_top(BAR, 90) == pytest.approx(10)
    assert stock_top(BAR, 45) == pytest.approx(10 * math.sqrt(2))        # an edge is on top
    off = {"type": "bar", "y": [-10, 30], "z": [-5, 5]}
    # A = 90 turns the part's -Y side up (ray psi = 90 points along part (-1, 0))
    assert stock_top(off, 90) == pytest.approx(10)
    assert stock_top(off, 270) == pytest.approx(30)
    assert stock_reach(off) == pytest.approx(math.hypot(30, 5))


def test_index_places_op_on_the_face():
    tp = Toolpath("p", Tool("t", 3))
    tp.rapid(z=5); tp.rapid(x=1, y=2); tp.feed(z=-1, f=100); tp.feed(x=3, f=500); tp.rapid(z=5)
    out = ops.index(tp, 90, 10, 20)
    assert out.name == "p A90"
    head = [(m.kind, m.x, m.y, m.z, m.a) for m in out.moves[:2]]
    assert head == [("rapid", None, None, 20, None), ("rapid", None, None, None, 90)]
    assert [m.z for m in out.moves[2:-1]] == [15, None, 9, None, 15]
    assert [(m.x, m.y) for m in out.moves[2:-1]] == [(None, None), (1, 2), (None, None), (3, None), (None, None)]
    assert (out.moves[-1].kind, out.moves[-1].z) == ("rapid", 20)
    tp.rapid(a=10)
    with pytest.raises(ValueError, match="must not move A"):
        ops.index(tp, 90, 10, 20)
    high = Toolpath("q", Tool("t", 3))
    high.rapid(z=30)
    with pytest.raises(ValueError, match="above the index clearance"):
        ops.index(high, 0, 0, 20)


def drill_job(**op):
    return {"post": "linuxcnc", "stock": BAR, "tools": {"d3": D3},
            "operations": [{"op": "drill", "tool": "d3", "points": [[10, 3]], "depth": 2, "rpm": 12000, **op}]}


def test_indexed_drill_gcode_is_pinned():
    # bar 20 x 20: corner radius 14.1421, clearance 5 -> turn A at Z19.1421; face top 10.
    # After the tool change it goes to the first X Y and A at the G53 Z0 height.
    gcode = job_post(drill_job(index=[90])).emit(run_job(drill_job(index=[90])))
    assert gcode.splitlines()[4:-4] == [
        "(drill A90)", "M5", "G53 G0 Z0", "T1 M6 (d3 D3)", "G43 H1", "M3 S12000", "G0 X10 Y3 A90",
        "G0 Z19.1421", "G0 A90", "G0 Z15", "G0 X10 Y3", "G0 Z11", "G1 Z8 F200",
        "G0 Z11", "G0 Z15", "G0 Z19.1421"]
    assert "G93" not in gcode


def test_face_z_and_clearance_override():
    job = drill_job(index=[0], face_z=8)
    job["index_clearance"] = 2
    lines = job_post(job).emit(run_job(job)).splitlines()
    assert "G0 Z16.1421" in lines and "G0 Z13" in lines and "G1 Z6 F200" in lines
    job = drill_job(index=[0], face_z=12)               # the op's own safe Z (17) is higher still
    job["index_clearance"] = 2
    lines = job_post(job).emit(run_job(job)).splitlines()
    assert lines[11:13] == ["G0 Z17", "G0 A0"]


def test_indexed_pocket_keeps_arcs_in_g94():
    job = {"post": "linuxcnc", "stock": {"type": "cylinder", "diameter": 30, "x": [0, 60]},
           "tools": {"d3": D3},
           "shapes": {"hole": {"type": "circle", "cx": 30, "cy": 4, "r": 5}},
           "operations": [{"op": "pocket", "shape": "hole", "tool": "d3", "depth": 1, "step_down": 1,
                           "index": [0, 120]}]}
    gcode = job_post(job).emit(run_job(job))
    assert "G93" not in gcode and re.search(r"^G[23] ", gcode, re.M)
    assert gcode.count("G0 A") == 2
    issues, _, sim = run(gcode, machine({1: D3}), job["stock"])
    assert errors(issues) == []
    # the pocket floor is at Z14 (face 15 - 1) and Y = 4 +- 3.5 on both faces
    for a in (0, 120):
        psi = a - math.degrees(math.atan2(4, 14))       # ray through machine (Y 4, Z 14) at A = a
        floor = 14 / math.cos(math.radians(cell_psi(sim, psi) - a))
        assert ray(sim, 30, psi) == pytest.approx(floor, abs=1e-6)
        assert ray(sim, 30, a + 30) == pytest.approx(15)     # the other side of the axis line: untouched


def test_passes_and_dependencies_per_face():
    job = {"post": "linuxcnc", "stock": BAR, "tools": {"d3": D3, "v60": V60},
           "index": {"sides": 2, "start": 0},
           "shapes": {"sq": {"type": "rect", "x": 5, "y": -4, "w": 10, "h": 8}},
           "operations": [
               {"id": "v", "op": "vcarve", "shape": "sq", "tool": "v60", "max_depth": 1, "step": 0.3,
                "clear_tool": "d3"},
               {"id": "h", "op": "drill", "tool": "d3", "points": [[30, 0]], "depth": 1, "after": ["v"]}]}
    names = [tp.name for tp in run_job(job)]
    assert names == ["vcarve-clear A0", "vcarve A0", "vcarve-clear A180", "vcarve A180", "drill A0", "drill A180"]
    job["tool_order"] = "minimize_changes"
    names = [tp.name for tp in run_job(job)]
    # both clearings first (one tool), then the V-bit; the drill waits for all of "v"
    assert names == ["vcarve-clear A0", "vcarve-clear A180", "vcarve A180", "vcarve A0", "drill A0", "drill A180"]


def test_minimize_changes_stays_on_the_face():
    job = drill_job(index={"sides": 4})
    job["operations"].append({"op": "drill", "tool": "d3", "points": [[30, 0]], "depth": 1, "index": [90]})
    job["tool_order"] = "minimize_changes"
    names = [tp.name for tp in run_job(job)]
    assert names == ["drill A0", "drill A90", "drill A90", "drill A180", "drill A270"]


def test_turns_take_the_short_way():
    job = drill_job(index=[0, 270])
    a = [m.a for tp in run_job(job) for m in tp.moves if m.a is not None]
    assert a == [0, -90]


@pytest.mark.parametrize("change, msg", [
    (lambda j: j.update(index=[0], stock={"type": "box", "x": [0, 40], "y": [0, 20], "z": [-10, 0]}),
     "cylinder or bar"),
    (lambda j: j.update(index=[0], stock={**BAR, "y": [0, 20]}), "enclose the rotary axis"),
    (lambda j: j.update(index=[0], rotary={"diameter": 20}), "ambiguous"),
    (lambda j: j["operations"][0].update(face_z=3), "face_z only applies"),
    (lambda j: j.update(index_clearance=0, index=[0]), "index_clearance"),
])
def test_bad_indexed_jobs_are_refused(change, msg):
    job = drill_job()
    change(job)
    with pytest.raises(ValueError, match=msg):
        run_job(job)


def test_wrapped_and_indexed_paths_in_one_job():
    job = {"post": "linuxcnc", "stock": {"type": "cylinder", "diameter": 20, "x": [0, 60]},
           "rotary": {"diameter": 20}, "tools": {"d3": D3},
           "shapes": {"band": {"type": "rect", "x": 40, "y": 0, "w": 4, "h": 62.83}},
           "operations": [
               {"op": "drill", "tool": "d3", "points": [[10, 0]], "depth": 1, "index": [90]},
               {"op": "pocket", "shape": "band", "tool": "d3", "depth": 0.5, "step_down": 0.5}]}
    paths = run_job(job)
    assert [tp.name for tp in paths] == ["drill A90", "pocket"]
    gcode = job_post(job).emit(paths)
    head, wrapped = gcode.split("(pocket)")
    assert "G93" not in head and "G93" in wrapped
    assert errors(run(gcode, machine({1: D3}), job["stock"])[0]) == []


# ---------- verify: bar stock and tools beside the axis ----------

def test_bar_stock_rays_and_surface():
    s = Stock({"type": "bar", "x": [0, 10], "y": [-10, 30], "z": [-5, 5]})
    assert s.radius == pytest.approx(math.hypot(30, 5))
    assert ray(s, 5, 0) == pytest.approx(5)
    assert ray(s, 5, 90) == pytest.approx(10 / math.cos(math.radians(cell_psi(s, 90) - 90)))   # psi 90: part -Y
    assert ray(s, 5, 270) == pytest.approx(30 / math.cos(math.radians(cell_psi(s, 270) - 270)))
    assert s.surface(0, 0) == pytest.approx(5)
    assert s.surface(20, 0) == pytest.approx(5)
    assert s.surface(31, 0) == -math.inf
    assert s.surface(0, 90) == pytest.approx(10)        # A = 90: part -Y side up
    assert s.surface(0, 270) == pytest.approx(30)
    assert s.surface(4, 270) == pytest.approx(30)
    with pytest.raises(ValueError, match="enclose"):
        Stock({"type": "bar", "x": [0, 10], "y": [1, 30], "z": [-5, 5]})


def test_cut_beside_the_axis_lands_on_the_right_ray():
    # a flat D6 sinks to Z15 at machine Y+5 with A = 90: the part ray through that
    # point is psi = 90 - atan(5/15) (A turns the part, Y+ is ray angle -), radius 15.81
    prog = "T1 M6\nG43 H1\nM3 S10000\nG0 Z40\nG0 A90\nG0 X30 Y5\nG1 Z15 F100\nG0 Z40\n"
    stock = {"type": "cylinder", "diameter": 40, "x": [0, 60]}
    issues, _, sim = run(prog, machine(), stock)
    assert errors(issues) == []
    psi = 90 - math.degrees(math.atan2(5, 15))
    assert ray(sim, 30, psi) == pytest.approx(math.hypot(5, 15), abs=0.02)
    assert ray(sim, 30, 90 + math.degrees(math.atan2(5, 15))) == pytest.approx(20)   # mirror side: untouched


def test_turning_a_bar_needs_clearance_over_its_edges():
    prog = "T1 M6\nG43 H1\nM3 S10000\nG0 Z{z}\nG0 X20 Y0\nG0 A45\n"
    bar = {"type": "bar", "x": [0, 40], "y": [-20, 20], "z": [-20, 20]}
    assert any("CRASH: rapid" in e for e in errors(run(prog.format(z=22), machine(), bar)[0]))
    assert errors(run(prog.format(z=29), machine(), bar)[0]) == []      # corners at 28.28


def test_hex_flats_on_a_cylinder():
    # six faces, each flattened 2 mm deep over its full width: an exact hexagon of apothem 18
    width = 2 * math.sqrt(20 ** 2 - 18 ** 2) + 4
    job = {"post": "linuxcnc", "stock": {"type": "cylinder", "diameter": 40, "x": [0, 50]},
           "tools": {"d3": D3}, "index": {"sides": 6},
           "shapes": {"flat": {"type": "rect", "x": 10, "y": -width / 2, "w": 30, "h": width}},
           "operations": [{"op": "pocket", "shape": "flat", "tool": "d3", "depth": 2, "step_down": 1}]}
    gcode = job_post(job).emit(run_job(job))
    issues, _, sim = run(gcode, machine({1: D3}), job["stock"])
    assert errors(issues) == []
    for a in range(0, 360, 60):
        assert ray(sim, 25, a) == pytest.approx(18, abs=0.01)
        # the corner between two flats: 18 / cos(30 deg) = 20.78 > 20, so the full radius stays
        assert ray(sim, 25, a + 30) == pytest.approx(20, abs=0.01)
    assert ray(sim, 5, 0) == pytest.approx(20)                           # outside the flats


# ---------- the example ----------

def test_indexed_example_matches_fixture_and_verifies_clean():
    job, base = load_job(EX / "indexed_job.json")
    gcode = job_post(job).emit(run_job(job, base))
    assert gcode == (EX / "indexed.ngc").read_text()
    m = machine_from_library(Machine.load(EX.parent / "machine" / "printnc_provisional.json"), EX / "tools.json")
    issues, _, sim = run(gcode, m, job["stock"])
    assert [str(i) for i in issues] == []
    for psi in (0, 90, 180, 270):                        # a 3 mm slot on every face
        assert ray(sim, 35, psi) == pytest.approx(17, abs=0.01)
    assert ray(sim, 100, 90) == pytest.approx(12, abs=0.01)             # holes 8 deep on 90 and 270 only
    assert ray(sim, 100, 270) == pytest.approx(12, abs=0.01)
    assert ray(sim, 100, 180) == pytest.approx(20)


# ---------- viewer parity ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_viewer_ray_interval_is_a_port_of_verify(tmp_path):
    tpl = (Path(__file__).parent.parent / "camcore" / "viewer_template.html").read_text()
    start = tpl.index("function barRadius(")
    end = tpl.index("function stamp(")
    rng = np.random.default_rng(7)
    cases = []
    for tool in ({"diameter": 6, "kind": "flat"}, {"diameter": 6, "kind": "ball"},
                 {"diameter": 12.7, "kind": "vbit", "v_angle": 60}):
        for _ in range(400):
            y = float(rng.choice([0.0, rng.uniform(-25, 25)]))
            z = float(rng.uniform(0.5, 25))
            dx = float(rng.uniform(-tool["diameter"] / 2 - 0.2, tool["diameter"] / 2 + 0.2))
            phi = float(-math.atan2(y + rng.uniform(-8, 8), z)) if rng.random() < 0.8 else float(rng.uniform(-1.6, 1.6))
            cases.append([dx, phi, z, tool, y])
    js = tpl[start:end] + f"""
const C = {json.dumps(cases)};
console.log(JSON.stringify(C.map(([dx, phi, z, tl, y]) => rayInterval(dx, phi, z, tl, y))));
"""
    (tmp_path / "t.js").write_text(js)
    got = json.loads(subprocess.run(["node", str(tmp_path / "t.js")], capture_output=True, text=True,
                                    check=True).stdout)
    for (dx, phi, z, tool, y), g in zip(cases, got):
        lo, hi = Stock._ray_interval(np.array([dx]), np.array([phi]), z, tool, y)
        if not lo[0] <= hi[0]:
            assert g is None, (dx, phi, z, tool, y, g)
            continue
        assert g is not None, (dx, phi, z, tool, y, lo, hi)
        assert g[0] == pytest.approx(lo[0], abs=1e-9)
        assert (g[1] is None and hi[0] == np.inf) or g[1] == pytest.approx(hi[0], abs=1e-9)
