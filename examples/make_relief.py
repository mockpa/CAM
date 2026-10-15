"""Writes examples/relief.stl: a 60 x 40 mm block for 3-axis 3D work.

Its top is a relief between Z-9 and Z0: a smooth dome (raster finishing), a
steep round boss (waterline finishing) and a round dish (an inner contour).
The block stands from Z-12, so in an 18 mm board with Z0 on top the roughing
clears around it. Footprint X10..70, Y10..50.

python examples/make_relief.py
"""
from pathlib import Path

import numpy as np

X0, Y0, W, H, RES, BOTTOM = 10.0, 10.0, 60.0, 40.0, 0.5, -12.0


def height(x, y):
    u, v = x - X0, y - Y0
    z = -8.0 + 6.5 * np.exp(-(((u - 22) / 11) ** 2 + ((v - 20) / 9) ** 2))            # dome
    boss = np.hypot(u - 48, v - 28)
    z = np.maximum(z, np.where(boss <= 5.0, -1.0, -1.0 - 7.0 * np.clip((boss - 5.0) / 0.6, 0, 1)))  # steep boss
    dish = np.hypot(u - 48, v - 10)
    z = np.where(dish < 6.0, np.minimum(z, -8.0 - 1.0 * (1 - (dish / 6.0) ** 2)), z)  # dish down to Z-9
    edge = np.minimum.reduce([u, W - u, v, H - v])
    return np.where(edge < 1.0, np.minimum(z, -8.0), z)                                 # flat rim


def relief():
    import trimesh

    xs, ys = np.arange(0, W + 1e-9, RES) + X0, np.arange(0, H + 1e-9, RES) + Y0
    nx, ny = len(xs), len(ys)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    top = np.stack([X, Y, height(X, Y)], axis=-1).reshape(-1, 3)
    bot = np.stack([X, Y, np.full_like(X, BOTTOM)], axis=-1).reshape(-1, 3)
    v = np.vstack([top, bot])
    idx = np.arange(nx * ny).reshape(nx, ny)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    f = [np.stack([a, b, c], 1), np.stack([a, c, d], 1)]                         # top, facing up
    o = nx * ny
    f += [np.stack([a + o, c + o, b + o], 1), np.stack([a + o, d + o, c + o], 1)]  # bottom, facing down
    ring = list(idx[:, 0]) + list(idx[-1, 1:]) + list(idx[-2::-1, -1]) + list(idx[0, -2:0:-1])
    for p, q in zip(ring, ring[1:] + ring[:1]):                                 # walls, facing out
        f.append(np.array([[p, p + o, q], [q, p + o, q + o]]))
    mesh = trimesh.Trimesh(v, np.vstack(f), process=True)
    if not mesh.is_watertight or mesh.volume <= 0:
        raise RuntimeError("relief mesh is not a closed solid")
    return mesh


if __name__ == "__main__":
    out = Path(__file__).with_name("relief.stl")
    relief().export(out)
    print(out)
