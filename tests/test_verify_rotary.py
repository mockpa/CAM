"""verify's cylinder stock: material along rays from the axis, cut by the real tool body."""
import math

import numpy as np
import pytest

from camcore.verify import Machine, Stock, run

TOOLS = {1: {"diameter": 6, "kind": "flat", "flute_length": 30, "center_cutting": True},
         2: {"diameter": 6, "kind": "ball", "flute_length": 30, "center_cutting": True},
         3: {"diameter": 6, "kind": "vbit", "v_angle": 60, "flute_length": 30, "center_cutting": True}}
CYL = {"type": "cylinder", "diameter": 40, "x": [0, 60]}


def machine():
    return Machine(travel={"x": [-500, 500], "y": [-500, 500], "z": [-500, 500]},
                   work_offset={"x": 0, "y": 0, "z": 0}, has_a=True, tools=TOOLS)


def inside(tool, dx, phi, z, rho):
    """Brute-force body membership of the ray points at distances rho (array)."""
    r = tool["diameter"] / 2
    lat, hgt = rho * abs(math.sin(phi)), rho * math.cos(phi)
    d = np.hypot(dx, lat)
    if tool["kind"] == "ball":
        prof = r - np.sqrt(np.clip(r * r - d * d, 0, None))
    elif tool["kind"] == "vbit":
        prof = d / math.tan(math.radians(tool["v_angle"]) / 2)
    else:
        prof = 0.0
    return (d <= r) & (hgt >= z + prof)


@pytest.mark.parametrize("t", [1, 2, 3])
def test_ray_interval_matches_brute_force(t):
    tool = TOOLS[t]
    rng = np.random.default_rng(t)
    dx = rng.uniform(-3.2, 3.2, 300)
    phi = rng.uniform(-math.pi / 2, math.pi / 2, 300)
    phi[:20] = 0.0
    rhos = np.linspace(0, 60, 60001)
    for z in (2.0, 12.5):
        lo, hi = Stock._ray_interval(dx, phi, z, tool)
        for k in range(len(dx)):
            ins = rhos[inside(tool, dx[k], phi[k], z, rhos)]
            if not len(ins):
                assert not lo[k] <= hi[k] or hi[k] - lo[k] < 2e-3, (k, lo[k], hi[k])
                continue
            assert abs(lo[k] - ins[0]) < 2e-3
            assert hi[k] >= 60 - 2e-3 if ins[-1] == 60 else abs(hi[k] - ins[-1]) < 2e-3


def test_ring_cut_leaves_exact_radius_and_rapids_see_it():
    # flat D6 cuts a full ring at Z15 (radius 15) around X30
    ring = "T1 M6\nG43 H1\nM3 S10000\nG0 Z30\nG0 X30 A0\nG1 Z15 F100\nG93\nG1 A360 F1\nG94\nG0 Z30\n"
    issues, _, sim = run(ring, machine(), CYL)
    assert [str(i) for i in issues if i.level == "error"] == []
    ix = int(round((30 - sim.x0) / sim.res))
    assert np.allclose(sim.h[ix], 15.0, atol=0.01)               # the ring itself, every angle (0.25 mm steps)
    assert np.allclose(sim.h[ix - 7], 20.0) and np.allclose(sim.h[ix + 7], 20.0)   # 3.5 mm off: untouched
    # rapids inside the ring are fine, a rapid next to it at the same height hits the wall
    ok = ring + "G0 Z15.5\nG0 A90\nG0 Z30\n"
    assert [i for i in run(ok, machine(), CYL)[0] if i.level == "error"] == []
    bad = ring + "G0 X36\nG0 Z15.5\nG0 A90\n"
    assert any("CRASH: rapid" in str(i) for i in run(bad, machine(), CYL)[0])


def test_side_material_below_the_stock_surface_hits_the_tool():
    # stock already turned down to radius 10, with a groove to radius 8 for |A| < 10 deg.
    # A flat D6 tip at radius 8.2 over the groove is clear of the groove floor, but the
    # groove walls at 10..17 deg reach into its body (lateral 1.7..2.9 mm, height 9.6..9.8).
    # Measuring the footprint along the original surface (radius 20) only saw +-8.6 deg.
    s = Stock(CYL)
    s.h[:] = 10.0
    ang = (np.arange(s.h.shape[1]) * s.a_res + 180) % 360 - 180
    s.h[:, np.abs(ang) < 10] = 8.0
    pen = s.interference(30, 0, 8.2, TOOLS[1])
    first = np.radians(np.abs(ang)[np.abs(ang) >= 10].min())    # nearest wall ray
    assert pen == pytest.approx(10 - 8.2 / math.cos(first), abs=1e-9)
    assert pen > 1.5
    assert s.interference(30, 0, 10.0, TOOLS[1]) <= 0          # on top of the walls: clear


def test_tool_below_the_axis_is_an_error():
    issues, _, _ = run("T1 M6\nG43 H1\nG0 X70 Z-1\n", machine(), CYL)
    assert any("below the rotary centreline" in str(i) for i in issues)
