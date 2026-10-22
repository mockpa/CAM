"""The viewer's stock surface: cells cut through the block are holes, not a floor."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).parent.parent / "camcore" / "viewer_template.html"


def stock_index(nx, ny, h, floor, wrap, tmp_path):
    tpl = TEMPLATE.read_text()
    fn = tpl[tpl.index("function stockIndex("):tpl.index("function refreshStock(")]
    js = fn + f"console.log(JSON.stringify(stockIndex({nx}, {ny}, {json.dumps(h)}, {json.dumps(floor)}, {json.dumps(wrap)})));"
    (tmp_path / "t.js").write_text(js)
    out = subprocess.run(["node", str(tmp_path / "t.js")], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cut_through_cells_are_left_out(tmp_path):
    # 4 x 4 corners = 3 x 3 cells, block from -10 to 0; corners k = i * ny + j
    h = [0.0] * 16
    assert len(stock_index(4, 4, h, -10, False, tmp_path)) == 9 * 6          # uncut: every cell
    for k in (5, 6, 9, 10):                                                 # the middle cell's corners
        h[k] = -10.3                                                        # below the bottom: through
    tris = stock_index(4, 4, h, -10, False, tmp_path)
    assert len(tris) == 8 * 6                                               # the middle cell is a hole
    assert [5, 9, 6, 9, 10, 6] not in [tris[i:i + 6] for i in range(0, len(tris), 6)]
    h[10] = -9.3                                                            # a tab corner: material left
    assert len(stock_index(4, 4, h, -10, False, tmp_path)) == 9 * 6
    h[10] = -10.3
    assert len(stock_index(4, 4, h, None, True, tmp_path)) == 3 * 4 * 6     # rotary: never holes, wraps
