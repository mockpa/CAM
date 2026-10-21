"""Relief height fields (relief plan phase 1): image and STL in, HeightField out."""
import math

import numpy as np
import pytest
import trimesh
from PIL import Image

from camcore.relief import heightfield as hf


def save(arr, path, mode=None):
    im = Image.fromarray(arr) if mode is None else Image.fromarray(arr).convert(mode)
    im.save(path)
    return path


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def test_8bit_image_is_grey_over_255(tmp_path):
    a = np.array([[0, 51, 255], [102, 204, 153]], dtype=np.uint8)
    raw = hf.load_image(save(a, tmp_path / "g.png"))
    assert raw.dtype == np.float32 and raw.shape == (2, 3)
    assert np.allclose(raw, a / 255.0, atol=1e-6)


def test_16bit_image_is_always_over_65535(tmp_path):
    # Reliefwerk divided by 255 when no value was above 255; a dark 16-bit image stays dark.
    a = np.array([[0, 200], [255, 100]], dtype=np.uint16)
    raw = hf.load_image(save(a, tmp_path / "g16.png"))
    assert Image.open(tmp_path / "g16.png").mode == "I;16"
    assert np.allclose(raw, a / 65535.0, atol=1e-7)
    b = np.array([[0, 32768], [65535, 1000]], dtype=np.uint16)
    assert np.allclose(hf.load_image(save(b, tmp_path / "g16.tif")), b / 65535.0, atol=1e-7)


def test_colour_is_luma_and_transparent_is_low(tmp_path):
    rgba = np.zeros((1, 3, 4), dtype=np.uint8)
    rgba[0, :, :3] = 255                       # white everywhere
    rgba[0, :, 3] = [255, 0, 51]               # opaque, transparent, 20 % opaque
    raw = hf.load_image(save(rgba, tmp_path / "a.png"))
    assert np.allclose(raw[0], [1.0, 0.0, 0.2], atol=1e-6)
    rgb = np.zeros((1, 2, 3), dtype=np.uint8)
    rgb[0, 0] = [255, 0, 0]
    rgb[0, 1] = [0, 255, 0]
    raw = hf.load_image(save(rgb, tmp_path / "c.png"))
    assert np.allclose(raw[0], [76 / 255, 150 / 255], atol=1 / 255)   # ITU-R 601 luma


def test_large_image_is_scaled_to_max_side(tmp_path):
    a = np.tile(np.linspace(0, 255, 3000).astype(np.uint8), (100, 1))
    raw = hf.load_image(save(a, tmp_path / "wide.png"))
    assert raw.shape == (47, hf.MAX_SIDE)
    assert raw.min() >= 0 and raw.max() <= 1
    assert raw[0, 0] < 0.01 and raw[0, -1] > 0.99


# --------------------------------------------------------------------------
# Preparation
# --------------------------------------------------------------------------

def dot(h=4, w=6):
    a = np.zeros((h, w), dtype=np.float32)
    a[0, 0] = 1.0                              # back-left corner
    return a


@pytest.mark.parametrize("rotate, mirror, where", [
    (0, False, (0, 0)), (90, False, (0, -1)), (180, False, (-1, -1)), (270, False, (-1, 0)),
    (0, True, (0, -1)), (90, True, (0, 0)),
])
def test_rotation_is_clockwise_then_mirror(rotate, mirror, where):
    a = hf.orient(dot(), rotate, mirror)
    assert a.shape == ((4, 6) if rotate in (0, 180) else (6, 4))
    assert a[where] == 1.0 and a.sum() == 1.0


def test_row_0_is_the_back_edge_and_y_points_up():
    F = hf.prepare(dot(), 60, 40, fit="stretch", blur=0)
    assert F.shape == (4, 6) and F.cell == (12.0, 40 / 3)
    assert F.sample(0, 40) == 1.0              # back-left corner, X0 Y=H
    assert F.sample(0, 0) == 0.0 and F.sample(60, 40) == 0.0
    assert F.sample(6, 40) == pytest.approx(0.5)              # half a cell along X
    assert F.sample(0, 40 - 20 / 3) == pytest.approx(0.5)     # half a cell towards the front
    assert F.sample(-5, 50) == 1.0             # clamped to the edge


def test_cover_crops_the_centre():
    ramp = np.tile(np.arange(200, dtype=np.float32) / 199, (100, 1))   # 100 rows x 200 cols
    F = hf.prepare(ramp, 100, 100, blur=0)
    assert F.shape == (100, 100)
    assert np.allclose(F.z[0, [0, -1]], [50 / 199, 149 / 199])
    tall = np.tile(np.arange(200, dtype=np.float32)[:, None] / 199, (1, 100))  # 200 rows x 100 cols
    F = hf.prepare(tall, 100, 50, blur=0)
    assert F.shape == (50, 100)
    assert np.allclose(F.z[[0, -1], 0], [75 / 199, 124 / 199])


def test_stretch_keeps_every_pixel():
    ramp = np.tile(np.arange(200, dtype=np.float32) / 199, (100, 1))
    F = hf.prepare(ramp, 100, 100, fit="stretch", blur=0)
    assert F.shape == (100, 200) and F.cell == (100 / 199, 100 / 99)
    assert np.allclose(F.z, ramp)


def test_tonal_range_gamma_and_invert():
    ramp = np.tile(np.linspace(0, 1, 101, dtype=np.float32), (2, 1))
    F = hf.prepare(ramp, 100, 1, fit="stretch", blur=0, black=20, white=80)
    assert F.z.dtype == np.float32
    assert np.all(F.z[:, :21] == 0) and np.all(F.z[:, 80:] == 1)
    assert F.z[0, 50] == pytest.approx(0.5)
    F = hf.prepare(ramp, 100, 1, fit="stretch", blur=0, gamma=2)
    assert F.z[0, 50] == pytest.approx(0.25)
    F = hf.prepare(ramp, 100, 1, fit="stretch", blur=0, gamma=2, invert=True)
    assert F.z[0, 50] == pytest.approx(0.75) and F.z[0, 0] == 1 and F.z[0, -1] == 0


@pytest.mark.parametrize("cell", [1.0, 0.25])
def test_blur_is_in_mm(cell):
    n = int(round(100 / cell)) + 1
    x = np.arange(n) * cell
    step = np.tile((x > 50).astype(np.float32), (3, 1))
    edge = 50 + cell / 2
    F = hf.prepare(step, 100, 10, fit="stretch", blur=5)
    phi = lambda s: 0.5 * (1 + math.erf(s / math.sqrt(2)))
    for d in (-5, 0, 5, 10):
        assert F.sample(edge + d, 5) == pytest.approx(phi(d / 5), abs=0.01)


def test_blur_is_round_in_mm_when_stretched():
    a = np.zeros((51, 51), dtype=np.float32)
    a[25, 25] = 1.0
    F = hf.prepare(a, 200, 50, fit="stretch", blur=5)          # cells 4 x 1 mm
    cx, cy = F.cell
    z = F.z / F.z.sum()
    sx = math.sqrt((z.sum(0) * ((np.arange(51) - 25) * cx) ** 2).sum())
    sy = math.sqrt((z.sum(1) * ((np.arange(51) - 25) * cy) ** 2).sum())
    assert sx == pytest.approx(5, abs=0.4) and sy == pytest.approx(5, abs=0.1)


def test_no_blur_leaves_values():
    raw = np.random.default_rng(1).random((20, 30)).astype(np.float32)
    assert np.array_equal(hf.prepare(raw, 30, 20, blur=0).z, raw)


@pytest.mark.parametrize("kw", [dict(black=50, white=50), dict(black=60, white=40), dict(white=120),
                                dict(gamma=0), dict(rotate=45), dict(fit="pad"), dict(blur=-1),
                                dict(width=0)])
def test_bad_settings_are_refused(kw):
    args = dict(width=10, height=10)
    args.update(kw)
    with pytest.raises(ValueError):
        hf.prepare(dot(), **args)


def test_builtin_sample_relief():
    a = hf.sample_relief(60, 40)
    assert a.shape == (40, 60) and a.dtype == np.float32
    assert 0 <= a.min() < 0.1 and 0.9 < a.max() <= 1


# --------------------------------------------------------------------------
# STL
# --------------------------------------------------------------------------

A, TOP, BOX = 10.0, 8.0, (12.0, 16.0, -4.0, 4.0, 5.0)   # pyramid half-width, apex, box x0 x1 y0 y1 top


def scene():
    """A square pyramid (base 20 x 20 at Z0, apex Z8 over the origin) and a 4 x 8 x 5 box beside it."""
    v = np.array([[-A, -A, 0], [A, -A, 0], [A, A, 0], [-A, A, 0], [0, 0, TOP]])
    f = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4], [0, 2, 1], [0, 3, 2]]
    pyramid = trimesh.Trimesh(v, f, process=False)
    x0, x1, y0, y1, h = BOX
    box = trimesh.creation.box(extents=(x1 - x0, y1 - y0, h))
    box.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, h / 2))
    m = trimesh.util.concatenate([pyramid, box])
    assert m.is_volume
    return m


def expected(x, y):
    z = np.where((np.abs(x) <= A) & (np.abs(y) <= A), TOP * (1 - np.maximum(np.abs(x), np.abs(y)) / A), np.nan)
    x0, x1, y0, y1, h = BOX
    inbox = (x >= x0 - 1e-9) & (x <= x1 + 1e-9) & (y >= y0 - 1e-9) & (y <= y1 + 1e-9)
    return np.where(inbox, np.fmax(z, h), z)


def check_drop(d, cell):
    ny, nx = d.z.shape
    assert d.cell == cell and (d.x0, d.y1) == (-A, A)
    X = d.x0 + np.arange(nx)[None, :] * cell
    Y = d.y1 - np.arange(ny)[:, None] * cell
    want = expected(X, Y)
    assert np.array_equal(np.isnan(d.z), np.isnan(want))
    hit = ~np.isnan(want)
    assert np.abs(d.z[hit] - want[hit]).max() < 1e-6        # the plan asks for 0.05 mm


@pytest.mark.parametrize("cell", [0.5, 0.37])
def test_drop_gives_exact_heights(cell):
    d = hf.drop(scene().triangles, cell)
    assert d.z.shape == (int(math.ceil(20 / cell - 1e-9)) + 1, int(math.ceil(26 / cell - 1e-9)) + 1)
    check_drop(d, cell)
    if cell == 0.5:
        assert d.z[20, 20] == TOP                              # the apex is on this grid
    assert np.isnan(d.z[0, -1])                                # back right: beside the box


@pytest.mark.parametrize("view, turn", [
    ("y", lambda v: np.c_[v[:, 0], -v[:, 2], v[:, 1]]),      # top of the scene facing -Y (the front)
    ("x", lambda v: np.c_[v[:, 2], v[:, 0], v[:, 1]]),       # top facing +X, scene X along model Y
])
def test_views_turn_the_model_to_face_up(view, turn):
    m = scene()
    turned = trimesh.Trimesh(turn(m.vertices), m.faces, process=False)
    assert np.linalg.det(turn(np.eye(3))) == pytest.approx(1)  # a turn, not a mirror
    check_drop(hf.drop(hf.view_triangles(turned.triangles, view), 0.5), 0.5)


def test_bad_view_is_refused():
    with pytest.raises(ValueError, match="view"):
        hf.view_triangles(scene().triangles, "w")


def test_stl_file_loads_scaled_to_0_1(tmp_path):
    path = tmp_path / "scene.stl"
    scene().export(path)
    raw = hf.load(path, stl_cell=0.5)
    assert raw.dtype == np.float32 and raw.shape == (41, 53)
    d = hf.drop(scene().triangles, 0.5)
    want = np.where(np.isnan(d.z), 0.0, d.z / TOP)
    assert np.abs(raw - want).max() < 1e-6
    assert raw[20, 20] == pytest.approx(1.0) and raw[0, -1] == 0.0
    default = hf.load_stl(path)
    assert max(default.shape) == hf.STL_SIDE


def test_flat_stl_is_refused():
    tri = np.array([[[0, 0, 0], [1, 0, 0], [2, 0, 0]]], dtype=float)
    with pytest.raises(ValueError, match="no extent"):
        hf.drop(tri, 0.5)
