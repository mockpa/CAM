"""Machine body: the part-against-part geometry, verify's collision checks
(obstacles, stock, gantry, rotary unit), the position after a tool change,
the wizard's jaw reach, and the vismach scene the sim GUI draws."""
import importlib.util
import json
import re
import types
from pathlib import Path

import pytest

from camcore.body import Body, Part, jaw_reach, overlaps, part, scene
from camcore.check import check
from camcore.verify import Machine, run

ROOT = Path(__file__).parent.parent
PRINTNC = json.loads((ROOT / "machine" / "printnc_provisional.json").read_text())

BODY = {
    "default_tool_length": 30,
    "spindle": [{"name": "collet nut", "axis": "z", "x": 0, "y": 0, "r": 16, "z": [-16, 0]},
                {"name": "spindle housing", "axis": "z", "x": 0, "y": 0, "r": 40, "z": [30, 250]}],
    "gantry": [{"name": "gantry beam", "x": [-100, 600], "y": [60, 140], "z": [40, 160]}],
    "fixed": [{"name": "spoilboard", "x": [-50, 650], "y": [-50, 450], "z": [-150, -120], "tool_may_enter": True},
              {"name": "ATC rack", "x": [400, 550], "y": [380, 400], "z": [-120, -60]}],
    "rotary": {"jaw_face_x": 100, "axis_y": 200, "axis_z": -60, "parts": [
        {"name": "chuck", "axis": "x", "y": 0, "z": 0, "r": 50, "x": [-50, 0]}]},
}
TOOL = {"diameter": 6, "kind": "flat", "flute_length": 20, "center_cutting": True, "gauge_length": 30}


def machine(**kw):
    return Machine(travel={"x": [0, 600], "y": [0, 400], "z": [-150, 0]},
                   work_offset={"x": 20, "y": 20, "z": -100}, has_a=True, tools={1: dict(TOOL)},
                   body=Body(BODY), **kw)


def program(*moves):
    return "\n".join(["G21 G90 G17 G94", "G53 G0 Z0", "T1 M6", "G43 H1", "M3 S10000", *moves, "M5", "M2"]) + "\n"


def errors(issues):
    return [str(i) for i in issues if i.level == "error"]


BOX = {"type": "box", "x": [0, 100], "y": [0, 80], "z": [-20, 0]}


def test_parts_meet_exactly_where_their_shapes_do():
    box = part({"name": "b", "x": [0, 10], "y": [0, 10], "z": [0, 10]})
    cz = part({"name": "c", "axis": "z", "x": 0, "y": 0, "r": 5, "z": [0, 10]})
    cx = part({"name": "x", "axis": "x", "y": 0, "z": 0, "r": 5, "x": [0, 10]})
    # a vertical cylinder next to a box corner: its bounding box overlaps long before it does
    d = 5 / 2 ** 0.5
    assert list(overlaps(cz, [(-d + 0.1, -d + 0.1, 0), (-d - 0.1, -d - 0.1, 0)], box)) == [True, False]
    assert list(overlaps(cz, [(-4.9, 5, 0), (-5.1, 5, 0)], box)) == [True, False]
    # two vertical cylinders: centre distance against the sum of the radii
    assert list(overlaps(cz, [(9.9, 0, 0), (10.1, 0, 0)], cz)) == [True, False]
    # along X against a box below it, and against a vertical cylinder from the side and above
    assert list(overlaps(cx, [(0, 0, -4.9), (0, 0, -5.1)], box)) == [True, False]
    assert list(overlaps(cz, [(5, 9.9, -5), (5, 10.1, -5)], cx)) == [True, False]
    above = (5, 0, 4.9)                          # cylinder bottom 0.1 into the top of the X cylinder
    assert overlaps(cz, [above], cx)[0] and not overlaps(cz, [(5, 0, 5.1)], cx)[0]
    # the vertical one beside the X cylinder's end: their sections only meet if it reaches past x = 10
    assert overlaps(cz, [(14.9, 0, 0)], cx)[0] and not overlaps(cz, [(15.1, 0, 0)], cx)[0]
    with pytest.raises(ValueError, match="empty"):
        part({"name": "flat", "x": [0, 0], "y": [0, 1], "z": [0, 1]})


def test_spindle_hitting_the_atc_rack_is_an_error():
    # rack: machine Y 380..400, top at Z -60 = work Z 40; the nut bottom is 14 above the tip
    def over_rack(z):
        return errors(run(program("G0 X380 Y340", f"G0 Z{z}", "G0 X460 Y365"), machine(), None)[0])
    assert over_rack(45) == []
    # the move runs from machine (400, 360) to (480, 385) in 84 samples; the tool (radius 3) is
    # in the rack from machine Y 377.01 (sample 58), the nut (radius 16) from Y 364.01 (sample 14)
    tool = "ERROR   line 8: COLLISION: tool hits ATC rack with the tool at work X435.24 Y357.26 Z{}.00"
    assert over_rack(30) == [tool.format(30)]
    assert over_rack(20) == ["ERROR   line 8: COLLISION: collet nut hits ATC rack with the tool at work "
                             "X393.33 Y344.17 Z20.00", tool.format(20)]


def test_the_tool_may_cut_into_the_spoilboard_but_the_spindle_may_not():
    # spoilboard top at machine Z -120 = work Z -20: the stock bottom. With a 45 mm gauge
    # length the nut stays 29 mm above the tip, clear of the 20 mm stock
    m = machine()
    m.tools[1]["gauge_length"] = 45
    through = program("G0 X50 Y40", "G0 Z1", "G1 Z-20.2 F300", "G0 Z5")
    assert errors(run(through, m, BOX)[0]) == []
    deep = program("G0 X150 Y40", "G0 Z1", "G1 Z-50 F300", "G0 Z5")       # nut bottom at -21
    assert errors(run(deep, m, None)[0]) == [
        "ERROR   line 8: COLLISION: collet nut hits spoilboard with the tool at work X150.00 Y40.00 Z-50.00"]


def test_the_collet_nut_hits_the_stock_beside_a_deep_hole():
    # 6 mm hole, 18 deep: the 32 mm nut comes down to 18 + 16 - 30 = 4 mm into the uncut top
    gcode = program("G0 X50 Y40", "G0 Z1", "G1 Z-18 F300", "G0 Z5")
    assert errors(run(gcode, machine(), BOX)[0]) == [
        "ERROR   line 8: COLLISION: collet nut hits the stock with the tool at work X50.00 Y40.00 Z-15.00"]
    assert errors(run(program("G0 X50 Y40", "G0 Z1", "G1 Z-13 F300", "G0 Z5"), machine(), BOX)[0]) == []


def test_the_gantry_beam_passes_over_the_stock_only_if_it_is_low_enough():
    # beam bottom at machine Z 40 = work Z 140, 60 to 140 mm behind the tool axis; the
    # tool stays at Y <= 150, well in front of the stock at Y 250..330. The beam is over
    # the stock from tool Y 110 on (sampled every mm: 111)
    tall = {"type": "box", "x": [0, 100], "y": [250, 330], "z": [-20, 150]}
    gcode = program("G0 X50 Y0", "G0 Z100", "G0 Y150")
    assert errors(run(gcode, machine(), tall)[0]) == [
        "ERROR   line 8: COLLISION: gantry beam hits the stock with the tool at work X50.00 Y111.00 Z100.00"]
    low = {**tall, "z": [-20, 130]}
    assert errors(run(gcode, machine(), low)[0]) == []


def test_rotary_jobs_sit_at_the_chuck_and_the_tool_must_stay_off_the_jaws():
    stock = {"type": "cylinder", "diameter": 30, "x": [-20, 80], "chuck_hold": 10}
    m = machine()
    # jaw face with the tool at work X -10; G54 X = 100 - (-20 + 10), Y and Z on the axis
    assert m.offset_for(stock) == {"x": 110, "y": 200, "z": -60}
    assert m.offset_for(BOX) == m.work_offset
    # the nut (radius 16) is 14 above the tip: inside the chuck's radius 50 from tip Z 35
    clear = program("G0 X10 Y0 A0", "G0 Z50", "G0 Z20")
    assert errors(run(clear, m, stock)[0]) == []
    assert errors(run(program("G0 X5 Y0 A0", "G0 Z50", "G0 Z20"), m, stock)[0]) == [
        "ERROR   line 8: COLLISION: collet nut hits chuck with the tool at work X5.00 Y0.00 Z35.00"]
    near = program("G0 X-8 Y0 A0", "G0 Z50", "G0 Z20")                     # tool edge 1 mm past the jaw face
    assert "ERROR   line 8: COLLISION: tool hits chuck with the tool at work X-8.00 Y0.00 Z49.00" in \
        errors(run(near, m, stock)[0])
    errs = errors(run(clear, m, {k: v for k, v in stock.items() if k != "chuck_hold"})[0])
    assert any("chuck_hold" in e for e in errs)


def test_z_may_not_come_down_before_x_and_y_after_a_tool_change():
    blind = program("G0 Z5", "G0 X10 Y10")
    assert any("Z moves down before X and Y" in e for e in errors(run(blind, machine(), None)[0]))
    assert errors(run(program("G0 X10 Y10", "G0 Z5"), machine(), None)[0]) == []
    # the post now does this after every tool change, at the G53 Z0 height
    from camcore import ops
    from camcore.post import get_post
    from camcore.tools import Tool
    from shapely.geometry import box
    tp = ops.pocket(box(10, 10, 30, 30), Tool("em6", 6.0, number=1), depth=1, step_down=1)
    lines = get_post("linuxcnc").emit([tp]).splitlines()
    k = lines.index("T1 M6 (em6 D6)")
    assert lines[k - 1] == "G53 G0 Z0" and lines[k + 1] == "G43 H1" and lines[k + 2].startswith("M3 S")
    assert re.fullmatch(r"G0 X[-\d.]+ Y[-\d.]+", lines[k + 3]) and lines[k + 4].startswith("G0 Z")


def test_tools_without_a_gauge_length_get_the_default_with_a_warning():
    m = machine()
    del m.tools[1]["gauge_length"]
    issues, _, _ = run(program("G0 X10 Y10", "G0 Z5"), m, None)
    assert [str(i) for i in issues] == ['WARNING line 6: T1 has no "gauge_length" (spindle nose to tip): '
                                        'the machine body check assumes 30 mm']


def test_jaw_reach_of_the_printnc_body():
    b = Body(PRINTNC["body"])
    # the Z plate (80 mm either side of the tool axis, 10 above the nose) against the headstock
    # (50 behind the jaw face, up to 60 above the axis): it needs 80 - 50 = 30 mm
    assert jaw_reach(b, 45, 6) == pytest.approx(30 - 0.01, abs=0.005)
    # without the Z plate, the collet nut over the chuck decides: 16 mm
    spec = {**PRINTNC["body"], "spindle": [p for p in PRINTNC["body"]["spindle"] if p["name"] != "Z plate"]}
    assert jaw_reach(Body(spec), 45, 6) == pytest.approx(16 - 0.01, abs=0.005)


def load_vismach_script():
    spec = importlib.util.spec_from_file_location("camcore_vismach", ROOT / "sim" / "camcore_vismach.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeVismach(types.SimpleNamespace):
    """Records what the model script builds, without OpenGL."""

    def __init__(self):
        def node(kind):
            def make(*args):
                return {"kind": kind, "args": args}
            return make
        super().__init__(**{k: node(k) for k in ("Box", "Translate", "HalTranslate", "HalRotate", "Color",
                                                    "Collection", "Capture", "CylinderX")})

        class CylinderZ:
            def __init__(self, *args):
                self.args = args

            def coords(self):
                return list(self.args)
        self.CylinderZ = CylinderZ


def walk(node):
    yield node
    if isinstance(node, dict):
        for a in node["args"]:
            for item in a if isinstance(a, list) else [a]:
                yield from walk(item)


def test_the_sim_model_draws_the_checked_scene():
    b = Body(PRINTNC["body"])
    stock = {"type": "cylinder", "diameter": 30, "x": [-45, 50], "chuck_hold": 10}
    m = Machine(body=b)
    sc = json.loads(json.dumps(scene(b, m.offset_for(stock), stock, {2: TOOL})))
    frames = [p["frame"] for p in sc["parts"]]
    assert frames.count("spindle") == 4 and frames.count("gantry") == 1 and frames.count("stock") == 1
    names = {p["name"] for p in sc["parts"] if p["frame"] == "fixed"}
    assert {"chuck", "headstock", "ATC rack"} <= names
    chuck = next(p for p in sc["parts"] if p["name"] == "chuck")
    assert chuck["hi"][0] == 180 and chuck["lo"][1:] == [350, -113]     # jaw face, axis Y 400 Z -63, r 50
    flat = scene(b, PRINTNC["work_offset"], PRINTNC["stock"], {})
    assert "chuck" not in {p["name"] for p in flat["parts"]}           # the rotary unit is off for flat jobs

    vm = load_vismach_script()
    values = {"joint.0.pos-fb": 100.0, "joint.1.pos-fb": 400.0, "joint.2.pos-fb": -20.0, "joint.3.pos-fb": 90.0,
              "iocontrol.0.tool-number": 2}
    pins = vm.Pins(sc, values.__getitem__)
    assert pins["x"] == 100 and pins["a"] == 90 and pins["nose"] == -20 + 30   # tip + gauge length
    model, _, _ = vm.build(sc, FakeVismach(), pins)
    nodes = list(walk(model))
    boxes = [n for n in nodes if isinstance(n, dict) and n["kind"] == "Box"]
    assert len(boxes) == len([p for p in sc["parts"] if p["kind"] == "box"])
    moved = {n["args"][2] for n in nodes if isinstance(n, dict) and n["kind"] == "HalTranslate"}
    assert moved == {"x", "y", "z", "nose"}
    assert any(n["args"][2] == "a" for n in nodes if isinstance(n, dict) and n["kind"] == "HalRotate")
    tool = next(n for n in nodes if hasattr(n, "coords") and n.args == (0, 0, 0, 0))
    assert tool.coords() == [0, 3.0, 30, 3.0]


def test_check_writes_the_scene_with_the_sim_setup(tmp_path):
    assert check(ROOT / "examples" / "pawn_job.json", tmp_path, sim=False, log=lambda s: None)
    sc = json.loads((tmp_path / "pawn_scene.json").read_text())
    assert sc["rotary_axis"] == [400, -63]
    assert "MACHINE = printnc-provisional-sim" in sc["sim"]["ini"]
    # G54 from the rotary unit: jaw face 180 - (stock start -45 + hold 10)
    assert sc["sim"]["var"].splitlines()[1:4] == ["5221\t215.000000", "5222\t400.000000", "5223\t-63.000000"]
    out = tmp_path / "setup"
    out.mkdir()
    load_vismach_script().setup(tmp_path / "pawn_scene.json", out)
    assert (out / "sim.var").read_text() == sc["sim"]["var"] and (out / "tool.tbl").read_text().startswith("T1 ")
