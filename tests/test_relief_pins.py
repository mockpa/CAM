"""Pins and paperwork (relief plan phase 5)."""
import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import numpy as np
import pytest

from camcore.relief import heightfield as hf
from camcore.relief import pins
from camcore.relief.heightfield import HeightField
from camcore.relief.lamella import LamellaSettings
from camcore.relief.lamella_job import SheetSettings, plan_boards, write_jobs
from camcore.relief.pins import PinSettings
from camcore.relief.sheets import write_pin_paperwork

EX = Path(__file__).parent.parent / "examples"


def ramp_x(w=100.0, h=64.0):
    return HeightField(np.tile(np.linspace(0, 1, 101, dtype=np.float32), (65, 1)), w, h)


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


# --------------------------------------------------------------------------
# Pin grid
# --------------------------------------------------------------------------

def test_grid_is_centred_rows_from_the_back():
    g = pins.pin_grid(ramp_x(), PinSettings(size=20, gap=4, levels=0, base=10, relief=40))
    # 4 x 20 + 3 x 4 = 92 across 100 (4 mm margins), 2 x 20 + 4 = 44 across 64 (10 mm)
    assert g.cx == pytest.approx([14, 38, 62, 86]) and g.cy == pytest.approx([44, 20])
    assert g.count == 8
    # h = X / 100 is linear: each pin's 3 x 3 mean is its centre
    assert g.heights[0] == pytest.approx(np.round((10 + 40 * g.cx / 100) / 0.5) * 0.5)
    assert np.array_equal(g.heights[0], g.heights[1])


def test_levels_snap_heights():
    g = pins.pin_grid(ramp_x(), PinSettings(size=20, gap=4, levels=3, base=10, relief=40))
    assert g.levels == [10, 30, 50]
    # h at the centres 0.14, 0.38, 0.62, 0.86 -> levels round(h * 2) = 0, 1, 1, 2
    assert g.level[0].tolist() == [0, 1, 1, 2] and g.heights[0].tolist() == [10, 30, 30, 50]


def test_stepless_levels_are_the_lengths_used():
    g = pins.pin_grid(ramp_x(), PinSettings(size=20, gap=4, levels=0, base=10, relief=40))
    assert g.levels == sorted(set(g.heights.ravel().tolist()))
    assert all(g.levels[k] == g.heights[j, i] for (j, i), k in np.ndenumerate(g.level))


def test_row_names():
    assert [pins.row_name(j) for j in (0, 1, 25, 26, 27, 51, 52, 701, 702)] == \
        ["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]


@pytest.mark.parametrize("shape, n, across", [("square", 4, 20.0), ("round", 48, 20.0), ("hex", 6, 20.0)])
def test_pin_outlines(shape, n, across):
    pts = np.array(pins.outline(PinSettings(shape=shape, size=20), 5, 7))
    assert len(pts) == n
    assert pts[:, 1].max() - pts[:, 1].min() == pytest.approx(across, abs=1e-9)   # hex: across the flats
    area = 0.5 * abs(np.dot(pts[:, 0], np.roll(pts[:, 1], 1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], 1)))
    assert area == pytest.approx({"square": 400, "round": math.pi * 100, "hex": 2 * math.sqrt(3) * 100}[shape], rel=3e-3)


@pytest.mark.parametrize("kw", [dict(shape="star"), dict(size=0), dict(gap=-1), dict(base=0), dict(relief=-1),
                                dict(levels=1), dict(levels=-2), dict(kerf=-1)])
def test_bad_pin_settings(kw):
    with pytest.raises(ValueError):
        pins.pin_grid(ramp_x(), PinSettings(**kw))


def test_pins_bigger_than_the_artwork():
    with pytest.raises(ValueError, match="no 120 mm pin"):
        pins.pin_grid(ramp_x(), PinSettings(size=120))


# --------------------------------------------------------------------------
# Paperwork
# --------------------------------------------------------------------------

def check_svg(path):
    root = ET.parse(path).getroot()                      # well-formed XML
    assert root.tag == "{http://www.w3.org/2000/svg}svg" and root.get("viewBox")
    return root


def test_pin_paperwork_totals_match_the_model(tmp_path):
    F = hf.prepare(hf.sample_relief(120, 80), 300, 200, blur=2)
    st = PinSettings(shape="hex", size=25, gap=3, levels=6, kerf=3)
    g = pins.pin_grid(F, st)
    files = write_pin_paperwork(F, st, g, tmp_path, "p")
    assert [f.name for f in files] == ["p_cut_list.csv", "p_positions.csv", "p_pin_plan.svg", "p_pin_plan.dxf"]
    cl = read_csv(tmp_path / "p_cut_list.csv")
    assert cl[0] == ["level", "length mm", "count", "bar mm (with 3 mm saw cuts)"]
    body, total = cl[1:-1], cl[-1]
    assert sum(int(r[2]) for r in body) == int(total[2]) == g.count == 10 * 7
    assert float(total[3]) == pytest.approx(g.heights.sum() + g.count * 3, abs=0.01)
    for r in body:
        assert int(r[2]) == int((g.heights == float(r[1])).sum())
    pos = read_csv(tmp_path / "p_positions.csv")
    assert len(pos) == 1 + g.count and pos[1][:3] == ["A1", "A", "1"] and pos[-1][:3] == ["G10", "G", "10"]
    assert float(pos[1][6]) == pytest.approx(g.cy[0]) and float(pos[1][6]) > float(pos[-1][6])   # A at the back
    root = check_svg(tmp_path / "p_pin_plan.svg")
    assert len(root.findall("{http://www.w3.org/2000/svg}polygon")) == g.count + 1      # pins + frame
    msp = ezdxf.readfile(tmp_path / "p_pin_plan.dxf").modelspace()
    assert len(msp.query("LWPOLYLINE[layer=='PINS']")) == g.count and len(msp.query("TEXT")) == g.count


def test_round_pins_are_circles_in_the_dxf(tmp_path):
    F = hf.prepare(hf.sample_relief(120, 80), 120, 80, blur=2)
    st = PinSettings(shape="round", size=18)
    g = pins.pin_grid(F, st)
    write_pin_paperwork(F, st, g, tmp_path, "r")
    msp = ezdxf.readfile(tmp_path / "r_pin_plan.dxf").modelspace()
    circles = msp.query("CIRCLE")
    assert len(circles) == g.count and all(c.dxf.radius == 9 for c in circles)


def test_lamella_paperwork_matches_the_layout(tmp_path):
    F = hf.prepare(hf.sample_relief(120, 80), 120, 80, blur=1)
    ls = LamellaSettings(thickness=12, base=8, relief=15, step=2)
    st = SheetSettings(width=200, height=150, interlock=True)
    paths = write_jobs(F, ls, st, tmp_path, "w", EX / "tools.json")
    d = plan_boards(F, ls, st, EX / "tools.json")
    assert len(paths) == len(d.sheets) == 2
    rows = read_csv(tmp_path / "w_parts.csv")
    assert rows[0][:5] == ["part", "length mm", "lowest mm", "highest mm", "position mm (from the left)"]
    assert [r[0] for r in rows[1:]] == [p.label for p in d.parts]
    by_board = {}
    for r, p in zip(rows[1:], d.parts):
        assert float(r[1]) == p.length and float(r[3]) == pytest.approx(p.height, abs=0.01)
        by_board.setdefault(int(r[5]), []).append(r[0])
    for sh in d.sheets:
        assert sorted(by_board[sh.index + 1]) == [ob.label for ob in sh.parts]
        ob = sh.parts[0]
        r = next(r for r in rows if r[0] == ob.label)
        assert (float(r[6]), float(r[7])) == pytest.approx(tuple(ob.points.min(axis=0)), abs=0.01)
    root = check_svg(tmp_path / "w_assembly.svg")
    texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert all(p.label in texts for p in d.parts)
    for n in (1, 2):
        check_svg(tmp_path / f"w_board{n}.svg")
        msp = ezdxf.readfile(tmp_path / f"w_board{n}.dxf").modelspace()
        assert len(msp.query("LWPOLYLINE[layer=='PARTS']")) == len(d.sheets[n - 1].parts)
        assert len(msp.query("CIRCLE[layer=='TABS']")) == 2 * len(d.sheets[n - 1].parts)
