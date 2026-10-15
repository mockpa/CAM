"""JSON job files: stock, tools, shapes and an ordered list of operations."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from shapely.affinity import translate
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from . import ops
from .ops.common import quad_segs
from .ops.rotary import index_angles, stock_reach, stock_top
from .post import job_post
from .svg import load_svg
from .tools import Tool, load_library



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
    elif t == "dxf":
        from .dxf import load_dxf
        g = load_dxf(str(base_dir / spec["file"]), spec.get("scale", 1.0), spec.get("layers"),
                     spec.get("join_tolerance", 0.01), spec.get("ignore_open", False))
    elif t == "union":
        g = unary_union([build_shape(s, base_dir) for s in spec["shapes"]])
    elif t == "difference":
        g = build_shape(spec["a"], base_dir).difference(build_shape(spec["b"], base_dir))
    else:
        raise ValueError(f"unknown shape type {t}")
    if "fit_origin" in spec:                      # move the drawing's lower-left corner here
        minx, miny, *_ = g.bounds
        g = translate(g, spec["fit_origin"][0] - minx, spec["fit_origin"][1] - miny)
    return g


def build_model(spec: dict, base_dir: Path):
    """A mesh for continuous rotary ops: {"file": "x.stl", "along": "x", "flip": false, "rotate_x": 0,
    "center_yz": false, "x_start": null, "translate": [0, 0, 0]} (stl.place)."""
    from .stl import load_stl, place
    return place(load_stl(base_dir / spec["file"]), spec.get("rotate_x", 0.0), spec.get("center_yz", False),
                 spec.get("x_start"), spec.get("translate", (0.0, 0.0, 0.0)), spec.get("along", "x"),
                 spec.get("flip", False))


def continue_a(paths, native):
    """Shift each native rotary path by whole turns of A so it starts as close as
    possible to where the previous path left A (no long unwinding moves)."""
    last = 0.0
    for tp in paths:
        a_vals = [m.a for m in tp.moves if m.a is not None]
        if not a_vals:
            continue
        if id(tp) in native:
            k = round((last - a_vals[0]) / 360.0)
            if k:
                for m in tp.moves:
                    if m.a is not None:
                        m.a += 360.0 * k
            a_vals = [m.a for m in tp.moves if m.a is not None]
        last = a_vals[-1]
    return paths


def cutting_data(library: dict, tool_name: str, material) -> dict:
    """The raw library tool's feeds and speeds for the job's material ({} without a material)."""
    if material is None:
        return {}
    data = library.get(tool_name, {}).get("cutting", {})
    if material not in data:
        raise ValueError(f"tool {tool_name} has no cutting data for material {material!r} "
                         f"(add \"cutting\": {{\"{material}\": {{...}}}} to it in the library)")
    return data[material]


def param_sources(fn, own: dict, defaults: dict, cutting: dict) -> dict:
    """Where each parameter of op function fn comes from, as {name: (value, source)}.
    source: "op" (the operation's own key), "cutting" (the tool's data for the
    material) or "defaults" (the job's); the last two only supply parameters fn
    takes (plus "rpm"). Parameters left at fn's own default are absent."""
    takes = set(inspect.signature(fn).parameters) | {"rpm"}
    out = {k: (v, src) for src, d in (("defaults", defaults), ("cutting", cutting)) for k, v in d.items()
           if k in takes}
    out.update({k: (v, "op") for k, v in own.items()})
    return out


def run_job(job: dict, base_dir: Path = Path(".")):
    """Build the toolpaths of a job, in cutting order.

    Parameters of an operation come from, highest first: the operation itself,
    the tool's "cutting" data for the job's "material", the job's "defaults".
    The last two only supply parameters the operation actually takes.
    """
    raw = {}
    if "tool_library" in job:
        raw.update(json.loads((base_dir / job["tool_library"]).read_text()))
    raw.update(job.get("tools", {}))
    tools = {}
    if "tool_library" in job:
        tools.update(load_library(base_dir / job["tool_library"]))
    tools.update({k: Tool.from_dict({"name": k, **v}) for k, v in job.get("tools", {}).items()})
    shapes = {k: build_shape(v, base_dir) for k, v in job.get("shapes", {}).items()}
    models = {k: build_model(v, base_dir) for k, v in job.get("models", {}).items()}
    native = set()                           # ids of toolpaths that already carry A (not wrapped)
    defaults = job.get("defaults", {})
    material = job.get("material")
    if "index" in job and job.get("rotary"):
        raise ValueError("a job-level \"index\" and \"rotary\" wrapping together are ambiguous: "
                         "put \"index\" on the operations that run indexed")

    def params(fn, tool_name, own):
        """Merged parameters for op function fn using tool_name; own = the op's own keys."""
        cut = cutting_data(raw, tool_name, material)
        return {k: v for k, (v, _) in param_sources(fn, own, defaults, cut).items()}

    nodes, by_id = [], {}                    # nodes: [toolpath, node id, deps], in written order
    for idx, spec_own in enumerate(job["operations"]):
        spec_own = dict(spec_own)
        kind = spec_own.pop("op")
        op_id = spec_own.pop("id", f"#{idx + 1}")
        after = spec_own.pop("after", [])
        if op_id in by_id:
            raise ValueError(f"duplicate operation id {op_id!r}")
        tool_name = spec_own.pop("tool")
        index_spec = spec_own.pop("index", None if "model" in spec_own else job.get("index"))
        face_z = spec_own.pop("face_z", None)
        if "model" in spec_own and index_spec is not None:
            raise ValueError(f"operation {op_id!r}: rotary model ops turn A themselves and cannot be indexed")
        if face_z is not None and index_spec is None:
            raise ValueError(f"operation {op_id!r}: face_z only applies to indexed operations")
        fn = getattr(ops, kind)
        spec = params(fn, tool_name, spec_own)
        tool = tools[tool_name]
        rpm = spec.pop("rpm", 18000)
        mine = []
        if kind == "drill":
            pts = spec.pop("points")
            tp = ops.drill(pts, tool, **spec)
        elif "model" in spec:
            model = models[spec.pop("model")]
            stock = job.get("stock") or {}
            if "stock_diameter" not in spec:
                if stock.get("type") != "cylinder":
                    raise ValueError(f"{kind} needs a cylinder stock or a stock_diameter")
                spec["stock_diameter"] = stock["diameter"]
            tp = fn(model, tool, **spec)
            native.add(id(tp))
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
                cargs = params(ops.pocket, clear, {"top_z": spec.get("top_z", 0.0), "safe_z": spec.get("safe_z", 5.0),
                                                   "step_down": spec["max_depth"], **clear_args})
                crpm = cargs.pop("rpm", rpm)
                ct = ops.pocket(flat, tools[clear], spec["max_depth"], name="vcarve-clear", **cargs)
                ct.spindle_rpm = crpm
                mine.append([ct, f"{op_id}:clear", set(after)])
            tp = fn(shape, tool, **spec)
        tp.spindle_rpm = rpm
        mine.append([tp, op_id, set(after) | {n[1] for n in mine}])   # the V-bit pass waits for its clearing
        if index_spec is not None:
            mine = index_nodes(mine, index_angles(index_spec), job.get("stock"), face_z,
                               job.get("index_clearance", 5.0))
            native.update(id(n[0]) for n in mine)
        else:
            for n in mine:
                n.append(None)                  # 4th entry: the A an indexed pass runs at
        by_id[op_id] = [n[1] for n in mine]
        nodes += mine
    node_ids = {n[1] for n in nodes}
    for n in nodes:                         # "after": an op id stands for all of its passes
        deps = set()
        for d in n[2]:
            if d not in by_id and d not in node_ids:
                raise ValueError(f"operation {n[1]!r} is after unknown id {d!r}")
            deps |= set(by_id.get(d, [d]))
        n[2] = deps
    paths = order_paths(nodes, job.get("tool_order") == "minimize_changes")
    rotary = job.get("rotary")
    if rotary:
        safe = defaults.get("safe_z", 5.0)
        wrapped = [tp if id(tp) in native else ops.wrap(tp, rotary["diameter"], safe) for tp in paths]
        paths = wrapped
    return continue_a(paths, native)


def index_nodes(passes, angles, stock, face_z, clearance):
    """Indexed 3+1: each pass of an op once per A angle, angle by angle. Within one
    angle the passes keep their dependencies (a V-bit pass waits for the clearing
    of the same face). Nodes gain a 4th entry, the A they run at."""
    if not stock or stock.get("type") not in ("cylinder", "bar"):
        raise ValueError("indexed operations need a cylinder or bar stock (Y0 and Z0 on the rotary axis)")
    if stock["type"] == "bar" and not all(lo < 0 < hi for lo, hi in (stock["y"], stock["z"])):
        raise ValueError("bar stock must enclose the rotary axis (its y and z ranges must contain 0)")
    if clearance <= 0:
        raise ValueError("index_clearance must be > 0")
    local = {n[1] for n in passes}
    out = []
    for a in angles:
        top = stock_top(stock, a) if face_z is None else face_z
        tag = lambda nid: f"{nid}@A{a:g}"
        for tp, nid, deps in passes:
            highest = max((m.z for m in tp.moves if m.z is not None), default=0.0) + top
            clear = max(stock_reach(stock) + clearance, highest)
            out.append([ops.index(tp, a, top, clear), tag(nid),
                        {tag(d) if d in local else d for d in deps}, a])
    return out


def order_paths(nodes, minimize_changes: bool):
    """Cutting order: as written, except that a pass waits for everything it is
    "after". With minimize_changes, stay on the current tool while any pass
    for it is ready, and then on the current index angle (greedy; never breaks
    a dependency)."""
    remaining, done, out, current, current_a = list(nodes), set(), [], None, None
    while remaining:
        ready = [n for n in remaining if n[2] <= done]
        if not ready:
            raise ValueError("operations depend on each other in a cycle: "
                             + ", ".join(sorted(n[1] for n in remaining)))
        pool = ready
        if minimize_changes:
            pool = [n for n in ready if n[0].tool == current] or ready
            pool = [n for n in pool if current_a is not None and len(n) > 3 and n[3] == current_a] or pool
        pick = pool[0]
        remaining.remove(pick)
        done.add(pick[1])
        out.append(pick[0])
        current, current_a = pick[0].tool, (pick[3] if len(pick) > 3 else None)
    return out


def load_job(path: str | Path) -> tuple[dict, Path]:
    p = Path(path)
    return json.loads(p.read_text()), p.parent


def job_to_gcode(path: str | Path) -> str:
    job, base = load_job(path)
    paths = run_job(job, base)
    return job_post(job).emit(paths)
