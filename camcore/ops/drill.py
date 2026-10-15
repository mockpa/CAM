"""Drilling (peck) cycles emitted as plain moves so every post can handle them."""
from __future__ import annotations

from ..tools import Tool
from ..toolpath import Toolpath


def drill(points, tool: Tool, depth: float, peck: float | None = None, plunge: float = 200,
          safe_z: float = 5.0, retract_z: float = 1.0, top_z: float = 0.0,
          name: str = "drill") -> Toolpath:
    if not tool.center_cutting:
        raise ValueError(f"drilling plunges, but tool {tool.name} is not marked center_cutting")
    tp = Toolpath(name, tool)
    tp.rapid(z=safe_z)
    for x, y in points:
        tp.rapid(x=x, y=y)
        tp.rapid(z=top_z + retract_z)
        z = top_z
        bottom = top_z - depth
        while z > bottom + 1e-9:
            z = max(bottom, z - (peck or depth))
            tp.feed(z=z, f=plunge)
            tp.rapid(z=top_z + retract_z)
        tp.rapid(z=safe_z)
    return tp
