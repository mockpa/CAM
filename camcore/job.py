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
from .shapes import mirror
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
    elif t == "mirror":
        g = mirror(build_shape(spec["shape"], base_dir), spec.get("axis", "y"))
    else:
        raise ValueError(f"unknown shape type {t}")
    if "fit_origin" in spec:                      # move the drawing's lower-left corner here
        minx, miny, *_ = g.bounds
        g = translate(g, spec["fit_origin"][0] - minx, spec["fit_origin"][1] - miny)
    return g


def _mm(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def shape_on_stock(name: str, shape, stock: dict | None) -> str | None:
    """Where a flat job's shape lies against its box stock (in X Y). Entirely off
    the stock raises ValueError; partly off returns a note; on it returns None.
    Drawings keep their own coordinates (SVG Y runs downwards, so an SVG lands
    below Y0), so both messages say how fit_origin moves it."""
    if not stock or stock.get("type") != "box" or shape.is_empty:
        return None
    sx, sy = stock["x"], stock["y"]
    area = box(sx[0], sy[0], sx[1], sy[1])
    if shape.within(area) or shape.covers(area):     # covering the whole stock: facing, on purpose
        return None
    x0, y0, x1, y1 = shape.bounds
    w, h = x1 - x0, y1 - y0
    where = (f"shape {name!r} lies at X {_mm(x0)}..{_mm(x1)} Y {_mm(y0)}..{_mm(y1)}, "
             f"the stock at X {_mm(sx[0])}..{_mm(sx[1])} Y {_mm(sy[0])}..{_mm(sy[1])}")
    if w <= sx[1] - sx[0] and h <= sy[1] - sy[0]:
        cx, cy = (sx[0] + sx[1] - w) / 2, (sy[0] + sy[1] - h) / 2
        how = (f'add "fit_origin": [x, y] to the shape to put its lower-left corner there, '
               f'e.g. [{_mm(cx)}, {_mm(cy)}] to centre it on the stock')
    else:
        how = (f"it is {_mm(w)} x {_mm(h)} mm, bigger than the stock: scale the drawing "
               f'("scale") or use bigger stock, then place it with "fit_origin": [x, y]')
    if not shape.intersects(area):
        raise ValueError(f"{where}: it is completely off the stock. {how[0].upper()}{how[1:]}")
    return f"{where}: part of it is off the stock. If that is not meant, {how}"


SURFACE_OPS = ("surface_rough", "surface_finish")     # 3-axis model ops (box stock, no A)
MODEL_OPS = SURFACE_OPS + ("rotary_rough", "rotary_finish")


def _names(d) -> str:
    return ", ".join(sorted(d)) or "none"


def check_refs(op_id, kind, spec: dict, tools: dict, shapes: dict, models: dict):
    """What an operation names must exist: its tool, clear tool, and the shape,
    model or points it cuts. Says which, instead of a bare KeyError."""
    where = f"operation {op_id!r} ({kind})"
    if not kind or not hasattr(ops, kind):
        raise ValueError(f"operation {op_id!r}: unknown op {kind!r}")
    for key in ("tool", "clear_tool"):
        name = spec.get(key)
        if key == "tool" and not name:
            raise ValueError(f"{where} has no tool: pick one (tools: {_names(tools)})")
        if name and name not in tools:
            raise ValueError(f"{where}: {key} {name!r} is not in the tool library (tools: {_names(tools)})")
    if kind == "drill":
        if not spec.get("points"):
            raise ValueError(f"{where} has no points: give the hole centres, [[x, y], ...]")
        return
    key, have = ("model", models) if kind in MODEL_OPS else ("shape", shapes)
    name = spec.get(key)
    if not name:
        raise ValueError(f"{where} has no {key}: pick one ({key}s: {_names(have)})")
    if name not in have:
        raise ValueError(f"{where}: {key} {name!r} is not in the job's {key}s ({key}s: {_names(have)})")


def build_model(spec: dict, base_dir: Path):
    """A mesh in machine coordinates: {"file": "x.stl", "along": "x", "flip": false, "rotate_x": 0,
    "center_yz": false, "x_start": null, "translate": [0, 0, 0]} (stl.place), then for flat
    jobs optionally "xy_start": [x, y] (lowest X and Y moved there) and "z_top": z (highest
    point moved there, e.g. 0 for the stock top)."""
    from .stl import load_stl, place
    m = place(load_stl(base_dir / spec["file"]), spec.get("rotate_x", 0.0), spec.get("center_yz", False),
              spec.get("x_start"), spec.get("translate", (0.0, 0.0, 0.0)), spec.get("along", "x"),
              spec.get("flip", False))
    if "xy_start" in spec or "z_top" in spec:
        off = [0.0, 0.0, 0.0]
        if "xy_start" in spec:
            off[0] = spec["xy_start"][0] - m.bounds[0][0]
            off[1] = spec["xy_start"][1] - m.bounds[0][1]
        if "z_top" in spec:
            off[2] = spec["z_top"] - m.bounds[1][2]
        m.apply_translation(off)
    return m


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
        check_refs(op_id, kind, {"tool": spec_own.get("tool"), **spec_own}, tools, shapes, models)
        tool_name = spec_own.pop("tool")
        index_spec = spec_own.pop("index", None if "model" in spec_own else job.get("index"))
        face_z = spec_own.pop("face_z", None)
        if "model" in spec_own and index_spec is not None:
            raise ValueError(f"operation {op_id!r}: model ops (rotary or 3D) cannot be indexed")
        if kind in SURFACE_OPS and job.get("rotary"):
            raise ValueError(f"operation {op_id!r}: 3D ops cannot be wrapped onto a rotary job")
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
        elif kind in SURFACE_OPS:
            model = models[spec.pop("model")]
            if kind == "surface_rough" and "stock" not in spec:
                spec["stock"] = job.get("stock")
            tp = fn(model, tool, **spec)
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
            shape_name = spec.pop("shape")
            shape = shapes[shape_name]
            off = None
            if index_spec is None and not job.get("rotary"):
                off = shape_on_stock(shape_name, shape, job.get("stock"))
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
            if kind == "inlay":
                spec.pop("fit_with", None)        # camcore check's fit check reads it from the job
                tp = _inlay(shape, tool, spec, clear, clear_args, tools, job, params, rpm, op_id, after, mine)
            else:
                tp = fn(shape, tool, **spec)
            if off:
                tp.notes.append(f"{op_id}: {off}")
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


def _inlay(shape, tool, spec, clear, clear_args, tools, job, params, rpm, op_id, after, mine):
    """An inlay part: the clear tool's floor (pocket) or flange (plug) first, then the V-bit."""
    if not clear:
        raise ValueError(f"operation {op_id!r}: inlay needs a clear_tool (an end mill for the flat areas)")
    if spec.get("part", "pocket") == "plug" and "stock" not in spec:
        spec["stock"] = job.get("stock")
    region, depth = ops.inlay_clear_area(shape, tool, tools[clear], **spec)
    ct = None
    if not region.is_empty:
        cargs = params(ops.pocket, clear, {"top_z": spec.get("top_z", 0.0), "safe_z": spec.get("safe_z", 5.0),
                                           **clear_args})
        cargs.setdefault("step_down", depth)
        crpm = cargs.pop("rpm", rpm)
        ct = ops.pocket(region, tools[clear], depth, name="inlay-clear", **cargs)
        ct.spindle_rpm = crpm
    tp = ops.inlay(shape, tool, **spec)
    if ct is not None and any(m.kind == "feed" for m in ct.moves):
        mine.append([ct, f"{op_id}:clear", set(after)])
    elif ct is not None:
        # flat areas narrower than the clear tool: an empty pass would still change tools
        tp.notes = ct.notes + [f"{op_id}: nothing to clear that {clear} fits into, clear pass left out"] + tp.notes
    return tp


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
