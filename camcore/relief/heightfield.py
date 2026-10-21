"""Height fields for relief designs: an image or STL in, a `HeightField` out.

Ported from Reliefwerk's `rw_core` (load_image, load_stl, prepare, sample). A raw
field is a 2D float array with values 0..1 (1 = high), row 0 at the back edge and
column 0 at the left. `prepare` fits it to the artwork size W x H mm (crop or
stretch), turns, mirrors, blurs in mm and sets the tonal range; the result is a
`HeightField` whose grid spans exactly 0..W and 0..H (corner samples, so cell
(0, 0) is at the back-left corner X0 Y=H).

Changes from Reliefwerk:
- STL input is a drop onto a grid: each grid point gets the exact height of the
  highest triangle above it (`drop`), instead of splatting sample points.
- Blur is in mm along each axis, so a stretched image blurs the same in X and Y.
- 16-bit images are always scaled by 65535 (Reliefwerk divided by 255 when no
  value was above 255).
- A white point at or below the black point is an error instead of being nudged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAX_SIDE = 1400        # longer images are scaled down (finer than any milling raster)
STL_SIDE = 900         # default STL drop: this many grid points along the longer side
FITS = ("cover", "stretch")
VIEWS = ("z", "y", "x")


@dataclass
class HeightField:
    """Relief heights 0..1 (1 = high) over the artwork W x H mm.

    `z[0]` is the back edge (Y = height), `z[-1]` the front edge (Y = 0); column 0 is X = 0
    and the last column X = width. Grid points sit on the corners, so the cell size is
    width / (nx - 1) by height / (ny - 1).
    """
    z: np.ndarray
    width: float
    height: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape

    @property
    def cell(self) -> tuple[float, float]:
        """(mm per cell along X, mm per cell along Y)."""
        ny, nx = self.z.shape
        return self.width / max(nx - 1, 1), self.height / max(ny - 1, 1)

    def sample(self, x, y) -> np.ndarray:
        """Bilinear height at artwork coordinates in mm (X right, Y up), clamped to the edges."""
        return sample_uv(self.z, np.asarray(x, dtype=float) / self.width,
                         1.0 - np.asarray(y, dtype=float) / self.height)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load(path, stl_view: str = "z", stl_cell: float | None = None) -> np.ndarray:
    """Raw field from an image (bright = high) or an STL (by extension)."""
    if Path(path).suffix.lower() == ".stl":
        return load_stl(path, view=stl_view, cell=stl_cell)
    return load_image(path)


def load_image(path) -> np.ndarray:
    """Grey values 0..1 from an 8- or 16-bit image. Transparent pixels count as black (low);
    images longer than MAX_SIDE are scaled down bilinearly."""
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        if im.mode.startswith("I;16"):
            a = np.asarray(im, dtype=np.float64) / 65535.0
        elif im.mode == "I":       # 32-bit int: 16-bit data unless nothing is above 255
            a = np.asarray(im, dtype=np.float64)
            a = a / (65535.0 if a.max() > 255 else 255.0)
        elif im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = np.asarray(rgba, dtype=np.float64)[..., 3] / 255.0
            a = np.asarray(rgba.convert("L"), dtype=np.float64) / 255.0 * alpha
        else:
            a = np.asarray(im.convert("L"), dtype=np.float64) / 255.0
    a = np.clip(a, 0.0, 1.0).astype(np.float32)
    h, w = a.shape
    if max(h, w) > MAX_SIDE:
        f = MAX_SIDE / max(h, w)
        a = np.asarray(Image.fromarray(a).resize(           # float32 -> mode "F"
            (max(2, round(w * f)), max(2, round(h * f))), Image.BILINEAR), dtype=np.float32)
        a = np.clip(a, 0.0, 1.0)
    return a


def view_triangles(tri: np.ndarray, view: str) -> np.ndarray:
    """Triangles (n, 3, 3) turned so that the viewer looks down -Z: "z" from above,
    "y" from the front (height = -Y), "x" from the +X side (height = X, Y to the right)."""
    tri = np.asarray(tri, dtype=float)
    if view == "z":
        return tri.copy()
    if view == "y":
        return tri[:, :, [0, 2, 1]] * np.array([1.0, 1.0, -1.0])
    if view == "x":
        return tri[:, :, [1, 2, 0]]
    raise ValueError(f"STL view must be one of {VIEWS}, not {view!r}")


@dataclass
class Drop:
    """Heights in mm of a mesh dropped onto a grid. `z[j, i]` is at X = x0 + i * cell,
    Y = y1 - j * cell (row 0 at the back); NaN where no triangle is above the point."""
    z: np.ndarray
    x0: float
    y1: float
    cell: float


def drop(tri: np.ndarray, cell: float, chunk: int = 2_000_000) -> Drop:
    """Exact top surface of triangles (n, 3, 3) on a grid over their XY bounding box.

    Each grid point gets the highest triangle height straight above it. Triangles with no
    XY area (vertical walls) are left out: a vertical ray meets them only along an edge,
    which the neighbouring facets cover.
    """
    if cell <= 0:
        raise ValueError("cell must be > 0")
    tri = np.asarray(tri, dtype=float)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    if hi[0] - lo[0] <= 0 or hi[1] - lo[1] <= 0:
        raise ValueError("the model has no extent in the view plane")
    nx = int(np.ceil((hi[0] - lo[0]) / cell - 1e-9)) + 1
    ny = int(np.ceil((hi[1] - lo[1]) / cell - 1e-9)) + 1
    x0, y1 = float(lo[0]), float(hi[1])
    px = (tri[:, :, 0] - x0) / cell                 # grid units, column index
    py = (y1 - tri[:, :, 1]) / cell                 # grid units, row index
    pz = tri[:, :, 2]
    d = (py[:, 1] - py[:, 2]) * (px[:, 0] - px[:, 2]) + (px[:, 2] - px[:, 1]) * (py[:, 0] - py[:, 2])
    keep = np.abs(d) > 1e-12
    px, py, pz, d = px[keep], py[keep], pz[keep], d[keep]
    eps = 1e-9
    i0 = np.clip(np.ceil(px.min(1) - eps), 0, nx - 1).astype(np.int64)
    i1 = np.clip(np.floor(px.max(1) + eps), 0, nx - 1).astype(np.int64)
    j0 = np.clip(np.ceil(py.min(1) - eps), 0, ny - 1).astype(np.int64)
    j1 = np.clip(np.floor(py.max(1) + eps), 0, ny - 1).astype(np.int64)
    bw = np.maximum(i1 - i0 + 1, 0)
    bh = np.maximum(j1 - j0 + 1, 0)
    count = bw * bh
    zbuf = np.full(ny * nx, -np.inf)
    ends = np.cumsum(count)
    start = 0
    while start < len(count):
        base = ends[start - 1] if start else 0
        stop = int(np.searchsorted(ends, base + chunk, side="right"))
        stop = max(stop, start + 1)
        t = np.arange(start, stop)
        c = count[t]
        tt = np.repeat(t, c)
        k = np.arange(int(c.sum())) - np.repeat(np.cumsum(c) - c, c)
        gi = i0[tt] + k % bw[tt]
        gj = j0[tt] + k // bw[tt]
        X, Y = gi - px[tt, 2], gj - py[tt, 2]
        l0 = ((py[tt, 1] - py[tt, 2]) * X + (px[tt, 2] - px[tt, 1]) * Y) / d[tt]
        l1 = ((py[tt, 2] - py[tt, 0]) * X + (px[tt, 0] - px[tt, 2]) * Y) / d[tt]
        l2 = 1.0 - l0 - l1
        ok = (l0 >= -eps) & (l1 >= -eps) & (l2 >= -eps)
        z = l0 * pz[tt, 0] + l1 * pz[tt, 1] + l2 * pz[tt, 2]
        np.maximum.at(zbuf, (gj * nx + gi)[ok], z[ok])
        start = stop
    z = zbuf.reshape(ny, nx)
    z[~np.isfinite(z)] = np.nan
    return Drop(z, x0, y1, float(cell))


def load_stl(path, view: str = "z", cell: float | None = None) -> np.ndarray:
    """Raw field of an STL seen from `view` (see `view_triangles`): the drop heights scaled
    to 0..1 between the lowest and highest point hit; grid points off the model are 0.
    `cell` defaults to the longer side over STL_SIDE - 1."""
    from ..stl import load_stl as load_mesh

    tri = view_triangles(load_mesh(path).triangles, view)
    if cell is None:
        ext = tri.reshape(-1, 3).max(0) - tri.reshape(-1, 3).min(0)
        cell = max(ext[0], ext[1]) / (STL_SIDE - 1)
    z = drop(tri, cell).z
    hit = np.isfinite(z)
    zmin, zmax = z[hit].min(), z[hit].max()
    return np.where(hit, (z - zmin) / max(zmax - zmin, 1e-9), 0.0).astype(np.float32)


def sample_relief(w: int = 600, h: int = 400) -> np.ndarray:
    """Reliefwerk's built-in example: flowing waves with one bump."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = x / (w - 1), y / (h - 1)
    bump = np.exp(-((u - 0.64) ** 2 + (v - 0.46) ** 2) / 0.035)
    z = 0.5 + 0.5 * np.sin(9.0 * u + 2.6 * np.sin(4.2 * v + 1.0) + 3.2 * bump)
    return np.clip(0.75 * z + 0.25 * bump, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Preparation
# --------------------------------------------------------------------------

def orient(raw: np.ndarray, rotate: int = 0, mirror: bool = False) -> np.ndarray:
    """Turned `rotate` degrees clockwise (0, 90, 180, 270), then mirrored left to right."""
    if rotate not in (0, 90, 180, 270):
        raise ValueError(f"rotate must be 0, 90, 180 or 270, not {rotate!r}")
    a = np.rot90(raw, -(rotate // 90)) if rotate else raw
    if mirror:
        a = a[:, ::-1]
    return np.ascontiguousarray(a)


def crop_to(a: np.ndarray, aspect: float) -> np.ndarray:
    """Centre crop to width / height = aspect (at least 2 pixels each way)."""
    h, w = a.shape
    if w / h > aspect:                     # too wide: cut left and right
        cw = max(2, int(round(h * aspect)))
        x0 = (w - cw) // 2
        return a[:, x0:x0 + cw]
    ch = max(2, int(round(w / aspect)))    # too tall: cut top and bottom
    y0 = (h - ch) // 2
    return a[y0:y0 + ch, :]


def blur_mm(a: np.ndarray, blur: float, cell: tuple[float, float]) -> np.ndarray:
    """Gaussian blur with sigma `blur` mm along both axes (edges repeated)."""
    from scipy.ndimage import gaussian_filter1d

    out = a.astype(np.float32)
    for axis, step in ((1, cell[0]), (0, cell[1])):
        sigma = blur / step
        if sigma >= 0.3 and out.shape[axis] > 1:
            out = gaussian_filter1d(out, sigma, axis=axis, mode="nearest", truncate=3.0)
    return out


def prepare(raw: np.ndarray, width: float, height: float, fit: str = "cover",
            rotate: int = 0, mirror: bool = False, blur: float = 2.0,
            black: float = 0.0, white: float = 100.0, gamma: float = 1.0,
            invert: bool = False) -> HeightField:
    """Fit a raw field to the artwork `width` x `height` mm.

    `fit` "cover" crops the centre to the artwork's aspect, "stretch" scales it. `blur`
    is the Gaussian sigma in mm. `black` / `white` are percent of the grey range that
    become 0 and 1, then `gamma` (> 1 darkens the mid tones) and `invert`.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be > 0")
    if fit not in FITS:
        raise ValueError(f"fit must be one of {FITS}, not {fit!r}")
    if blur < 0:
        raise ValueError("blur must be >= 0")
    if not 0 <= black < white <= 100:
        raise ValueError(f"need 0 <= black < white <= 100 (percent), got {black} and {white}")
    if gamma <= 0:
        raise ValueError("gamma must be > 0")
    a = np.asarray(raw, dtype=np.float32)
    if a.ndim != 2 or min(a.shape) < 2:
        raise ValueError("the raw field must be a 2D array of at least 2 x 2")
    a = orient(a, rotate, mirror)
    if fit == "cover":
        a = crop_to(a, width / height)
    ny, nx = a.shape
    a = blur_mm(a, blur, (width / (nx - 1), height / (ny - 1)))
    lo, hi = black / 100.0, white / 100.0
    a = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    if abs(gamma - 1.0) > 1e-9:
        a = a ** gamma
    if invert:
        a = 1.0 - a
    return HeightField(a.astype(np.float32), float(width), float(height))


def sample_uv(F: np.ndarray, u, v) -> np.ndarray:
    """Bilinear sample of F at u (0 = left .. 1 = right), v (0 = top .. 1 = bottom)."""
    h, w = F.shape
    x = np.clip(np.asarray(u, dtype=np.float64), 0, 1) * (w - 1)
    y = np.clip(np.asarray(v, dtype=np.float64), 0, 1) * (h - 1)
    x0 = np.minimum(np.floor(x).astype(np.int64), max(w - 2, 0))
    y0 = np.minimum(np.floor(y).astype(np.int64), max(h - 2, 0))
    fx, fy = x - x0, y - y0
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    top = F[y0, x0] * (1 - fx) + F[y0, x1] * fx
    bot = F[y1, x0] * (1 - fx) + F[y1, x1] * fx
    return top * (1 - fy) + bot * fy
