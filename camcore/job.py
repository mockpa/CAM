"""JSON job files: stock, tools, shapes and an ordered list of operations."""
from __future__ import annotations

import json
from pathlib import Path

from shapely.affinity import translate
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from . import ops
from .ops.common import quad_segs
from .post import job_post
from .svg import load_svg
from .tools import Tool, load_library

ENTRY_KEYS = ("entry", "ramp_angle", "helix_diameter")


def build_shape(spec: dict, base_dir: Path):
    t = spec["type"]
    if t == "rect":
        g = box(spec["x"], spec["y"], spec["x"] + spec["w"], spec["y"] + spec["h"])
    elif t == "circle":
        g = Point(spec["cx"], spec["cy"]).buffer(spec["r"], quad_segs=quad_segs(spec["r"]))
    elif t == "polygon":
        g = Polygon(spec["points"], spec.get("holes", []))
    elif t == "svg":
        g = load_svg(str(base_dir / spec["file"]), spec.get("scale", 1.0))
        if "fit_origin" in spec:
            minx, miny, *_ = g.bounds
            g = translate(g, spec["fit_origin"][0] - minx, spec["fit_origin"][1] - miny)
    elif t == "union":
        g = unary_union([build_shape(s, base_dir) for s in spec["shapes"]])
    elif t == "difference":
        g = build_shape(spec["a"], base_dir).difference(build_shape(spec["b"], base_dir))
    else:
        raise ValueError(f"unknown shape type {t}")
    return g


def run_job(job: dict, base_dir: Path = Path(".")):
    tools = {}
    if "tool_library" in job:
        tools.update(load_library(base_dir / job["tool_library"]))
    tools.update({k: Tool.from_dict({"name": k, **v}) for k, v in job.get("tools", {}).items()})
    shapes = {k: build_shape(v, base_dir) for k, v in job["shapes"].items()}
    defaults = job.get("defaults", {})
    paths = []
    for spec_own in job["operations"]:
        spec = {**defaults, **spec_own}
        kind = spec.pop("op")
        tool = tools[spec.pop("tool")]
        rpm = spec.pop("rpm", 18000)
        if kind == "drill":
            for k in ENTRY_KEYS:
                if k in defaults and k not in spec_own:
                    spec.pop(k)
            pts = spec.pop("points")
            tp = ops.drill(pts, tool, **spec)
        else:
            shape = shapes[spec.pop("shape")]
            clear = spec.pop("clear_tool", None)
            clear_args = spec.pop("clear", {})
            if kind == "vcarve" and clear:
                # Flat-bottom V-carve: an endmill clears the area deeper than
                # max_depth first, then the V-bit cuts the walls.
                if "max_depth" not in spec:
                    raise ValueError("vcarve with clear_tool needs max_depth")
                flat = ops.vcarve_flat_bottom_area(shape, tool, spec["max_depth"])
                ct = ops.pocket(flat, tools[clear], spec["max_depth"],
                                top_z=spec.get("top_z", 0.0), safe_z=spec.get("safe_z", 5.0),
                                name="vcarve-clear", **{"step_down": spec["max_depth"], **clear_args})
                ct.spindle_rpm = rpm
                paths.append(ct)
            tp = getattr(ops, kind)(shape, tool, **spec)
        tp.spindle_rpm = rpm
        paths.append(tp)
    rotary = job.get("rotary")
    if rotary:
        safe = defaults.get("safe_z", 5.0)
        paths = [ops.wrap(tp, rotary["diameter"], safe) for tp in paths]
    if job.get("tool_order") == "minimize_changes":
        paths = group_by_tool(paths)
    return paths


def group_by_tool(paths):
    """Stable regroup so each tool is loaded once, in order of first use.

    Only safe when operations with different tools don't depend on each
    other's order (e.g. a clearing pass that must precede a V-carve), so it is
    opt-in via "tool_order": "minimize_changes".
    """
    order = []
    for tp in paths:
        if tp.tool not in order:
            order.append(tp.tool)
    return [tp for t in order for tp in paths if tp.tool == t]


def load_job(path: str | Path) -> tuple[dict, Path]:
    p = Path(path)
    return json.loads(p.read_text()), p.parent


def job_to_gcode(path: str | Path) -> str:
    job, base = load_job(path)
    paths = run_job(job, base)
    return job_post(job).emit(paths)
