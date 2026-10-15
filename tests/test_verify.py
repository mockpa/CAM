from pathlib import Path

from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
TOOLS = {1: {"diameter": 6, "kind": "flat", "flute_length": 10}}
BOX = {"type": "box", "x": [0, 50], "y": [0, 50], "z": [-20, 0]}
HEAD = "G21 G90\nT1 M6\nG43 H1\nM3 S10000\nG0 Z5\n"


def check(body, stock=BOX, **mkw):
    m = Machine(tools=dict(TOOLS), **mkw)
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
