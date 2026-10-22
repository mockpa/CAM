import json
import math
from pathlib import Path

import ezdxf
import pytest

from camcore.dxf import load_dxf
from camcore.job import load_job, run_job
from camcore.post import job_post
from camcore.svg import _cubic, load_svg, parse_transform

EX = Path(__file__).resolve().parent.parent / "examples"


def svg(tmp_path, body, attrs='viewBox="0 0 100 100"'):
    p = tmp_path / "t.svg"
    p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>')
    return str(p)


def bounds(g):
    return tuple(round(v, 3) for v in g.bounds)


# --- SVG ---------------------------------------------------------------------

def test_svg_transforms_on_elements_and_nested_groups(tmp_path):
    # 10 x 5 rect at (0, 0); SVG Y points down, the import flips it (y -> -y)
    assert bounds(load_svg(svg(tmp_path, '<rect width="10" height="5"/>'))) == (0, -5, 10, 0)
    g = load_svg(svg(tmp_path, '<g transform="translate(20 30)"><rect width="10" height="5" transform="scale(2)"/></g>'))
    assert bounds(g) == (20, -40, 40, -30)                               # scaled first, then moved
    g = load_svg(svg(tmp_path, '<rect width="10" height="5" transform="rotate(90)"/>'))
    assert bounds(g) == (-5, -10, 0, 0)
    g = load_svg(svg(tmp_path, '<rect width="10" height="5" transform="rotate(90 5 0)"/>'))
    assert bounds(g) == (0, -5, 5, 5)
    g = load_svg(svg(tmp_path, '<g transform="matrix(1 0 0 1 3 4)"><g transform="translate(1,1)">'
                               '<polygon points="0,0 2,0 2,2"/></g></g>'))
    assert bounds(g) == (4, -7, 6, -5)
    g = load_svg(svg(tmp_path, '<rect width="10" height="10" transform="skewX(45)"/>'))
    assert bounds(g) == (0, -10, 20, 0)
    assert parse_transform("translate(1) scale(2)") == (2, 0, 0, 2, 1, 0)
    with pytest.raises(ValueError, match="unsupported SVG transform"):
        parse_transform("perspective(3)")


def test_svg_physical_units(tmp_path):
    g = load_svg(svg(tmp_path, '<rect width="100" height="50"/>', 'width="10cm" height="5cm" viewBox="0 0 100 50"'))
    assert bounds(g) == (0, -50, 100, 0)                                 # 1 user unit = 1 mm
    g = load_svg(svg(tmp_path, '<rect width="1" height="1"/>', 'width="2in" height="2in" viewBox="0 0 2 2"'))
    assert bounds(g) == (0, -25.4, 25.4, 0)
    g = load_svg(svg(tmp_path, '<rect x="10" y="10" width="5" height="5"/>', 'width="20mm" height="20mm" viewBox="10 10 20 20"'))
    assert bounds(g) == (0, -5, 5, 0)                                    # viewBox origin moves to 0
    g = load_svg(svg(tmp_path, '<rect width="10" height="10"/>', 'width="400px" viewBox="0 0 100 100"'))
    assert bounds(g) == (0, -10, 10, 0)                                  # px: user units stay mm


def test_svg_arcs_smooth_curves_and_ellipses(tmp_path):
    # circle of r 10 drawn as two A arcs, a T/S-smoothed blob, an ellipse
    g = load_svg(svg(tmp_path, '<path d="M 0 10 A 10 10 0 0 1 20 10 A 10 10 0 0 1 0 10 Z"/>'))
    assert g.area == pytest.approx(math.pi * 100, rel=1e-3) and bounds(g) == (0, -20, 20, 0)
    g = load_svg(svg(tmp_path, '<path d="M 0 0 a 5 5 0 1 0 10 0 a 5 5 0 1 0 -10 0 z"/>'))
    assert g.area == pytest.approx(math.pi * 25, rel=1e-3)
    s = load_svg(svg(tmp_path, '<path d="M0 0 C 0 -10 10 -10 10 0 S 20 10 20 0 L 20 20 L 0 20 Z"/>'))
    c = load_svg(svg(tmp_path, '<path d="M0 0 C 0 -10 10 -10 10 0 C 10 10 20 10 20 0 L 20 20 L 0 20 Z"/>'))
    assert s.symmetric_difference(c).area < 1e-9                         # S reflects the last control point
    t = load_svg(svg(tmp_path, '<path d="M0 0 Q 5 -10 10 0 T 20 0 L 20 20 L 0 20 Z"/>'))
    q = load_svg(svg(tmp_path, '<path d="M0 0 Q 5 -10 10 0 Q 15 10 20 0 L 20 20 L 0 20 Z"/>'))
    assert t.symmetric_difference(q).area < 1e-9
    e = load_svg(svg(tmp_path, '<ellipse cx="0" cy="0" rx="20" ry="10" transform="rotate(30)"/>'))
    assert e.area == pytest.approx(math.pi * 200, rel=1e-3)


def test_bezier_flattening_meets_the_chord_tolerance():
    p = [(0, 0), (0, 40), (60, 40), (60, 0)]
    pts = [p[0]] + _cubic(*p)
    def B(t):
        u = 1 - t
        return (u**3 * p[0][0] + 3 * u**2 * t * p[1][0] + 3 * u * t**2 * p[2][0] + t**3 * p[3][0],
                u**3 * p[0][1] + 3 * u**2 * t * p[1][1] + 3 * u * t**2 * p[2][1] + t**3 * p[3][1])
    n = len(pts) - 1
    worst = 0.0
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        for k in range(1, 20):
            x, y = B((i + k / 20) / n)
            L = math.hypot(bx - ax, by - ay)
            worst = max(worst, abs((x - ax) * (by - ay) - (y - ay) * (bx - ax)) / L)
    assert worst <= 0.005


# --- DXF ---------------------------------------------------------------------

def dxf(tmp_path, build, units=4, name="t.dxf"):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    build(doc.modelspace(), doc)
    p = tmp_path / name
    doc.saveas(p)
    return str(p)


def test_dxf_joins_lines_and_arcs_in_any_order_and_direction(tmp_path):
    def build(msp, doc):
        # a 40 x 20 slot: two lines and two half arcs, shuffled, one line drawn backwards
        msp.add_arc((30, 10), 10, -90, 90)
        msp.add_line((30, 20), (10, 20))
        msp.add_line((30, 0), (10, 0))                                   # against the chain direction
        msp.add_arc((10, 10), 10, 90, 270)
        msp.add_circle((20, 10), 3)                                      # hole
        msp.add_text("LABEL")                                            # ignored
    g = load_dxf(dxf(tmp_path, build))
    assert g.area == pytest.approx(20 * 20 + math.pi * 100 - math.pi * 9, rel=1e-4)
    assert len(g.interiors) == 1 and bounds(g) == (0, 0, 40, 20)


def test_dxf_polylines_bulges_splines_inserts_and_units(tmp_path):
    def build(msp, doc):
        # rounded square from a closed LWPOLYLINE with bulges (quarter circles, r=5)
        b = math.tan(math.radians(90) / 4)
        msp.add_lwpolyline([(5, 0, 0), (25, 0, b), (30, 5, 0), (30, 25, b), (25, 30, 0), (5, 30, b),
                            (0, 25, 0), (0, 5, b)], format="xyb", close=True)
        blk = doc.blocks.new("HOLE")
        blk.add_circle((0, 0), 2)
        msp.add_blockref("HOLE", (15, 15))
        msp.add_spline(fit_points=[(50, 0), (60, 10), (70, 0), (60, -10), (50, 0)])
    g = load_dxf(dxf(tmp_path, build))
    parts = sorted(g.geoms, key=lambda p: p.bounds[0])
    assert parts[0].area == pytest.approx(30 * 30 - (4 - math.pi) * 25 - math.pi * 4, rel=1e-4)
    assert parts[1].bounds[0] == pytest.approx(50) and parts[1].area > 100
    inch = load_dxf(dxf(tmp_path, lambda msp, doc: msp.add_circle((0, 0), 1), units=1, name="in.dxf"))
    assert bounds(inch) == (-25.4, -25.4, 25.4, 25.4)


def test_dxf_open_contours_layers_and_tolerance(tmp_path):
    def build(msp, doc):
        msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], dxfattribs={"layer": "CUT"}, close=True)
        msp.add_line((20, 0), (30, 0), dxfattribs={"layer": "SKETCH"})      # open construction line
        msp.add_line((0, 20), (10, 20), dxfattribs={"layer": "GAP"})
        msp.add_line((10, 20), (10, 30), dxfattribs={"layer": "GAP"})
        msp.add_line((10, 30), (0, 30), dxfattribs={"layer": "GAP"})
        msp.add_line((0, 30), (0, 20.05), dxfattribs={"layer": "GAP"})      # 0.05 mm short of closing
    p = dxf(tmp_path, build)
    with pytest.raises(ValueError, match=r"2 contour\(s\) don't close"):
        load_dxf(p)
    assert load_dxf(p, layers=["CUT"]).area == pytest.approx(100)
    assert load_dxf(p, ignore_open=True).area == pytest.approx(100)
    assert load_dxf(p, layers=["GAP"], join_tolerance=0.1).area == pytest.approx(100, rel=1e-2)
    with pytest.raises(ValueError, match="no closed contours"):
        load_dxf(p, layers=["SKETCH"], ignore_open=True)


def test_dxf_job_profile_becomes_arcs(tmp_path):
    p = dxf(tmp_path, lambda msp, doc: (msp.add_circle((50, 50), 20), msp.add_circle((50, 50), 5)))
    (tmp_path / "lib.json").write_text(json.dumps({"em3": {"number": 1, "diameter": 3, "center_cutting": True}}))
    job = {"tool_library": "lib.json",
           "shapes": {"ring": {"type": "dxf", "file": "t.dxf", "fit_origin": [10, 10]}},
           "operations": [{"op": "profile", "shape": "ring", "tool": "em3", "depth": 1, "step_down": 1}]}
    (tmp_path / "j.json").write_text(json.dumps(job))
    j, base = load_job(tmp_path / "j.json")
    paths = run_job(j, base)
    minx, miny, _, maxx, maxy, _ = paths[0].bounds()
    assert tuple(round(v, 3) for v in (minx, miny, maxx, maxy)) == (8.5, 8.5, 51.5, 51.5)   # r 20 + 1.5 around (30, 30)
    g = job_post(j).emit(paths)
    assert sum(l.startswith(("G2 ", "G3 ")) for l in g.splitlines()) >= 4


def _svg_job(tmp_path, shape_extra=None, stock_x=(0, 200)):
    """An SVG drawn at 5..45 x 5..25 in its own coordinates (Y down), so camcore puts it at Y -25..-5."""
    (tmp_path / "art.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="30mm" '
                                      'viewBox="0 0 50 30"><rect x="5" y="5" width="40" height="20"/></svg>')
    return {"tool_library": str(EX / "tools.json"),
            "stock": {"type": "box", "x": list(stock_x), "y": [0, 100], "z": [-10, 0]},
            "shapes": {"art": {"type": "svg", "file": "art.svg", **(shape_extra or {})}},
            "operations": [{"op": "pocket", "shape": "art", "tool": "em3", "depth": 1, "step_down": 1}]}


def test_shape_off_the_stock_says_how_to_place_it(tmp_path):
    job = _svg_job(tmp_path)
    with pytest.raises(ValueError) as e:
        run_job(job, tmp_path)
    assert str(e.value) == (
        "shape 'art' lies at X 5..45 Y -25..-5, the stock at X 0..200 Y 0..100: it is completely off the "
        'stock. Add "fit_origin": [x, y] to the shape to put its lower-left corner there, e.g. [80, 40] '
        "to centre it on the stock")
    job["shapes"]["art"]["fit_origin"] = [80, 40]
    paths = run_job(job, tmp_path)
    assert not [n for tp in paths for n in tp.notes if "off the stock" in n]


def test_shape_partly_off_the_stock_is_a_note(tmp_path):
    paths = run_job(_svg_job(tmp_path, {"fit_origin": [-10, 40]}), tmp_path)
    notes = [n for tp in paths for n in tp.notes if "off the stock" in n]
    assert notes == ["#1: shape 'art' lies at X -10..30 Y 40..60, the stock at X 0..200 Y 0..100: part of it is "
                     'off the stock. If that is not meant, add "fit_origin": [x, y] to the shape to put its '
                     "lower-left corner there, e.g. [80, 40] to centre it on the stock"]


def test_shape_covering_the_whole_stock_is_facing(tmp_path):
    job = _svg_job(tmp_path, {"fit_origin": [80, 40]})
    job["shapes"]["face"] = {"type": "rect", "x": -12, "y": -12, "w": 224, "h": 124}
    job["operations"].append({"op": "pocket", "shape": "face", "tool": "em6", "depth": 0.5, "step_down": 0.5})
    assert not [n for tp in run_job(job, tmp_path) for n in tp.notes if "off the stock" in n]


def test_shape_bigger_than_the_stock(tmp_path):
    with pytest.raises(ValueError, match=r"completely off the stock. It is 40 x 20 mm, bigger than the stock: scale the drawing"):
        run_job(_svg_job(tmp_path, stock_x=(100, 120)), tmp_path)


def test_missing_references_say_what_is_missing(tmp_path):
    job = _svg_job(tmp_path, {"fit_origin": [80, 40]})
    op = job["operations"][0]
    del op["shape"]
    with pytest.raises(ValueError, match=r"^operation '#1' \(pocket\) has no shape: pick one \(shapes: art\)$"):
        run_job(job, tmp_path)
    op["shape"] = "recess"
    with pytest.raises(ValueError, match=r"shape 'recess' is not in the job's shapes \(shapes: art\)"):
        run_job(job, tmp_path)
    op["shape"], op["tool"] = "art", "em99"
    with pytest.raises(ValueError, match=r"tool 'em99' is not in the tool library \(tools: ball3, em3"):
        run_job(job, tmp_path)
    job["operations"] = [{"op": "drill", "tool": "em3", "points": [], "depth": 1}]
    with pytest.raises(ValueError, match=r"has no points"):
        run_job(job, tmp_path)
