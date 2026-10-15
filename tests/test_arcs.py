import json
import math
from pathlib import Path

import numpy as np
import pytest

from camcore.arcfit import fit_run
from camcore.job import load_job, run_job
from camcore.ops.entry import helix_down
from camcore.post import get_post, job_post
from camcore.toolpath import Toolpath
from camcore.tools import Tool
from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
EM = Tool("em3", 3.0, number=1)


def body(g):
    lines = g.splitlines()
    return lines[lines.index("M3 S18000") + 1:lines.index("M5 M9")]


def quarter(n=24, r=10.0, z=-1.0):
    """Quarter circle around the origin, counter-clockwise from (10, 0) to (0, 10), as n lines
    (24 lines: each bulges 0.0054 mm from the true arc, inside the 0.01 mm tolerance)."""
    tp = Toolpath("q", EM)
    tp.rapid(z=5)
    tp.rapid(x=r, y=0)
    tp.feed(z=z, f=300)
    for k in range(1, n + 1):
        a = math.pi / 2 * k / n
        tp.feed(x=r * math.cos(a), y=r * math.sin(a), f=600)
    tp.rapid(z=5)
    return tp


def test_quarter_circle_becomes_one_arc_pinned():
    assert body(get_post("linuxcnc").emit([quarter()])) == [
        "G0 X10 Y0", "G0 Z5", "G0 X10 Y0", "G1 Z-1 F300", "G3 X0 Y10 I-10 J0 F600", "G0 Z5"]
    rev = Toolpath("r", EM)                                  # same points, the other way round
    rev.rapid(z=5); rev.rapid(x=0, y=10); rev.feed(z=-1, f=300)
    for k in range(23, -1, -1):
        a = math.pi / 2 * k / 24
        rev.feed(x=10 * math.cos(a), y=10 * math.sin(a), f=600)
    rev.rapid(z=5)
    assert body(get_post("linuxcnc").emit([rev])) == [
        "G0 X0 Y10", "G0 Z5", "G0 X0 Y10", "G1 Z-1 F300", "G2 X10 Y0 I0 J-10 F600", "G0 Z5"]


def test_linear_option_and_rotary_stay_g1():
    g = get_post("linuxcnc", arcs=False).emit([quarter()])
    assert "\nG2 " not in g and "\nG3 " not in g and g.count("G1 X") == 24
    from camcore import ops
    w = ops.wrap(quarter(), diameter=40, safe_z=5)
    g = get_post("linuxcnc").emit([w])
    assert "G93" in g and "\nG2 " not in g and "\nG3 " not in g


def test_corners_stay_lines_and_tolerance_is_respected():
    square = [(0, 0, -1), (10, 0, -1), (10, 10, -1), (0, 10, -1), (0, 0, -1)]
    assert [p[0] for p in fit_run(square, 0.01)] == ["line"] * 4
    # a polygon standing for a circle only becomes an arc if every chord bulges less than the tolerance
    for n, expect_arc in ((8, False), (12, False), (24, True), (64, True)):
        pts = [(10 * math.cos(a), 10 * math.sin(a), 0.0) for a in np.linspace(0, math.pi / 2, n + 1)]
        sag = 10 * (1 - math.cos(math.pi / 2 / n / 2))
        assert (sag <= 0.01) == expect_arc
        pieces = fit_run(pts, 0.01)
        assert [p[0] for p in pieces] == (["arc"] if expect_arc else ["line"] * n)


def test_helix_becomes_helical_arcs():
    tp = Toolpath("h", EM)
    tp.rapid(z=5); tp.rapid(x=13, y=10); tp.feed(z=0, f=300)
    helix_down(tp, (10, 10), 3, 0, -1, 3.0, 600)
    lines = body(get_post("linuxcnc").emit([tp]))
    arcs = [l for l in lines if l.startswith("G2")]
    assert arcs and all(" I" in l and " J" in l for l in arcs)
    assert any(" Z" in l for l in arcs)                    # helical arcs carry Z
    m = Machine(tools={1: {"diameter": 3, "kind": "flat", "center_cutting": False}})
    issues, motions, _ = run(get_post("linuxcnc").emit([tp]), m, {"type": "box", "x": [0, 20], "y": [0, 20], "z": [-5, 0]})
    assert [str(i) for i in issues] == []
    for mo in motions:
        if mo.kind == "feed" and mo.end[2] < 0:
            assert math.hypot(mo.end[0] - 10, mo.end[1] - 10) == pytest.approx(3, abs=0.011)


def test_arcs_cut_the_same_as_lines():
    job, base = load_job(EX / "sign_job.json")
    paths = run_job(job, base)
    m = machine_from_library(Machine.load(Path(__file__).parent.parent / "machine" / "printnc_provisional.json"),
                             EX / "tools.json")
    res = {}
    for arcs in (True, False):
        g = get_post("linuxcnc", arcs=arcs).emit(paths)
        issues, _, sim = run(g, m, job["stock"])
        assert [str(i) for i in issues] == []
        res[arcs] = (g, sim.h.copy())
    assert res[True][0].count("\n") < 0.6 * res[False][0].count("\n")      # much shorter program
    top = job["stock"]["z"][1]
    removed = {k: (top - h).sum() for k, (_, h) in res.items()}
    assert removed[True] == pytest.approx(removed[False], rel=0.002)        # same material removed


def test_job_post_options():
    job = {"post": "linuxcnc", "post_options": {"arcs": False, "arc_tolerance": 0.005}}
    p = job_post(job)
    assert p.arcs is False and p.arc_tolerance == 0.005
    assert job_post(job, "grbl").name == "grbl"               # options only apply to the job's own post
    with pytest.raises(ValueError):
        get_post("linuxcnc", arc_tolerance=0)
