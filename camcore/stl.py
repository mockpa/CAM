"""STL import for rotary work (trimesh).

Units are mm. The model frame is the machine frame at A = 0: the rotary axis
is the model's X axis (Y = Z = 0). Placing the model on the axis (centring,
rotating about X) is the caller's job; this module only loads and checks.
"""
from __future__ import annotations

import numpy as np


def load_stl(path):
    """Load a mesh as a trimesh.Trimesh. Empty or degenerate meshes are an error."""
    import trimesh

    mesh = trimesh.load(str(path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"{path}: no triangles")
    mesh.update_faces(mesh.nondegenerate_faces())
    if len(mesh.faces) == 0:
        raise ValueError(f"{path}: only degenerate triangles")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"{path}: non-finite vertex coordinates")
    return mesh


def place(mesh, rotate_x: float = 0.0, center_yz: bool = False, x_start: float | None = None,
          translate=(0.0, 0.0, 0.0)):
    """A placed copy: turned `rotate_x` degrees about X, its Y/Z bounding box centred on
    the axis if `center_yz`, its lowest X moved to `x_start`, then moved by `translate`."""
    import trimesh

    m = mesh.copy()
    if rotate_x:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(rotate_x), (1, 0, 0)))
    off = np.zeros(3)
    if center_yz:
        off[1:] = -(m.bounds[0][1:] + m.bounds[1][1:]) / 2
    if x_start is not None:
        off[0] = x_start - m.bounds[0][0]
    m.apply_translation(off + np.asarray(translate, dtype=float))
    return m


def revolve(rings, sections: int = 360):
    """Closed polygonal solid of revolution about X from rings [(x, radius), ...].

    Ring vertices sit at the machine angles 0, 360/sections, ... (cylmap.directions),
    so rays cast at those angles pass exactly through vertices.
    """
    import trimesh

    from .cylmap import directions

    if len(rings) < 2 or any(r <= 0 for _, r in rings):
        raise ValueError("need at least two rings with radius > 0")
    n, u = sections, directions(sections)
    v = np.vstack([np.array([x, 0, 0]) + r * u for x, r in rings]
                  + [[[rings[0][0], 0, 0]], [[rings[-1][0], 0, 0]]])
    c0, c1, last = len(v) - 2, len(v) - 1, (len(rings) - 1) * n
    f = []
    for k in range(len(rings) - 1):
        for j in range(n):
            a, b = k * n + j, k * n + (j + 1) % n
            f += [[a, b, b + n], [a, b + n, a + n]]
    for j in range(n):
        f += [[c0, (j + 1) % n, j], [c1, last + j, last + (j + 1) % n]]
    return trimesh.Trimesh(v, f, process=False)
