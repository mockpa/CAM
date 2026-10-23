"""camcore_verify_kernel (rust/verify_kernel) against verify's Python reference.

The kernel is a port of Stock._interference_py / _cut_py / sweep's Python loop.
Every comparison here runs the same thing through both and asks for the same
issues, the same motions and the same stock, cell for cell. The only allowed
difference is the last bit of sin/cos on rotary stock (numpy and libm may round
them differently), hence the 1e-12 mm."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from camcore import verify
from camcore.check import DEFAULT_MACHINE, job_library
from camcore.verify import Machine, Stock, machine_from_library

KERNEL = pytest.importorskip("camcore_verify_kernel")
EX = Path(__file__).parent.parent / "examples"
TOL = 1e-12        # mm


def both(text, machine, stock, monkeypatch, res=0.5):
    monkeypatch.setattr(verify, "KERNEL", KERNEL)
    fast = verify.run(text, machine, stock, res=res)
    monkeypatch.setattr(verify, "KERNEL", None)
    ref = verify.run(text, machine, stock, res=res)
    monkeypatch.setattr(verify, "KERNEL", KERNEL)
    return fast, ref


def assert_same(fast, ref):
    (fi, fm, fs), (ri, rm, rs) = fast, ref
    assert [str(i) for i in fi] == [str(i) for i in ri]
    assert fm == rm
    assert fs.h.shape == rs.h.shape
    assert np.abs(fs.h - rs.h).max() <= TOL


@pytest.mark.parametrize("name", ["sign_medial", "indexed", "pawn", "inlay_pocket"])
def test_examples_replay_the_same(name, monkeypatch):
    """Box stock with flat and V-bits (sign_medial, inlay_pocket), the rectangular bar
    at several A angles with Y offsets (indexed), cylinder stock with ball ends (pawn)."""
    job = json.loads((EX / f"{name}_job.json").read_text())
    m = machine_from_library(Machine.load(DEFAULT_MACHINE), job_library(job, EX))
    assert_same(*both((EX / f"{name}.ngc").read_text(), m, job["stock"], monkeypatch))


TOOLS = {1: {"diameter": 6, "kind": "flat", "flute_length": 3, "center_cutting": False},
         2: {"diameter": 6, "kind": "ball", "flute_length": 20, "center_cutting": True},
         3: {"diameter": 8, "kind": "bull", "corner_radius": 1.5},
         4: {"diameter": 12, "kind": "vbit", "v_angle": 60, "flute_length": 2}}
BOX = {"type": "box", "x": [0, 40], "y": [0, 30], "z": [-15, 0]}
CYL = {"type": "cylinder", "x": [0, 40], "diameter": 30}
BAR = {"type": "bar", "x": [0, 40], "y": [-12, 8], "z": [-6, 9]}


def program(tool, spindle=True, g43=True, cuts=""):
    return (f"G21 G90\nT{tool} M6\n" + (f"G43 H{tool}\n" if g43 else "") + ("M3 S10000\n" if spindle else "")
            + "G0 X5 Y5\nG0 Z20\n" + cuts + "G0 Z40\nM5\nM2\n")


BOX_CUTS = ("G1 Z-1 F300\nG1 X30 Y20 Z-4 F600\nG1 Y8\nG2 X20 Y8 I-5 J0\nG1 Z-6 X12 Y25\n"
            "G0 Z5\nG0 X35 Y25\nG0 Z-3\n")                    # the last one is a rapid into material
ROT_CUTS = ("G1 Z12 F300\nG1 X30 A90 Z10 F600\nG1 Y3 A200\nG1 Z7 X12 A-40\nG1 Y-4 Z9 A30\n"
            "G0 Z14\nG0 X35 A120\nG0 Z11\n")


@pytest.mark.parametrize("tool", [1, 2, 3, 4])
@pytest.mark.parametrize("stock,cuts", [(BOX, BOX_CUTS), (CYL, ROT_CUTS), (BAR, ROT_CUTS)],
                         ids=["box", "cylinder", "bar"])
def test_every_tool_and_stock_replays_the_same(tool, stock, cuts, monkeypatch):
    """Cuts, plunges, flute warnings, arcs and a rapid crash; bull noses on rotary stock
    are an error before the stock is touched, also the same."""
    m = Machine(tools={k: dict(v) for k, v in TOOLS.items()})
    fast, ref = both(program(tool, cuts=cuts), m, stock, monkeypatch)
    assert_same(fast, ref)
    assert ref[0]                 # each of these finds something: the rapid crash at least


@pytest.mark.parametrize("kw", [{"spindle": False}, {"g43": False}])
def test_spindle_stopped_and_missing_g43_replay_the_same(kw, monkeypatch):
    m = Machine(tools={k: dict(v) for k, v in TOOLS.items()})
    for stock, cuts in ((BOX, BOX_CUTS), (CYL, ROT_CUTS)):
        assert_same(*both(program(2, cuts=cuts, **kw), m, stock, monkeypatch))


def test_no_tool_loaded_replays_the_same(monkeypatch):
    m = Machine(tools={k: dict(v) for k, v in TOOLS.items()})
    text = "G21 G90\nM3 S10000\nG0 X5 Y5\nG0 Z20\n" + BOX_CUTS + "M2\n"
    fast, ref = both(text, m, BOX, monkeypatch)
    assert_same(fast, ref)
    assert any("no tool loaded" in str(i) for i in ref[0])


def test_fine_grid_replays_the_same(monkeypatch):
    """The inlay fit replays at 0.2 mm."""
    m = Machine(tools={k: dict(v) for k, v in TOOLS.items()})
    assert_same(*both(program(4, cuts=BOX_CUTS), m, BOX, monkeypatch, res=0.2))


@pytest.mark.parametrize("tool", [2, 4])
@pytest.mark.parametrize("stock,cuts", [(BOX, "G1 Z-1 F300\nG1 X12 Y9 Z-3 F600\nG1 X9\nG0 Z5\nG0 X30 Y20\nG0 Z-2\n"),
                                        (CYL, "G1 Z14 F300\nG1 X12 A40 Z12 F600\nG1 A20\nG0 Z20\nG0 A40\nG0 Z13\n")],
                         ids=["box", "cylinder"])
def test_big_steps_split_over_cores_replay_the_same(tool, stock, cuts, monkeypatch):
    """At 0.05 mm a step of the 12 mm V-bit on the box, and any step on the cylinder,
    covers more than the kernel's PARALLEL_CELLS (16384) cells, so its rows are cut on
    several cores (the 6 mm ball on the box stays below: serial); the same as the
    Python loop either way (and a rapid crash at the end)."""
    m = Machine(tools={k: dict(v) for k, v in TOOLS.items()})
    fast, ref = both(program(tool, cuts=cuts), m, stock, monkeypatch, res=0.05)
    assert_same(fast, ref)
    assert any("CRASH: rapid" in str(i) for i in ref[0])


@pytest.mark.parametrize("spec", [BOX, CYL, BAR, {"type": "cylinder", "x": [0, 5], "diameter": 0.6}],
                         ids=["box", "cylinder", "bar", "thin-cylinder"])
def test_single_calls_match_at_random_positions(spec, monkeypatch):
    """interference and cut anywhere: inside and outside the stock, below the axis,
    beside it at any tool Y; on a 0.6 mm cylinder a tool covers more than the full turn
    of rays (columns listed twice)."""
    rng = np.random.default_rng(7)
    fast, ref = Stock(spec), Stock(spec)
    rotary = fast.rotary
    for k in range(3000):
        tool = dict(TOOLS[1 + k % 4])
        if rotary and tool["kind"] == "bull":
            tool["kind"] = "flat"
        x = rng.uniform(-5, 45)
        u = rng.uniform(-400, 400) if rotary else rng.uniform(-5, 35)
        z = rng.uniform(-3, 18) if rotary else rng.uniform(-16, 2)
        y = rng.uniform(-10, 10) if rotary else 0.0
        monkeypatch.setattr(verify, "KERNEL", KERNEL)
        got = fast.interference(x, u, z, tool, y)
        want = ref._interference_py(x, u, z, tool, y)
        assert got == want or abs(got - want) <= TOL, (k, got, want)
        if k % 2:
            fast.cut(x, u, z, tool, y)
            ref._cut_py(x, u, z, tool, y)
    assert np.abs(fast.h - ref.h).max() <= TOL
    assert (fast.h != Stock(spec).h).any()          # something was cut


@pytest.mark.parametrize("spec", [BOX, CYL, BAR], ids=["box", "cylinder", "bar"])
def test_sweep_matches_its_python_loop(spec, monkeypatch):
    """Stock.sweep: first step in material, first step deeper than the flute, its depth."""
    rng = np.random.default_rng(3)
    fast, ref = Stock(spec), Stock(spec)
    for k in range(300):
        tool = dict(TOOLS[1 + k % 4])
        if fast.rotary and tool["kind"] == "bull":
            tool["kind"] = "ball"
        lo = (-2, -8, 0 if fast.rotary else -10, -200)
        hi = (42, 8 if fast.rotary else 32, 18 if fast.rotary else 1, 200)
        start, end = tuple(rng.uniform(lo, hi)), tuple(rng.uniform(lo, hi))
        steps, stop, flute = int(rng.integers(1, 60)), bool(k % 5 == 0), [None, 2, 0.5][k % 3]
        monkeypatch.setattr(verify, "KERNEL", KERNEL)
        got = fast.sweep(start, end, steps, tool, stop, flute)
        monkeypatch.setattr(verify, "KERNEL", None)
        want = ref.sweep(start, end, steps, tool, stop, flute)
        assert got[:2] == want[:2] and abs(got[2] - want[2]) <= TOL, (k, got, want)
    assert np.abs(fast.h - ref.h).max() <= TOL


def test_tool_key_defaults():
    assert verify._tool_key({}) == (0, 1.5, 0.0, 90.0)
    assert verify._tool_key({"kind": "vbit", "diameter": 12, "v_angle": 60}) == (3, 6.0, 0.0, 60.0)
    assert verify._tool_key({"kind": "drill", "diameter": 5}) == (0, 2.5, 0.0, 90.0)
    assert math.isinf(Stock(BOX).interference(-50, -50, 0, {}))      # off the grid
