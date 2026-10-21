"""3-axis drop-cutter and waterline on a mesh, through OpenCAMLib.

The model frame is the machine frame (Z up, Z0 at the stock top for flat
jobs). OpenCAMLib computes cutter locations exactly against the triangles
for flat, ball, bull-nose and cone cutters:

- drop-cutter: for each sampled (x, y) the lowest tip Z where the cutter
  touches the mesh but does not cut into it (or the floor, where it meets
  nothing above it);
- waterline: at a fixed tip Z, the closed loops of tool-centre positions
  where the cutter just touches the mesh. Inside a loop around material the
  cutter would cut into the model at that Z.

The cutter is treated as infinitely long (shank as wide as the cutter), so
material anywhere above the tip blocks it. `leave` grows the cutter by
OpenCAMLib's exact offset (a ball by `leave`, a flat end into a bull nose
with corner radius `leave`, ...); the returned Z is the real tip, so the
real cutter stays `leave` off the model in every direction.

Vertical triangles: OpenCAMLib decides whether a contact lies on a facet
with a barycentric test in the XY plane, which divides by the facet's XY
area: for a vertical facet that is zero (plus rounding noise), so contacts
far off the facet pass and waterline contours grow spikes many mm long.
Such facets are left out of what OpenCAMLib sees, which is exact for closed
meshes: every cutter widens (or stays as wide) going up, so if it touches a
vertical facet inside, it also touches the point straight above on the
facet's upper boundary, an edge that a non-vertical triangle shares and
OpenCAMLib tests correctly. The mesh is also moved next to the origin
first, which keeps OpenCAMLib's arithmetic well conditioned. Waterline
contours are then checked point by point with the drop-cutter (`_check_loop`).

OpenCAMLib is only on conda-forge for current Pythons (see environment.yml),
so it is imported when first needed.
"""
from __future__ import annotations

import contextlib
import math
import os
import tempfile
from dataclasses import dataclass

import numpy as np

from .tools import Tool

LENGTH = 1000.0              # mm: cutter length for OpenCAMLib, longer than any model
MIN_XY_AREA = 1e-8           # mm^2: triangles with less XY area are left out (see the module docstring)
CHECK_TOL = 0.01             # mm: at a waterline point the cutter is at most this far off touching the model,
                             # clear of it or inside it (see _check_loop)
_SURF: dict = {}             # id(mesh) -> (mesh, Surf)


def ocl():
    try:
        import opencamlib
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError("3-axis 3D operations need OpenCAMLib: install the conda environment "
                           "(conda env create -p .conda -f environment.yml)") from e
    return opencamlib


@contextlib.contextmanager
def _quiet():
    """Swallow OpenCAMLib's C++ diagnostics on stdout (debug prints, destructor
    messages)."""
    try:
        fd = os.dup(1)
    except OSError:          # no real stdout (embedded): nothing to redirect
        yield
        return
    with tempfile.TemporaryFile() as sink:
        os.dup2(sink.fileno(), 1)
        try:
            yield
        finally:
            _flush_c_stdout()        # C++ output still in the stdio buffer goes to the sink too
            os.dup2(fd, 1)
            os.close(fd)


def _flush_c_stdout():
    try:
        import ctypes
        ctypes.CDLL(None).fflush(None)
    except (OSError, AttributeError):   # pragma: no cover - no C library to flush
        pass


@dataclass
class Surf:
    stl: object              # OpenCAMLib STLSurf, in coordinates shifted by -shift
    shift: np.ndarray        # (3,) machine = OpenCAMLib + shift
    dropped: int             # vertical triangles left out


def stl_surf(mesh) -> Surf:
    """The mesh as an OpenCAMLib STLSurf near the origin, without vertical
    triangles (cached per mesh object)."""
    hit = _SURF.get(id(mesh))
    if hit is not None and hit[0] is mesh:
        return hit[1]
    o = ocl()
    tri = np.asarray(mesh.triangles, dtype=float)
    shift = np.round((tri.reshape(-1, 3).min(axis=0) + tri.reshape(-1, 3).max(axis=0)) / 2)
    tri = tri - shift
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    keep = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2 >= MIN_XY_AREA
    s = o.STLSurf()
    P = o.Point
    for a, b, c in tri[keep]:
        s.addTriangle(o.Triangle(P(*a), P(*b), P(*c)))
    surf = Surf(s, shift, int((~keep).sum()))
    if len(_SURF) > 8:
        _SURF.clear()
    _SURF[id(mesh)] = (mesh, surf)
    return surf


def cutter(tool: Tool, leave: float = 0.0):
    """OpenCAMLib cutter for a camcore tool, grown by `leave` (exact offset). A negative
    `leave` gives the cutter shrunk by -leave (its inner offset): the same kind of cutter
    2 x -leave narrower, its corner radius -leave smaller; `tip_shift` says how far its
    tip sits from the real one."""
    o = ocl()
    d = tool.diameter
    if leave < 0:
        s = -leave
        if s >= tool.radius:
            raise ValueError(f"cannot shrink a {d:g} mm cutter by {s:g} mm")
        d -= 2 * s
    if tool.kind == "flat":
        c = o.CylCutter(d, LENGTH)
    elif tool.kind == "ball":
        c = o.BallCutter(d, LENGTH)
    elif tool.kind == "bull":
        r = tool.corner_radius + min(leave, 0.0)
        c = o.BullCutter(d, r, LENGTH) if r > 1e-9 else o.CylCutter(d, LENGTH)
    elif tool.kind == "vbit":
        c = o.ConeCutter(d, math.radians(tool.v_angle) / 2, LENGTH)
    else:
        raise ValueError(f"tool kind {tool.kind!r} not supported for 3D")
    return c.offsetCutter(leave) if leave > 0 else c


def tip_shift(tool: Tool, leave: float) -> float:
    """Real tip Z minus the tip Z of `cutter(tool, leave)` at the same position: a grown
    cutter reaches `leave` lower; a shrunk one starts -leave higher (a V-bit's cone side
    moves in by -leave, so its point rises -leave / sin(half angle))."""
    if leave < 0 and tool.kind == "vbit":
        return leave / math.sin(math.radians(tool.v_angle) / 2)
    return leave


def drop_points(mesh, tool: Tool, xy: np.ndarray, floor: float, leave: float = 0.0) -> np.ndarray:
    """Tip Z for each (x, y) in an (n, 2) array (never below `floor`); `leave` as in `cutter`."""
    o = ocl()
    # OpenCAMLib keeps raw pointers to the surface and cutter: hold them for the run
    surf, cut = stl_surf(mesh), cutter(tool, leave)
    sx, sy, sz = surf.shift
    k = tip_shift(tool, leave)
    bdc = o.BatchDropCutter()
    bdc.setSTL(surf.stl)
    bdc.setCutter(cut)
    for x, y in np.asarray(xy, dtype=float):
        bdc.appendPoint(o.CLPoint(float(x - sx), float(y - sy), float(floor - k - sz)))
    with _quiet():
        bdc.run()
    cl = bdc.getCLPoints()
    if len(cl) != len(xy):
        raise RuntimeError("OpenCAMLib returned a different number of points than it was given")
    return np.maximum(np.array([p.z for p in cl]) + sz + k, floor)


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
    mesh. Fibers are `sampling` apart. Only loops `keep(loop)` accepts (all by
    default) are returned, and every point of those is checked (_check_loop)."""
    o = ocl()
    if sampling <= 0:
        raise ValueError("sampling must be > 0")
    surf, cut = stl_surf(mesh), cutter(tool, leave)   # held for the run (raw pointers inside)
    sx, sy, sz = surf.shift
    w = o.Waterline()
    w.setSTL(surf.stl)
    w.setCutter(cut)
    w.setZ(float(z - leave - sz))
    w.setSampling(sampling)
    with _quiet():
        w.run()
        loops = w.getLoops()
        del w                 # its destructor prints too
    out = sorted((_canonical(np.array([(p.x + sx, p.y + sy) for p in loop])) for loop in loops if len(loop) >= 3),
                 key=lambda lp: tuple(lp[0]))
    if keep is not None:
        out = [lp for lp in out if keep(lp)]
    for lp in out:
        _check_loop(mesh, tool, lp, z, leave)
    return out


def _canonical(loop: np.ndarray) -> np.ndarray:
    """The loop started at its lowest (X, Y) point: OpenCAMLib's threads start
    loops anywhere, and the same model must always give the same program."""
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
        raise RuntimeError(f"OpenCAMLib waterline at Z{z:g} has {int(bad.sum())} point(s) off the model "
                           f"contact, the first at X{x:.3f} Y{y:.3f}; refusing to use it")
