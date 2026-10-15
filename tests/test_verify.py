import math
from pathlib import Path

import pytest

from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
TOOLS = {1: {"diameter": 6, "kind": "flat", "flute_length": 10, "center_cutting": True}}
BOX = {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-20, 0]}
HEAD = "G21 G90\nT1 M6\nG43 H1\nM3 S10000\nG0 Z5\n"


def check(body, stock=BOX, **mkw):
    m = Machine(**{"tools": dict(TOOLS), **mkw})
    issues, motions, _ = run(HEAD + body + "M5\nM2\n", m, stock)
    return [i.message for i in issues if i.level == "error"], [i.message for i in issues if i.level == "warning"]


def test_clean_program_passes():
    errors, warnings = check("G0 X10 Y10\nG1 Z-2 F200\nG1 X30 F800\nG0 Z5\n")
    assert errors == [] and warnings == []


def test_rapid_into_stock_is_a_crash():
    errors, _ = check("G0 X10 Y10\nG0 Z-2\n")
    assert any("CRASH: rapid" in e for e in errors)


def test_rapid_through_already_cut_slot_is_fine():
    errors, _ = check("G0 X10 Y10\nG1 Z-2 F200\nG1 X30 F800\nG0 X10\nG0 Z5\n")
    assert errors == []


def test_cutting_with_spindle_off():
    errors, _ = check("M5\nG0 X10 Y10\nG1 Z-1 F200\n")
    assert any("spindle stopped" in e for e in errors)


def test_unknown_tool_and_missing_offset():
    m = Machine(tools=dict(TOOLS))
    issues, _, _ = run("T9 M6\nM3 S1000\nG0 X10 Y10 Z5\nG1 Z-1 F100\n", m, BOX)
    msgs = [str(i) for i in issues]
    assert any("T9 is not in the machine's tool table" in s for s in msgs)
    assert any("no G43" in s for s in msgs)


def test_travel_limits_and_flute_length():
    errors, warnings = check("G0 X700 Y10\n")
    assert any("outside machine travel" in e for e in errors)
    errors, warnings = check("G0 X10 Y10\nG1 Z-12 F100\nG0 Z5\n")
    assert any("flute length" in w for w in warnings)


def test_a_axis_on_three_axis_machine():
    errors, _ = check("G0 X10 Y10 A90\n", has_a=False)
    assert any("no rotary axis" in e for e in errors)


def test_g93_needs_f():
    errors, _ = check("G93\nG0 X10 Y10\nG1 X20 A10\nG94\n", stock=None)
    assert any("without F" in e for e in errors)


def test_examples_verify_clean():
    m = machine_from_library(Machine.load(EX / "machine.json"), EX / "tools.json")
    import json
    box = json.loads((EX / "machine.json").read_text())["stock"]
    issues, _, _ = run((EX / "sign.ngc").read_text(), m, box)
    assert [str(i) for i in issues] == []
    cyl = json.loads((EX / "stock_cylinder.json").read_text())
    issues, _, sim = run((EX / "wrap.ngc").read_text(), m, cyl)
    assert [str(i) for i in issues] == []
    assert sim.h.min() < 20 - 2       # material was actually removed


PRINTNC = Path(__file__).parent.parent / "machine" / "printnc_provisional.json"


def test_printnc_profile_runs_examples_and_enforces_its_travel():
    import json
    m = machine_from_library(Machine.load(PRINTNC), EX / "tools.json")
    assert m.travel == {"x": [0, 1035], "y": [0, 1035], "z": [-150, 0]}
    assert m.has_a and m.rapid_rate == 17000
    box = json.loads(PRINTNC.read_text())["stock"]
    issues, _, _ = run((EX / "sign.ngc").read_text(), m, box)
    assert [str(i) for i in issues] == []
    cyl = json.loads((EX / "stock_cylinder.json").read_text())
    issues, _, _ = run((EX / "wrap.ngc").read_text(), m, cyl)
    assert [str(i) for i in issues] == []
    # G54 X is +20 mm in machine coordinates, so work X1016 is 1 mm past X max.
    errors, _ = check("G0 X1016\n", travel=m.travel, work_offset=m.work_offset)
    assert any("outside machine travel" in e for e in errors)
    errors, _ = check("G0 X1015\n", travel=m.travel, work_offset=m.work_offset)
    assert errors == []


def test_plunge_needs_a_center_cutting_tool():
    plain = {1: {**TOOLS[1], "center_cutting": False}}
    body = "G0 X10 Y10\nG1 Z-2 F300\nG0 Z5\n"
    assert check(body, tools=plain)[0] == [
        "T1 plunges into material but is not marked center_cutting (ramp or helix in, or mark the tool)"]
    assert check(body)[0] == []                                     # centre-cutting tool may plunge
    # a 3 deg ramp (2 mm down over 38.16 mm) is fine for any tool
    assert check("G0 X10 Y10\nG1 Z0 F300\nG1 X48.16 Z-2 F600\nG0 Z5\n", tools=plain)[0] == []
    # feeding straight down where the ramp already cut to -2 is not a plunge into material ...
    ramp = "G0 X10 Y10\nG1 Z0 F300\nG1 X48.16 Z-2 F600\nG0 Z5\n"
    assert check(ramp + "G0 X48.16\nG1 Z-2 F300\nG0 Z5\n", tools=plain)[0] == []
    # ... but at X20 the ramp was only 0.52 mm deep, so going to -2 there is one
    assert len(check(ramp + "G0 X20\nG1 Z-2 F300\nG0 Z5\n", tools=plain)[0]) == 1


def test_ops_never_plunge_with_a_non_center_cutting_tool():
    from shapely.geometry import Point, box
    from camcore import ops
    from camcore.post import get_post
    from camcore.tools import Tool
    em = Tool("em6", 6.0, number=1)                       # not centre-cutting
    shape = box(0, 0, 40, 25).difference(Point(20, 12.5).buffer(5))
    paths = [ops.pocket(shape, em, depth=4, step_down=1),
             ops.pocket(shape, em, depth=4, step_down=1, entry="ramp"),
             ops.profile(box(0, 0, 40, 25), em, depth=4, step_down=2)]
    m = Machine(tools={1: {"diameter": 6, "kind": "flat", "flute_length": 20}},
                work_offset={"x": 50, "y": 50, "z": -100})
    for tp in paths:
        issues, _, _ = run(get_post("linuxcnc").emit([tp]), m, {"type": "box", "x": [-10, 50], "y": [-10, 35], "z": [-20, 0]})
        assert [str(i) for i in issues] == [], tp.name


def test_arcs_are_replayed_and_checked():
    # quarter circle r=10 around (20, 20), clockwise from (10, 20) to (20, 30), cutting 1 mm deep
    body = "G0 X10 Y20\nG1 Z-1 F300\nG2 X20 Y30 I10 J0 F600\nG0 Z5\n"
    errors, warnings = check(body)
    assert errors == [] and warnings == []
    m = Machine(tools=dict(TOOLS))
    _, motions, sim = run(HEAD + body + "M5\nM2\n", m, BOX)
    arc = [mo for mo in motions if mo.line == 8]
    assert len(arc) > 5                                       # replayed as chords
    for mo in arc:
        assert math.hypot(mo.end[0] - 20, mo.end[1] - 20) == pytest.approx(10, abs=1e-9)
    assert sum(mo.seconds for mo in arc) == pytest.approx(10 * math.pi / 2 / 600 * 60, rel=1e-3)
    # material is gone on the arc (45 deg point) and untouched at the circle centre
    def h(x, y):
        return sim.h[int(round(x / 0.5)), int(round(y / 0.5))]
    assert h(20 - 10 / math.sqrt(2), 20 + 10 / math.sqrt(2)) == pytest.approx(-1.0)
    assert h(20, 20) == 0.0
    # counter-clockwise the long way round: G3 from the same start reaches (20, 30) after 270 deg
    _, motions, _ = run(HEAD + "G0 X10 Y20\nG1 Z-1 F300\nG3 X20 Y30 I10 J0\nG0 Z5\nM5\nM2\n", m, BOX)
    assert min(mo.end[1] for mo in motions if mo.line == 8) == pytest.approx(10, abs=0.05)


def test_helical_arc_and_full_circle():
    m = Machine(tools=dict(TOOLS))
    body = "G0 X30 Y25\nG1 Z0 F300\nG3 X30 Y25 Z-2 I-5 J0 F600\nG0 Z5\n"     # one full turn, 2 mm down
    issues, motions, _ = run(HEAD + body + "M5\nM2\n", m, BOX)
    assert [str(i) for i in issues] == []
    arc = [mo for mo in motions if mo.line == 8]
    assert arc[-1].end[2] == -2 and len(arc) >= 36
    zs = [mo.end[2] for mo in arc]
    assert zs == sorted(zs, reverse=True)


def test_bad_arcs_are_errors():
    errors, _ = check("G0 X10 Y20\nG1 Z1\nG2 X20 Y30.01 I10 J0 F600\n")
    assert errors == ["arc end radius 10.0100 differs from start radius 10.0000"]
    errors, _ = check("G0 X10 Y20\nG1 Z1\nG2 X20 Y30 R10 F600\n")
    assert errors == ["R-format arcs are not supported (use I J)"]
    errors, _ = check("G18\nG0 X10 Y20\n")
    assert errors == ["G18: only XY-plane (G17) arcs are supported"]
    # a rapid arc is not a thing, but a cutting arc into fresh material with the spindle off is
    errors, _ = check("M5\nG0 X10 Y20\nG1 Z-1 F300\n")
    assert errors and "spindle stopped" in errors[0]
