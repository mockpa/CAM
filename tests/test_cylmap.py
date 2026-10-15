import math

import numpy as np
import pytest
import trimesh

from camcore.cylmap import radial_map, tool_map
from camcore import stl
from camcore.stl import load_stl
from camcore.tools import Tool


def revolve(rings, n=360):
    return stl.revolve(rings, n)


def col(cmap, x):
    return int(np.argmin(np.abs(cmap.xs - x)))


# --- import ------------------------------------------------------------------

def test_load_stl_roundtrip_and_errors(tmp_path):
    p = tmp_path / "c.stl"
    revolve([(0, 10), (40, 10)], n=32).export(p)
    mesh = load_stl(p)
    assert mesh.is_watertight
    assert np.allclose(mesh.bounds, [[0, -10, -10], [40, 10, 10]], atol=1e-6)
    (tmp_path / "e.stl").write_text("solid e\nendsolid e\n")
    with pytest.raises(ValueError):
        load_stl(tmp_path / "e.stl")


# --- r(x, A) -----------------------------------------------------------------

def test_cylinder_radius_is_exact_on_vertices_and_facets():
    cmap = radial_map(revolve([(0, 20), (40, 20)]), x_step=0.5, a_step=1.0)
    assert cmap.r.shape == (81, 360) and cmap.hit.all()
    assert np.allclose(cmap.r, 20.0, atol=1e-9)
    # 12-sided prism with a 0.5 deg grid: radius = apothem / cos(angle from facet middle)
    cmap = radial_map(revolve([(0, 20), (40, 20)], n=12), x_step=5, a_step=0.5)
    a = np.radians(cmap.angles)
    half = math.pi / 12
    expect = 20 * math.cos(half) / np.cos((a % (2 * half)) - half)
    assert np.allclose(cmap.r, expect[None, :], atol=1e-9)


def test_cone_radius_is_linear_in_x():
    cmap = radial_map(revolve([(0, 10), (40, 30)]), x_step=0.5, a_step=1.0)
    assert np.allclose(cmap.r, (10 + 0.5 * cmap.xs)[:, None], atol=1e-9)


def test_angle_convention_matches_viewer_and_verify():
    # tool looks along u(A) = (0, -sin A, cos A): a block above the axis faces it at A = 0,
    # a block on the -Y side at A = 90, nothing at A = 180
    up = trimesh.creation.box(extents=(10, 4, 4)); up.apply_translation((5, 0, 20))
    side = trimesh.creation.box(extents=(10, 4, 4)); side.apply_translation((5, -20, 0))
    for mesh, a_hit in ((up, 0), (side, 90)):
        cmap = radial_map(mesh, x_step=1, a_step=1, x_range=(1, 9))
        assert np.allclose(cmap.r[:, a_hit], 22.0) and cmap.hit[:, a_hit].all()
        assert not cmap.hit[:, (a_hit + 180) % 360].any() and (cmap.r[:, (a_hit + 180) % 360] == 0).all()


def test_rays_past_the_model_end_miss():
    cmap = radial_map(revolve([(0, 20), (40, 20)]), x_step=1, a_step=10, x_range=(-5, 45))
    assert not cmap.hit[cmap.xs < 0].any() and not cmap.hit[cmap.xs > 40].any()
    assert cmap.hit[(cmap.xs >= 0) & (cmap.xs <= 40)].all()


# --- tool dilation -----------------------------------------------------------

FLAT = Tool("flat6", 6.0, "flat")
BALL = Tool("ball6", 6.0, "ball")
VBIT = Tool("v90", 6.0, "vbit", v_angle=90)


@pytest.fixture(scope="module")
def cyl():
    return radial_map(revolve([(0, 20), (40, 20)]), x_step=0.5, a_step=1.0, x_range=(-10, 50))


@pytest.mark.parametrize("tool", [FLAT, BALL, VBIT], ids=lambda t: t.kind)
def test_tool_on_cylinder_touches_at_the_tip(cyl, tool):
    rt = tool_map(cyl, tool)
    inside = (cyl.xs >= 0) & (cyl.xs <= 40)
    assert np.allclose(rt.r[inside], 20.0, atol=1e-9)
    assert (rt.r >= cyl.r - 1e-9).all()
    assert np.allclose(rt.r[cyl.xs < -3.01], 0.0) and np.allclose(rt.r[cyl.xs > 43.01], 0.0)


@pytest.mark.parametrize("dx", [0.5, 1.5, 2.5, 3.0])
def test_tool_past_the_cylinder_end_rides_on_the_edge(cyl, dx):
    # tip beyond the end face at x = 40: the edge circle at radius 20 limits each shape exactly
    i = col(cyl, 40 + dx)
    assert np.allclose(tool_map(cyl, FLAT).r[i], 20.0)
    assert np.allclose(tool_map(cyl, BALL).r[i], 20 - (3 - math.sqrt(9 - dx * dx)), atol=1e-9)
    assert np.allclose(tool_map(cyl, VBIT).r[i], 20 - dx, atol=1e-9)


@pytest.fixture(scope="module")
def cone():
    # radius 10 at x = 0 to 30 at x = 40: slope 0.5
    return radial_map(revolve([(0, 10), (40, 30)]), x_step=0.1, a_step=1.0)


def test_flat_on_cone_rests_on_its_uphill_edge(cone):
    i = col(cone, 20)
    assert np.allclose(tool_map(cone, FLAT).r[i], 10 + 0.5 * 23, atol=1e-9)


def test_ball_on_cone_matches_tangent_contact(cone):
    # ball tangent to a flank of slope angle alpha: tip = r(x) + R (1 / cos(alpha) - 1)
    alpha = math.atan(0.5)
    exact = 20 + 3 * (1 / math.cos(alpha) - 1)
    got = tool_map(cone, BALL).r[col(cone, 20)]
    assert np.all(got <= exact + 1e-9) and np.allclose(got, exact, atol=0.005)


def test_vbit_steeper_than_cone_touches_with_its_tip(cone):
    # 90 deg V-bit flank slope 1 > cone slope 0.5: only the tip touches
    assert np.allclose(tool_map(cone, VBIT).r[col(cone, 20)], 20.0, atol=1e-9)


@pytest.mark.parametrize("tool", [FLAT, BALL, VBIT, Tool("v60", 6.0, "vbit", v_angle=60)], ids=lambda t: t.name)
def test_closed_form_matches_dense_search_on_off_axis_model(tool):
    # off-axis bump on a shaft: for random cells, search rho densely along every source ray
    bump = trimesh.creation.icosphere(subdivisions=3, radius=6); bump.apply_translation((10, 4, 12))
    body = trimesh.util.concatenate([revolve([(0, 8), (20, 8)], n=72), bump])
    cmap = radial_map(body, x_step=0.5, a_step=3.0)
    rt = tool_map(cmap, tool)
    R, n, m = tool.radius, *cmap.r.shape
    if tool.kind == "ball":
        h = lambda d: R - np.sqrt(np.clip(R * R - d * d, 0, None))
    elif tool.kind == "vbit":
        h = lambda d: d / math.tan(math.radians(tool.v_angle) / 2)
    else:
        h = lambda d: np.zeros_like(d)
    rng = np.random.default_rng(1)
    for _ in range(40):
        i, j = rng.integers(n), rng.integers(m)
        best = -np.inf
        for k in range(-6, 7):
            if not 0 <= i + k < n:
                continue
            phi = np.radians(np.arange(m) * cmap.a_step)
            rho = cmap.r[i + k, (j + np.arange(m)) % m][:, None] * np.linspace(0, 1, 1001)[None, :]
            dx = k * cmap.x_step
            d = np.hypot(dx, rho * np.sin(phi)[:, None])
            v = np.where((d <= R + 1e-12) & (np.cos(phi)[:, None] > 0), rho * np.cos(phi)[:, None] - h(d), -np.inf)
            best = max(best, v.max())
        assert best - 1e-9 <= rt.r[i, j] <= best + 0.01
