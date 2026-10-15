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
