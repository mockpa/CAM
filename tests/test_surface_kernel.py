"""rust/surface_kernel (camcore_surface_kernel), the drop-cutter and waterline behind
camcore.surface, against independent references:

- OpenCAMLib, which camcore used before (where it is installed): the same drop
  heights for flat, ball and bull-nose cutters, grown and shrunk;
- brute force: dense points on the triangles, the cutter profile subtracted; the
  kernel must never be below it (that would be a gouge) and only sampling error above;
- closed forms at a block edge, for the grown V-bit too (OpenCAMLib's differs).
"""
import math

import numpy as np
import pytest
import trimesh

from camcore import surface
from camcore.relief.relief3d import block_mesh
from camcore.tools import Tool

K = pytest.importorskip("camcore_surface_kernel")
EM6 = Tool("em6", 6.0, "flat", number=2)
BALL3 = Tool("ball3", 3.0, "ball", number=3)
BULL6 = Tool("bull6", 6.0, "bull", corner_radius=1.0, number=6)
BULL8 = Tool("bull8", 8.0, "bull", corner_radius=0.4, number=7)
V90 = Tool("v90", 6.0, "vbit", v_angle=90, number=5)
V60 = Tool("v60", 6.35, "vbit", v_angle=60, number=4)
TOOLS = [EM6, BALL3, BULL6, BULL8, V90, V60]


def sphere_cap(centre, r, z_cut, rings=30, sections=72):
    cx, cy, cz = centre
    top = math.acos((z_cut - cz) / r)
    v = [(cx, cy, cz + r)]
    for i in range(1, rings + 1):
        ph = top * i / rings
        v += [(cx + r * math.sin(ph) * math.cos(t), cy + r * math.sin(ph) * math.sin(t), cz + r * math.cos(ph))
              for t in np.linspace(0, 2 * math.pi, sections, endpoint=False)]
    f = [(0, 1 + j, 1 + (j + 1) % sections) for j in range(sections)]
    for i in range(rings - 1):
        a, b = 1 + i * sections, 1 + (i + 1) * sections
        for j in range(sections):
            k = (j + 1) % sections
            f += [(a + j, b + j, b + k), (a + j, b + k, a + k)]
    return trimesh.Trimesh(np.array(v), np.array(f), process=False)


def block():
    b = trimesh.creation.box(extents=(10, 10, 5))
    b.apply_translation((5, 5, -2.5))
    return b


def part():
    plate = trimesh.creation.box(extents=(26, 16, 3))
    plate.apply_translation((17, 12, -9.5))
    boss = trimesh.creation.cylinder(radius=3, height=7, sections=64)
    boss.apply_translation((24, 12, -5.5))
    return trimesh.util.concatenate([plate, sphere_cap((11, 12, -9), 5.0, -8.0), boss])


def valley():
    xs, ys = np.arange(0, 40.01, 0.5), np.arange(0, 30.01, 0.5)
    X, Y = np.meshgrid(xs, ys)
    return block_mesh(xs, ys, np.minimum(0.0, -6 + 0.05 * X + 0.5 * np.abs(Y - 15)), -10)


def waves():
    """A wavy relief like the relief examples: smooth slopes, ridges and saddles."""
    xs, ys = np.arange(0, 30.01, 0.5), np.arange(0, 20.01, 0.5)
    X, Y = np.meshgrid(xs, ys)
    return block_mesh(xs, ys, -3 + 2.5 * np.sin(X / 3) * np.cos(Y / 2.5) - 0.02 * (X - 15) ** 2 / 4, -10)


MESHES = {"block": block, "part": part, "valley": valley, "waves": waves}


def random_xy(mesh, n, seed):
    (x0, y0, _), (x1, y1, _) = mesh.bounds
    return np.random.default_rng(seed).uniform((x0 - 4, y0 - 4), (x1 + 4, y1 + 4), size=(n, 2))


# --- OpenCAMLib, the drop-cutter camcore used before ---------------------------------

def ocl_drop(mesh, tool, xy, floor, leave):
    """camcore.surface.drop_points as it was with OpenCAMLib (vertical facets left out,
    the mesh moved next to the origin)."""
    o = pytest.importorskip("opencamlib")
    tri = np.asarray(mesh.triangles, dtype=float)
    shift = np.round((tri.reshape(-1, 3).min(axis=0) + tri.reshape(-1, 3).max(axis=0)) / 2)
    tri = tri - shift
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    keep = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2 >= 1e-8
    s = o.STLSurf()
    for a, b, c in tri[keep]:
        s.addTriangle(o.Triangle(o.Point(*a), o.Point(*b), o.Point(*c)))
    d = tool.diameter
    if leave < 0:
        d -= 2 * -leave
    cut = {"flat": lambda: o.CylCutter(d, 1000.0), "ball": lambda: o.BallCutter(d, 1000.0),
           "bull": lambda: (o.BullCutter(d, tool.corner_radius + min(leave, 0.0), 1000.0)
                            if tool.corner_radius + min(leave, 0.0) > 1e-9 else o.CylCutter(d, 1000.0)),
           "vbit": lambda: o.ConeCutter(d, math.radians(tool.v_angle) / 2, 1000.0)}[tool.kind]()
    if leave > 0:
        cut = cut.offsetCutter(leave)
    k = surface.tip_shift(tool, leave)
    bdc = o.BatchDropCutter()
    bdc.setSTL(s)
    bdc.setCutter(cut)
    for x, y in xy:
        bdc.appendPoint(o.CLPoint(float(x - shift[0]), float(y - shift[1]), float(floor - k - shift[2])))
    bdc.run()
    return np.maximum(np.array([p.z for p in bdc.getCLPoints()]) + shift[2] + k, floor)


@pytest.mark.parametrize("name", list(MESHES))
@pytest.mark.parametrize("tool", [EM6, BALL3, BULL6, BULL8], ids=lambda t: t.name)
@pytest.mark.parametrize("leave", [0.0, 0.5, -0.25])
def test_drop_matches_opencamlib(name, tool, leave):
    mesh = MESHES[name]()
    xy = random_xy(mesh, 1500, 5)
    got = surface.drop_points(mesh, tool, xy, -40.0, leave)
    want = ocl_drop(mesh, tool, xy, -40.0, leave)
    # OpenCAMLib solves the bull nose's edge contact numerically: ~1e-9
    assert np.abs(got - want).max() < 1e-8


@pytest.mark.parametrize("name", list(MESHES))
@pytest.mark.parametrize("tool", [V90, V60], ids=lambda t: t.name)
def test_cone_is_never_below_opencamlib(name, tool):
    """Where they differ OpenCAMLib's cone sinks into the model (see the brute force)."""
    mesh = MESHES[name]()
    xy = random_xy(mesh, 1500, 6)
    for leave in (0.0, -0.25):
        got = surface.drop_points(mesh, tool, xy, -40.0, leave)
        assert (got - ocl_drop(mesh, tool, xy, -40.0, leave)).min() > -1e-8


# --- brute force ----------------------------------------------------------------------

def profile(tool, leave, d):
    """The cutter's surface above its real tip at radial distance d, grown or shrunk by
    `leave`, straight from its geometry (Tool.profile is the unchanged cutter)."""
    cone, a, l, k = surface.cutter(tool, leave)
    R = a + l
    d = np.minimum(d, R)
    if not cone:
        e = np.clip(d - a, 0, None)
        f = l - np.sqrt(np.clip(l * l - e * e, 0, None))
    elif l == 0:
        f = k * d
    else:
        # grown cone: the set of points within l of the cone, lowest point first
        q = math.sqrt(1 + k * k)
        r1 = l * k / q
        f = np.where(d <= r1, l - np.sqrt(np.clip(l * l - d * d, 0, None)),
                     np.where(d <= a + r1, k * d + l - l * q,
                              k * a + l - np.sqrt(np.clip(l * l - (d - a) ** 2, 0, None))))
    return f - surface.tip_shift(tool, leave), R


def brute_drop(mesh, tool, x, y, leave, n=120):
    """Lower bound of the drop height: dense barycentric points on the triangles in reach."""
    _, R = profile(tool, leave, np.zeros(1))
    tri = np.asarray(mesh.triangles)
    near = tri[(tri[:, :, 0].min(1) <= x + R) & (tri[:, :, 0].max(1) >= x - R)
               & (tri[:, :, 1].min(1) <= y + R) & (tri[:, :, 1].max(1) >= y - R)]
    if not len(near):
        return -np.inf
    u, v = np.meshgrid(np.arange(n + 1) / n, np.arange(n + 1) / n)
    keep = u + v <= 1
    u, v = u[keep], v[keep]
    pts = (near[:, 0, None] + u[None, :, None] * (near[:, 1] - near[:, 0])[:, None]
           + v[None, :, None] * (near[:, 2] - near[:, 0])[:, None]).reshape(-1, 3)
    # and each triangle's point straight under the axis (a V-bit's point sits there on a
    # large flat facet, which the grid samples only every few tenths of a mm)
    a, e1, e2 = near[:, 0], near[:, 1] - near[:, 0], near[:, 2] - near[:, 0]
    det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    ok = np.abs(det) > 1e-12
    bu = np.where(ok, ((x - a[:, 0]) * e2[:, 1] - (y - a[:, 1]) * e2[:, 0]) / np.where(ok, det, 1), -1)
    bv = np.where(ok, (e1[:, 0] * (y - a[:, 1]) - e1[:, 1] * (x - a[:, 0])) / np.where(ok, det, 1), -1)
    under = ok & (bu >= 0) & (bv >= 0) & (bu + bv <= 1)
    pts = np.vstack([pts, (a + bu[:, None] * e1 + bv[:, None] * e2)[under]])
    # and the edges every 0.002 mm where they are in reach (a point resting on a long edge)
    p0 = near.reshape(-1, 3)
    p1 = near[:, [1, 2, 0]].reshape(-1, 3)
    e = p1 - p0
    a2 = e[:, 0] ** 2 + e[:, 1] ** 2
    b = (p0[:, 0] - x) * e[:, 0] + (p0[:, 1] - y) * e[:, 1]
    c = (p0[:, 0] - x) ** 2 + (p0[:, 1] - y) ** 2 - R * R
    disc = b * b - a2 * c
    ok = (a2 > 1e-18) & (disc >= 0)
    sq = np.sqrt(np.where(ok, disc, 0))
    t0 = np.clip((-b - sq) / np.where(ok, a2, 1), 0, 1)
    t1 = np.clip((-b + sq) / np.where(ok, a2, 1), 0, 1)
    ok &= t1 > t0
    n_e = np.where(ok, np.ceil((t1 - t0) * np.sqrt(a2) / 0.002).astype(int) + 1, 0)
    if n_e.sum():
        idx = np.repeat(np.arange(len(p0)), n_e)
        k = np.arange(n_e.sum()) - np.repeat(np.cumsum(n_e) - n_e, n_e)
        t = t0[idx] + (t1[idx] - t0[idx]) * k / np.maximum(n_e[idx] - 1, 1)
        pts = np.vstack([pts, p0[idx] + t[:, None] * e[idx]])
    d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
    ok = d <= R
    if not ok.any():
        return -np.inf
    f, _ = profile(tool, leave, d[ok])
    return float((pts[ok, 2] - f).max())


@pytest.mark.parametrize("name", ["part", "valley", "waves"])
@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
@pytest.mark.parametrize("leave", [0.0, 0.5, -0.25])
def test_drop_is_the_highest_contact_by_brute_force(name, tool, leave):
    """Never below a sampled model point (no gouge), and above the densest sample only by
    what the sampling misses (the cutter's slope times the sample spacing)."""
    mesh = MESHES[name]()
    xy = random_xy(mesh, 25, 7)
    got = surface.drop_points(mesh, tool, xy, -40.0, leave)
    for (x, y), z in zip(xy, got):
        b = max(brute_drop(mesh, tool, x, y, leave), -40.0)
        assert b <= z + 1e-9                 # no sampled point inside the cutter
        assert z - b < 0.005                 # and touching the model (or the floor)


def test_grown_vbit_is_the_exact_offset():
    """A 90 deg V-bit beside the block edge (X10, Z0), grown by 0.5: the cutter stays
    0.5 off the edge, whichever part of it is nearest."""
    b = block()
    # 1 mm out the cone side is nearest: the edge is (z + 1) cos 45 below it
    assert surface.drop_points(b, V90, [(11.0, 5.0)], -20.0, 0.5)[0] == pytest.approx(math.sqrt(0.5) - 1, abs=1e-12)
    # over the block's top: the grown cone's ball tip sits on it, the real tip 0.5 above
    assert surface.drop_points(b, V90, [(5.0, 5.0)], -20.0, 0.5)[0] == pytest.approx(0.5, abs=1e-12)
    # 3.45 mm out (0.45 past the rim of radius 3) the rim is nearest: 0.5 from the edge.
    # (OpenCAMLib's grown cone runs its flank straight out to radius 3.5: 0.04 mm higher here.)
    z = surface.drop_points(b, V90, [(13.45, 5.0)], -20.0, 0.5)[0]
    assert z == pytest.approx(math.sqrt(0.5 ** 2 - 0.45 ** 2) - 3, abs=1e-12)


# --- waterline ------------------------------------------------------------------------

def test_waterline_points_touch_and_are_the_same_every_run():
    mesh = waves()
    a = surface.waterline(mesh, BALL3, -3.0, 0.2, 0.5)
    b = surface.waterline(mesh, BALL3, -3.0, 0.2, 0.5)
    assert len(a) >= 2 and all(np.array_equal(p, q) for p, q in zip(a, b))
    for lp in a:                      # every point within 1e-6 mm of where the cutter touches
        assert lp.shape[1] == 2
        up = surface.drop_points(mesh, BALL3, lp, -40.0, 0.5 - 1e-6)
        down = surface.drop_points(mesh, BALL3, lp, -40.0, 0.5 + 1e-6)
        assert (up <= -3.0 + 1e-9).all() and (down > -3.0).all()


def test_kernel_refuses_bad_meshes_and_cutters():
    with pytest.raises(ValueError, match="empty"):
        K.Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))
    with pytest.raises(ValueError, match="out of range"):
        K.Mesh(np.zeros((3, 3)), np.array([[0, 1, 5]], dtype=np.int64))
    m = surface.kernel_mesh(block())
    with pytest.raises(ValueError, match="bad cutter"):
        m.drop((False, 0.0, 0.0, 0.0), np.zeros((1, 2)), -1.0)
    with pytest.raises(ValueError, match="sampling"):
        m.waterline((False, 3.0, 0.0, 0.0), -1.0, 0.0)
    assert m.vertical == 8                   # the block's side facets: edges and vertices only
