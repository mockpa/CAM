"""3-axis drop-cutter and waterline on a mesh (rust/surface_kernel).

The model frame is the machine frame (Z up, Z0 at the stock top for flat
jobs). Cutter locations are computed exactly against the triangles for flat,
ball, bull-nose and cone cutters (see rust/surface_kernel/src/lib.rs):

- drop-cutter: for each sampled (x, y) the lowest tip Z where the cutter
  touches the mesh but does not cut into it (or the floor, where it meets
  nothing above it);
- waterline: at a fixed tip Z, the closed loops of tool-centre positions
  where the cutter just touches the mesh. Inside a loop around material the
  cutter would cut into the model at that Z.

The cutter is treated as infinitely long (shank as wide as the cutter), so
material anywhere above the tip blocks it. `leave` grows the cutter by its
exact offset (a ball by `leave`, a flat end into a bull nose with corner
radius `leave`, a V-bit into a ball-tipped cone ...); the returned Z is the
real tip, so the real cutter stays `leave` off the model in every direction.
A negative `leave` shrinks it (see `cutter`).

The kernel replaced OpenCAMLib, which gave the same heights to 1e-9 mm for
flat, ball and bull-nose cutters but let cones (V-bits) sink up to 0.05 mm into
relief meshes, and took milliseconds per point on large meshes (tests compare
the two where OpenCAMLib is installed). Waterline contours are still checked
point by point with the drop-cutter (`_check_loop`).
"""
from __future__ import annotations

import math

import numpy as np

from .tools import Tool

CHECK_TOL = 0.01             # mm: at a waterline point the cutter is at most this far off touching the model,
                             # clear of it or inside it (see _check_loop)
_MESHES: dict = {}           # id(mesh) -> (mesh, kernel mesh)


def kernel():
    try:
        import camcore_surface_kernel
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError("3-axis 3D operations need camcore_surface_kernel: pip install ./rust/surface_kernel "
                           "(needs a Rust toolchain)") from e
    return camcore_surface_kernel


def kernel_mesh(mesh):
    """The mesh as the kernel's Mesh (cached per mesh object)."""
    hit = _MESHES.get(id(mesh))
    if hit is not None and hit[0] is mesh:
        return hit[1]
    km = kernel().Mesh(np.ascontiguousarray(mesh.vertices, dtype=float),
                       np.ascontiguousarray(mesh.faces, dtype=np.int64))
    if len(_MESHES) > 8:
        _MESHES.clear()
    _MESHES[id(mesh)] = (mesh, km)
    return km


def cutter(tool: Tool, leave: float = 0.0) -> tuple:
    """The kernel's cutter (cone, a, l, k) for a camcore tool grown by `leave`: a disc
    (flat, ball, bull) or a cone (V-bit, slope k = 1 / tan(half angle)) of radius a,
    grown by a ball of radius l (its exact offset). A negative `leave` gives the cutter
    shrunk by -leave (its inner offset): the same kind of cutter 2 x -leave narrower,
    its corner radius -leave smaller; `tip_shift` says how far its tip sits from the
    real one."""
    R = tool.radius
    k = 1 / math.tan(math.radians(tool.v_angle) / 2) if tool.kind == "vbit" else 0.0
    if tool.kind not in ("flat", "ball", "bull", "vbit"):
        raise ValueError(f"tool kind {tool.kind!r} not supported for 3D")
    if leave >= 0:
        if tool.kind == "flat":
            return (False, R, leave, 0.0)
        if tool.kind == "ball":
            return (False, 0.0, R + leave, 0.0)
        if tool.kind == "bull":
            return (False, R - tool.corner_radius, tool.corner_radius + leave, 0.0)
        return (True, R, leave, k)
    s = -leave
    if s >= R:
        raise ValueError(f"cannot shrink a {tool.diameter:g} mm cutter by {s:g} mm")
    if tool.kind == "flat":
        return (False, R - s, 0.0, 0.0)
    if tool.kind == "ball":
        return (False, 0.0, R - s, 0.0)
    if tool.kind == "bull":
        r = tool.corner_radius - s
        return (False, R - tool.corner_radius, r, 0.0) if r > 1e-9 else (False, R - s, 0.0, 0.0)
    return (True, R - s, 0.0, k)


def tip_shift(tool: Tool, leave: float) -> float:
    """Real tip Z minus the tip Z of `cutter(tool, leave)` at the same position: a grown
    cutter reaches `leave` lower; a shrunk one starts -leave higher (a V-bit's cone side
    moves in by -leave, so its point rises -leave / sin(half angle))."""
    if leave < 0 and tool.kind == "vbit":
        return leave / math.sin(math.radians(tool.v_angle) / 2)
    return leave


def drop_points(mesh, tool: Tool, xy: np.ndarray, floor: float, leave: float = 0.0) -> np.ndarray:
    """Tip Z for each (x, y) in an (n, 2) array (never below `floor`); `leave` as in `cutter`."""
    k = tip_shift(tool, leave)
    xy = np.ascontiguousarray(np.asarray(xy, dtype=float).reshape(-1, 2))
    z = kernel_mesh(mesh).drop(cutter(tool, leave), xy, float(floor - k))
    return np.maximum(z + k, floor)


def drop_path(mesh, tool: Tool, polyline, floor: float, sampling: float, leave: float = 0.0,
              tolerance: float | None = None, min_step: float = 0.002, max_rounds: int = 16) -> np.ndarray:
    """Drop the cutter along a 2D polyline [(x, y), ...]: (n, 3) tip positions.

    Every segment is sampled at most `sampling` apart, its end points
    included. The tip never goes below `floor`. With `tolerance`, segments are
    split where the height needed at their midpoint differs from the straight
    move by more than that, down to `min_step` long. Higher is a gouge; lower
    is tested too, because beside a wall the needed height is a step (the
    shank touches the wall) that may lie on either side of the midpoint, and
    the step must end up inside a segment no longer than min_step.
    """
    pts = np.asarray(polyline, dtype=float)
    if len(pts) < 2:
        raise ValueError("a drop path needs at least two points")
    if sampling <= 0:
        raise ValueError("sampling must be > 0")
    xy = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, math.ceil(math.dist(a, b) / sampling - 1e-9))
        xy.append(a + (b - a) * (np.arange(1, n + 1) / n)[:, None])
    xy = np.vstack(xy)
    z = drop_points(mesh, tool, xy, floor, leave)
    if tolerance is None:
        return np.column_stack([xy, z])
    check = np.ones(len(xy) - 1, dtype=bool)          # segments still to test
    for _ in range(max_rounds):
        seg = np.hypot(*(xy[1:] - xy[:-1]).T)
        idx = np.nonzero(check & (seg > min_step))[0]
        if not len(idx):
            break
        mid = (xy[idx] + xy[idx + 1]) / 2
        zm = drop_points(mesh, tool, mid, floor, leave)
        bad = np.abs(zm - (z[idx] + z[idx + 1]) / 2) > tolerance
        check[:] = False
        if not bad.any():
            break
        at = idx[bad] + 1
        xy = np.insert(xy, at, mid[bad], axis=0)
        z = np.insert(z, at, zm[bad])
        check = np.insert(check, at, False)
        new = at + np.arange(len(at))                 # positions of the inserted points
        check[new - 1] = True                         # both halves of a split segment
        check[new] = True
    return np.column_stack([xy, z])


def crop(mesh, x0: float, y0: float, x1: float, y1: float):
    """The triangles whose XY extent meets the box (whole triangles, so contours
    and drops of a cutter centred inside the box shrunk by the cutter's reach
    are the same as on the whole mesh)."""
    tri = np.asarray(mesh.triangles)
    lo, hi = tri.min(axis=1), tri.max(axis=1)
    keep = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1)
    return mesh.submesh([np.nonzero(keep)[0]], append=True, repair=False) if keep.any() else None


def waterline(mesh, tool: Tool, z: float, sampling: float, leave: float = 0.0, keep=None) -> list[np.ndarray]:
    """Closed loops [(n, 2) arrays, first point not repeated] of tool-centre
    positions at tip height z where the cutter (grown by `leave`) touches the
    mesh, from a grid `sampling` apart (each point on the contact to 1e-10 mm).
    Only loops `keep(loop)` accepts (all by default) are returned, and every
    point of those is checked (_check_loop)."""
    if sampling <= 0:
        raise ValueError("sampling must be > 0")
    raw = kernel_mesh(mesh).waterline(cutter(tool, leave), float(z - tip_shift(tool, leave)), float(sampling))
    out = sorted((_canonical(np.array(loop)) for loop in raw if len(loop) >= 3), key=lambda lp: tuple(lp[0]))
    if keep is not None:
        out = [lp for lp in out if keep(lp)]
    for lp in out:
        _check_loop(mesh, tool, lp, z, leave)
    return out


def _canonical(loop: np.ndarray) -> np.ndarray:
    """The loop started at its lowest (X, Y) point, so the same model always gives
    the same program whatever order the contour was traced in."""
    i = min(range(len(loop)), key=lambda k: (loop[k, 0], loop[k, 1]))
    return np.roll(loop, -i, axis=0)


def _check_loop(mesh, tool: Tool, loop: np.ndarray, z: float, leave: float):
    """Every waterline point must be within CHECK_TOL of where the cutter at tip
    height z touches the model: grown by CHECK_TOL it cannot get down to z there
    (the model is near), and shrunk by CHECK_TOL it can (the point is not inside
    the model). Both are tested at the point itself, so they need no direction:
    probing across the contour fails at the tip of a hairpin (a valley the cutter
    just fits into), where both sides across are material. Raises instead of
    letting a bad contour reach a toolpath."""
    floor = z - 1.0
    grown = drop_points(mesh, tool, loop, floor, leave + CHECK_TOL)
    shrunk = drop_points(mesh, tool, loop, floor, leave - CHECK_TOL)
    bad = (shrunk > z + 1e-6) | (grown < z - 1e-6)
    if bad.any():
        x, y = loop[np.argmax(bad)]
        raise RuntimeError(f"waterline at Z{z:g} has {int(bad.sum())} point(s) off the model "
                           f"contact, the first at X{x:.3f} Y{y:.3f}; refusing to use it")
