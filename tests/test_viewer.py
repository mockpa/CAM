"""The viewer's stock model, run in node: grid size, hole cells, the V-groove sweep,
normals and the depth colours."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).parent.parent / "camcore" / "viewer_template.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def functions(*names):
    """Source of the named top-level functions in the template (matched braces)."""
    tpl = TEMPLATE.read_text()
    out = []
    for name in names:
        start = re.search(rf"^function {name}\(", tpl, re.M).start()
        depth, i = 0, tpl.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(tpl[i], 0)
            i += 1
            if depth == 0:
                break
        out.append(tpl[start:i])
    return "\n".join(out)


def js(names, expr, tmp_path, prelude=""):
    code = functions(*names) + "\n" + prelude + f"\nconsole.log(JSON.stringify({expr}));"
    (tmp_path / "t.js").write_text(code)
    out = subprocess.run(["node", str(tmp_path / "t.js")], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def cells(index):
    """Triangles as 6-index cells, without the degenerate ones (holes)."""
    return [index[i:i + 6] for i in range(0, len(index), 6) if len(set(index[i:i + 6])) > 1]


def stock_index(nx, ny, h, floor, wrap, tmp_path):
    expr = f"Array.from(stockIndex({nx}, {ny}, {json.dumps(h)}, {json.dumps(floor)}, {json.dumps(wrap)}))"
    return js(["cellRow", "fillIndex", "stockIndex"], expr, tmp_path)


def test_cut_through_cells_are_left_out(tmp_path):
    # 4 x 4 corners = 3 x 3 cells, block from -10 to 0; corners k = i * ny + j
    h = [0.0] * 16
    idx = stock_index(4, 4, h, -10, False, tmp_path)
    assert len(idx) == 9 * 6 and len(cells(idx)) == 9                     # uncut: every cell
    for k in (5, 6, 9, 10):                                                 # the middle cell's corners
        h[k] = -10.3                                                        # below the bottom: through
    idx = stock_index(4, 4, h, -10, False, tmp_path)
    assert len(idx) == 9 * 6                                                # fixed size, updated in place
    assert len(cells(idx)) == 8                                             # the middle cell is a hole
    assert [5, 9, 6, 9, 10, 6] not in cells(idx)
    h[10] = -9.3                                                            # a tab corner: material left
    assert len(cells(stock_index(4, 4, h, -10, False, tmp_path))) == 9
    h[10] = -10.3
    assert len(cells(stock_index(4, 4, h, None, True, tmp_path))) == 3 * 4  # rotary: never holes, wraps


def test_fill_index_redoes_only_the_given_rows(tmp_path):
    prelude = """
    const h = new Array(25).fill(0), idx = stockIndex(5, 5, h, -10, false);
    for (const k of [6, 7, 11, 12, 16, 17]) h[k] = -11;      // cells (1,1) and (2,1) cut through
    fillIndex(idx, 5, 5, h, -10, false, 2, 2);               // only cell row 2 redone
    """
    idx = js(["cellRow", "fillIndex", "stockIndex"], "Array.from(idx)", tmp_path, prelude)
    row = lambda i: [idx[(i * 4 + j) * 6:(i * 4 + j + 1) * 6] for j in range(4)]
    assert all(len(set(c)) > 1 for c in row(1))                             # stale until it is redone
    assert len(set(row(2)[1])) == 1 and sum(len(set(c)) > 1 for c in row(2)) == 3


def test_stock_grid_keeps_the_cell_budget(tmp_path):
    names = ["stockGrid"]
    pre = "const STOCK_CELLS = 800000, MIN_RES = 0.1;"
    big = js(names, 'stockGrid({type: "box", x: [0, 600], y: [0, 400], z: [-18, 0]})', tmp_path, pre)
    assert big["nx"] * big["ny"] <= 800000 * 1.01 and big["res"] == pytest.approx(math.sqrt(600 * 400 / 800000))
    small = js(names, 'stockGrid({type: "box", x: [0, 40], y: [0, 30], z: [-6, 0]})', tmp_path, pre)
    assert small["res"] == 0.1 and (small["nx"], small["ny"]) == (401, 301)  # never finer than MIN_RES
    cyl = js(names, 'stockGrid({type: "cylinder", x: [0, 200], diameter: 50})', tmp_path, pre)
    assert cyl["base"] == 25 and cyl["nx"] * cyl["ny"] <= 800000 * 1.01 and 90 <= cyl["ny"] <= 2880


def test_sweep_cuts_an_exact_v_groove(tmp_path):
    # a 90 deg V-bit 1 mm deep along X across a 0.1 mm grid: every cell sits at the V's height
    pre = """
    const g = {nx: 101, ny: 61, x0: 0, y0: 0, res: 0.1, base: 0}, h = new Float32Array(101 * 61);
    const tl = {kind: "vbit", diameter: 6, v_angle: 90};
    const rows = sweep(h, g, [1, 3, -1, 0], [9, 3, -1, 0], tl);
    """
    out = js(["profile", "reach", "sweep"], "{rows, h: Array.from(h)}", tmp_path, "const SWEEP_DZ = 0.05;" + pre)
    h = out["h"]
    assert out["rows"] == [1, 99]                                         # x 0 and 10: the V only touches
    for i in (20, 50, 80):                  # along the groove, away from its rounded ends
        for j in range(61):
            want = min(0.0, -1 + abs(j * 0.1 - 3))
            assert h[i * 61 + j] == pytest.approx(want, abs=1e-5)
    assert h[50 * 61 + 30] == pytest.approx(-1, abs=1e-6)                  # the sharp bottom is there
    assert h[0 * 61 + 30] == pytest.approx(0, abs=1e-6)                     # 1 mm before the start: tip radius 1


def test_sweep_cuts_a_ramp_in_pieces(tmp_path):
    pre = """
    const g = {nx: 101, ny: 11, x0: 0, y0: 0, res: 0.1, base: 0}, h = new Float32Array(101 * 11);
    sweep(h, g, [0, 0.5, 0, 0], [10, 0.5, -2, 0], {kind: "flat", diameter: 1});
    """
    h = js(["profile", "reach", "sweep"], "Array.from(h)", tmp_path, "const SWEEP_DZ = 0.05;" + pre)
    for i in range(0, 101, 5):              # deepest where the leading edge passed: 0.5 mm further on
        true = -0.2 * min(i * 0.1 + 0.5, 10)
        assert true - 1e-6 <= h[i * 11 + 5] <= true + 0.05 + 1e-6          # within one piece, never deeper
    assert h[100 * 11 + 5] == pytest.approx(-2, abs=1e-6)


def test_grid_normals(tmp_path):
    # a plane z = 0.5 x on a box grid: normal (-0.5, 0, 1) / |..|, the same at the edges
    pre = """
    const nx = 4, ny = 3, pos = new Float32Array(nx * ny * 3), nrm = new Float32Array(nx * ny * 3);
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) { const o = 3 * (i * ny + j); pos[o] = i; pos[o + 1] = j; pos[o + 2] = 0.5 * i; }
    gridNormals(pos, nrm, nx, ny, false, 0, nx - 1);
    """
    n = js(["gridNormals"], "Array.from(nrm)", tmp_path, pre)
    l = math.hypot(0.5, 1)
    for k in range(12):
        assert n[3 * k:3 * k + 3] == pytest.approx([-0.5 / l, 0, 1 / l], abs=1e-6)
    # rotary: a cylinder of radius 10 round X, normals radial (inward, see gridNormals)
    pre = """
    const nx = 3, ny = 36, pos = new Float32Array(nx * ny * 3), nrm = new Float32Array(nx * ny * 3);
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) { const a = j / ny * 2 * Math.PI, o = 3 * (i * ny + j);
      pos[o] = i; pos[o + 1] = -10 * Math.sin(a); pos[o + 2] = 10 * Math.cos(a); }
    gridNormals(pos, nrm, nx, ny, true, 0, nx - 1);
    """
    n = js(["gridNormals"], "Array.from(nrm)", tmp_path, pre)
    for j in (0, 9, 35):                    # including both ends of the wrap
        a = j / 36 * 2 * math.pi
        assert n[3 * (36 + j):3 * (36 + j) + 3] == pytest.approx([0, math.sin(a), -math.cos(a)], abs=1e-6)


def test_depth_bands(tmp_path):
    names = ["bandStep", "shade", "deepestCut"]
    assert js(names, "[0.3, 1, 3, 6, 18, 40, 1000].map(bandStep)", tmp_path) == [0.1, 0.2, 0.5, 1, 2.5, 5, 100]
    s = js(names, "[0, 0.004, 0.4, 0.6, 2.9, 3, 5].map(d => shade(d, 3, 0.5))", tmp_path)
    assert s[0] == s[1] == 0                                                # uncut stays the stock colour
    assert 0 < s[2] < s[4] <= 1 and s[4] == pytest.approx(s[5]) == pytest.approx(s[6])  # capped at the span
    assert s[3] == pytest.approx(0.15 + 0.85 * 0.2 + 0.07)                  # the second band is lighter
    assert js(names, "deepestCut([5, 5, 7], [5, 2, 6.5])", tmp_path) == 3   # from each cell's own uncut height
