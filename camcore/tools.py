"""Cutting tool definitions. All dimensions in mm."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    diameter: float
    kind: str = "flat"          # flat | ball | bull | vbit
    v_angle: float = 90.0       # included angle in degrees, vbit only
    flute_length: float = 20.0
    number: int | None = None   # tool table number on the controller
    center_cutting: bool = False  # may plunge straight down (only where an op allows it)
    corner_radius: float = 0.0  # bull nose only: radius of the rounded corner, 0 < corner_radius <= radius

    def __post_init__(self):
        if self.kind == "bull" and not 0 < self.corner_radius <= self.radius:
            raise ValueError(f"tool {self.name}: a bull nose needs 0 < corner_radius <= {self.radius}")

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
        if self.kind == "bull":
            rc = self.corner_radius
            d = min(depth, rc)
            return self.radius - rc + math.sqrt(max(0.0, rc**2 - (rc - d) ** 2))
        return self.radius

    def profile(self, d: float) -> float:
        """Height of the cutting surface above the tip at distance d (<= radius) from the axis."""
        r = self.radius
        if self.kind == "vbit":
            return d / math.tan(math.radians(self.v_angle) / 2.0)
        if self.kind == "ball":
            return r - math.sqrt(max(0.0, r * r - d * d))
        if self.kind == "bull":
            rc, flat = self.corner_radius, r - self.corner_radius
            return 0.0 if d <= flat else rc - math.sqrt(max(0.0, rc * rc - (d - flat) ** 2))
        return 0.0

    @staticmethod
    def from_dict(d: dict) -> "Tool":
        """From a library entry; its "cutting" data (feeds and speeds per material) is read by the job."""
        return Tool(**{k: v for k, v in d.items() if k != "cutting"})


def load_library(path) -> dict[str, Tool]:
    """Tool library JSON: {"name": {"number": 3, "diameter": 6, "kind": "flat", ...}}.

    Numbers must be unique: they are the controller's tool table / ATC pocket ids.
    """
    import json
    from pathlib import Path
    return library_from_dict(json.loads(Path(path).read_text()))


def library_from_dict(data: dict) -> dict[str, Tool]:
    """Tools from a parsed library file, with the same checks as load_library."""
    lib = {k: Tool.from_dict({"name": k, **v}) for k, v in data.items()}
    nums = [t.number for t in lib.values() if t.number is not None]
    if len(nums) != len(set(nums)):
        raise ValueError("duplicate tool numbers in library")
    return lib
