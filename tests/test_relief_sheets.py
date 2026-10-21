"""Lamella boards (relief plan phase 4): nesting, interlocking, sheet checks, one job per board."""
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage
from shapely.geometry import LineString, Polygon, box

from camcore.check import DEFAULT_MACHINE, job_library
from camcore.job import load_job, run_job
from camcore.post import job_post
from camcore.relief import heightfield as hf
from camcore.relief import lamella_job as lj
from camcore.relief.lamella import LamellaSettings, lamellas
from camcore.relief.lamella_job import SheetError, SheetSettings
from camcore.relief.nesting import MaxRects, pack
from camcore.verify import Machine, machine_from_library, run

EX = Path(__file__).parent.parent / "examples"
REL = EX / "relief"
MDATA = json.loads(DEFAULT_MACHINE.read_text())
D, WEB = 6.0, 2.0                     # em6


class B:
    def __init__(self, w, h, name=""):
        self.w, self.h, self.name = w, h, name


def field(w=120, h=80):
    return hf.prepare(hf.sample_relief(120, 80), w, h, blur=1)


# --------------------------------------------------------------------------
# Packing
# --------------------------------------------------------------------------

def test_maxrects_best_short_side_fit():
    mr = MaxRects(100, 50)
    assert mr.find(30, 50)[1:] == (0, 0)
    mr.place(0, 0, 30, 50)
    assert mr.free == [(30.0, 0.0, 70.0, 50.0)]
    mr.place(30, 0, 70, 20)
    assert sorted(mr.free) == [(30.0, 20.0, 70.0, 30.0)]
    assert mr.find(80, 10) is None and mr.find(70, 30)[1:] == (30.0, 20.0)


def test_pack_never_overlaps_and_turns_when_allowed():
    rng = np.random.default_rng(3)
    items = [B(float(w), float(h), k) for k, (w, h) in enumerate(rng.uniform(20, 150, (40, 2)))]
    sheets, too_big = pack(items, 400, 300)
    assert not too_big and sum(len(s) for s in sheets) == 40
    for placed in sheets:
        for p in placed:
            assert -1e-9 <= p.x and p.x + p.w <= 400 + 1e-9 and -1e-9 <= p.y and p.y + p.h <= 300 + 1e-9
            assert {round(p.w, 6), round(p.h, 6)} == {round(p.item.w, 6), round(p.item.h, 6)}
        for a, b in itertools.combinations(placed, 2):
            assert box(a.x, a.y, a.x + a.w, a.y + a.h).intersection(box(b.x, b.y, b.x + b.w, b.y + b.h)).area < 1e-6
    tall = [B(50, 120)]
    assert pack(tall, 200, 100)[0][0][0].turned
    assert pack(tall, 200, 100, allow_turn=False)[1] == tall


# --------------------------------------------------------------------------
# Interlocking
# --------------------------------------------------------------------------

def ramp_part(L=100.0, h0=10.0, h1=40.0):
    return Polygon([(0, 0), (L, 0), (L, h1), (0, h0)])


def test_a_ramp_and_its_twin_interlock_at_the_clearance():
    # the twin turned 180 deg has its top edge parallel to the first's: the gap between the
    # two lines is clearance / cos(slope) vertically
    c = 2 * D + WEB
    lift = lj.pair_lift(ramp_part(), ramp_part(), 100.0, c)
    slope = math.atan(30 / 100)
    assert lift == pytest.approx(10 + 40 + c / math.cos(slope), abs=2e-3)
    assert ramp_part().distance(lj.flip(ramp_part(), 100.0, lift)) >= c - 1e-9
    assert ramp_part().distance(lj.flip(ramp_part(), 100.0, lift - 0.01)) < c


def test_flat_parts_are_not_paired():
    from camcore.relief.lamella import Lamella

    s = np.linspace(0, 100, 11)
    flat = [Lamella(i, f"V{i:02d}", 0.0, s, np.full(11, 30.0), Polygon([(0, 0), (100, 0), (100, 30), (0, 30)]))
            for i in range(2)]
    us = lj.units(flat, D / 2, 2 * D + WEB, interlock=True)
    assert [len(u.members) for u in us] == [1, 1]          # a pair would be no lower than stacking


def test_interlocking_saves_board_and_never_costs_it():
    parts = lamellas(field(240, 160), LamellaSettings(thickness=12, base=10, relief=25, step=1))
    c = 2 * D + WEB
    single = lj.units(parts, D / 2, c, interlock=False)
    paired = lj.units(parts, D / 2, c, interlock=True)
    assert sum(len(u.members) for u in paired) == len(parts) == len(single)
    by = {p.label: p for p in parts}
    for u in paired:
        if len(u.members) == 2:
            a, b = (m.part for m in u.members)
            assert u.h < a.height + b.height + c                       # lower than stacked
    area = lambda us: sum((u.w + c) * (u.h + c) for u in us)          # noqa: E731
    assert area(paired) < area(single)
    st = SheetSettings(width=600, height=400)
    assert len(lj.nest(parts, SheetSettings(width=300, height=300, interlock=True), D)) <= \
        len(lj.nest(parts, SheetSettings(width=300, height=300), D))
    assert by  # labels unique


# --------------------------------------------------------------------------
# Boards
# --------------------------------------------------------------------------

@pytest.mark.parametrize("interlock", [False, True])
@pytest.mark.parametrize("bw, bh", [(200, 150), (60, 300)])      # the narrow board turns every part
def test_parts_keep_their_distance_shape_and_tabs(interlock, bw, bh):
    ls = LamellaSettings(thickness=12, base=8, relief=15, step=2)
    parts = {p.label: p for p in lamellas(field(), ls)}
    st = SheetSettings(width=bw, height=bh, interlock=interlock, tabs=2)
    sheets = lj.nest(list(parts.values()), st, D)
    assert len(sheets) >= 2 and sum(len(s.parts) for s in sheets) == len(parts)    # more than one board
    turned = flipped = 0
    for sh in sheets:
        polys = []
        for label, pts, tabs in sh.parts:
            poly = Polygon(pts)
            part = parts[label]
            assert poly.is_valid and poly.area == pytest.approx(part.outline.area)
            assert poly.distance(box(0, 0, bw, bh).exterior) >= D + WEB - 1e-6     # inside the edge
            assert box(0, 0, bw, bh).contains(poly)
            # the tabs sit on the bottom edge: the edge between the first two outline points
            bottom = LineString(pts[:2])
            assert len(tabs) == 2 and all(bottom.distance(Polygon(pts).exterior.interpolate(
                Polygon(pts).exterior.project(__import__("shapely").geometry.Point(t)))) < 1e-6 for t in tabs)
            assert all(bottom.distance(__import__("shapely").geometry.Point(t)) < 1e-6 for t in tabs)
            polys.append(lj.cut_outline(poly, D / 2))
        for a, b in itertools.combinations(polys, 2):
            assert a.distance(b) >= 2 * D + WEB - 2e-3                          # cuts never touch
        turned += sum(p.turned for p in sh.placed)
        flipped += sum(m.flipped for p in sh.placed for m in p.item.unit.members)
    # on the narrow board no pair fits: interlocking falls back to single parts
    assert (turned > 0) == (bw < bh) and (flipped > 0) == (interlock and bw > bh)


def test_boards_the_machine_cannot_take_are_refused():
    lj.check_sheet(SheetSettings(width=1000, height=600), 18, MDATA)
    with pytest.raises(SheetError, match="X reaches -20..1015"):
        lj.check_sheet(SheetSettings(width=1200, height=600), 18, MDATA)           # Reliefwerk's default
    with pytest.raises(SheetError, match="ATC rack"):
        lj.check_sheet(SheetSettings(width=1000, height=1000), 18, MDATA)
    with pytest.raises(SheetError, match="web"):
        lj.check_sheet(SheetSettings(web=0), 18, MDATA)


def test_bad_designs_are_refused(tmp_path):
    F = field()
    lib = EX / "tools.json"
    ls = LamellaSettings(thickness=12, base=8, relief=15, step=2)
    cases = [(SheetSettings(width=1200, height=600), ls, "does not fit the machine"),
             (SheetSettings(width=90, height=60), ls, "do\\(es\\) not fit"),
             (SheetSettings(tool="v60"), ls, "needs an end mill"),
             (SheetSettings(), LamellaSettings(thickness=30, base=8, relief=15), "flutes are only 22"),
             (SheetSettings(tabs=6, tab_width=20), ls, "too short for 6 tabs"),
             ]
    for st, l, msg in cases:
        with pytest.raises(SheetError, match=msg):
            lj.build_jobs(F, l, st, tmp_path, "x", lib)
    with pytest.raises(SheetError, match="no lamella"):
        lj.build_jobs(field(10, 80), ls, SheetSettings(), tmp_path, "x", lib)


def material_pieces(sim, bottom):
    """Connected pieces of material left on verify's stock (4-neighbour cells above the cut)."""
    _, n = ndimage.label(sim.h > bottom + 0.5)
    return n


def test_board_machines_clean_with_tabs_and_no_loose_scrap(tmp_path):
    ls = LamellaSettings(thickness=12, base=8, relief=15, step=2)
    st = SheetSettings(width=360, height=260, interlock=True)
    (path,) = lj.write_jobs(field(), ls, st, tmp_path, "b", EX / "tools.json")
    job, base = load_job(path)
    paths = run_job(job, base)
    assert len(paths) == len(job["operations"]) == 10
    assert [n for tp in paths for n in tp.notes] == []                # every part got both tabs
    assert all(o["tabs"]["at"] and "entry" not in o for o in job["operations"])  # ramps (em6 cannot plunge)
    gcode = job_post(job).emit(paths)
    machine = machine_from_library(Machine.load(DEFAULT_MACHINE), job_library(job, base))
    issues, _, sim = run(gcode, machine, job["stock"])
    assert [str(i) for i in issues] == []
    # everything left is one piece: parts hang on the skeleton by their tabs, no scrap is cut free
    assert material_pieces(sim, -12) == 1
    # without tabs every part is a piece of its own, and still nothing else comes loose
    for o in job["operations"]:
        o.pop("tabs")
    issues, _, sim = run(job_post(job).emit(run_job(job, base)), machine, job["stock"])
    assert material_pieces(sim, -12) == 1 + 10


def test_example_regenerates(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_example", REL / "make_example.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    paths = mod.main(tmp_path)[1:]
    assert [p.name for p in paths] == ["waves_lam_sheet1_job.json"]
    for p in paths:
        new, old = json.loads(p.read_text()), json.loads((REL / p.name).read_text())
        new.pop("tool_library"), old.pop("tool_library")
        assert new == old
