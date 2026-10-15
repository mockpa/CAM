"""Cutting tool definitions. All dimensions in mm."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    diameter: float
    kind: str = "flat"          # flat | ball | vbit
    v_angle: float = 90.0       # included angle in degrees, vbit only
    flute_length: float = 20.0
    number: int | None = None   # tool table number on the controller

    @property
    def radius(self) -> float:
        return self.diameter / 2.0

    def depth_for_radius(self, r: float) -> float:
        """Depth at which a V-bit cuts a groove of half-width r."""
        if self.kind != "vbit":
            raise ValueError("depth_for_radius only applies to V-bits")
        return r / math.tan(math.radians(self.v_angle) / 2.0)

    def radius_at_depth(self, depth: float) -> float:
        """Cutting radius of the tool profile at a given depth below the tip."""
        if self.kind == "vbit":
            return min(self.radius, depth * math.tan(math.radians(self.v_angle) / 2.0))
        if self.kind == "ball":
            d = min(depth, self.radius)
            return math.sqrt(max(0.0, self.radius**2 - (self.radius - d) ** 2))
        return self.radius

    @staticmethod
    def from_dict(d: dict) -> "Tool":
        return Tool(**d)


def load_library(path) -> dict[str, Tool]:
    """Tool library JSON: {"name": {"number": 3, "diameter": 6, "kind": "flat", ...}}.

    Numbers must be unique: they are the controller's tool table / ATC pocket ids.
    """
    import json
    from pathlib import Path
    data = json.loads(Path(path).read_text())
    lib = {k: Tool.from_dict({"name": k, **v}) for k, v in data.items()}
    nums = [t.number for t in lib.values() if t.number is not None]
    if len(nums) != len(set(nums)):
        raise ValueError("duplicate tool numbers in library")
    return lib
