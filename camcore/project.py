"""Projects for the desktop app (camcore-gui). A project file is an ordinary
job JSON: the app edits it, and `camcore check` on the saved file is the only
way it makes a program. Nothing here imports Qt, so the model is tested without
a display, and nothing here makes toolpaths: parameter schemas come from the op
signatures and inherited values from `job.param_sources`, the code run_job uses.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import re
from pathlib import Path

from . import ops
from .job import cutting_data, param_sources
from .tools import Tool, library_from_dict
from .wizard import STRATEGIES, portable

OP_KINDS = ("pocket", "profile", "vcarve", "drill", "rotary_rough", "rotary_finish", "surface_rough",
            "surface_finish")
# What an op works on: a named shape, a named model, or a list of points.
TARGET = {"drill": "points", "rotary_rough": "model", "rotary_finish": "model", "surface_rough": "model",
          "surface_finish": "model"}
# Keys of an operation that are not op function parameters (job.run_job handles them).
STRUCT_KEYS = ("op", "id", "after", "tool", "shape", "model", "points", "index", "face_z", "clear_tool", "clear")
# Suggestions for string parameters; the ops themselves validate.
CHOICES = {"side": ("outside", "inside", "on"), "entry": ("ramp", "helix", "plunge"), "method": ("offset", "medial"),
           "strategy": STRATEGIES + ("raster", "waterline")}
TOOL_KINDS = ("flat", "ball", "bull", "vbit")
CUTTING_KEYS = ("rpm", "feed", "plunge", "step_down", "stepover_ratio", "ramp_angle")
DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "examples" / "tools.json"
REQUIRED = inspect.Parameter.empty


@dataclasses.dataclass(frozen=True)
class Param:
    name: str
    default: object          # REQUIRED if the op function has none

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


def target_key(kind: str) -> str:
    return TARGET.get(kind, "shape")


def op_params(kind: str) -> list[Param]:
    """The parameters a job operation of this kind takes, in signature order
    (without the shape/model/points and tool arguments)."""
    if kind not in OP_KINDS:
        raise ValueError(f"unknown operation {kind!r}")
    sig = list(inspect.signature(getattr(ops, kind)).parameters.values())[2:]
    return [Param(p.name, p.default) for p in sig] + [Param("rpm", 18000)]


def unknown_keys(op: dict) -> list[str]:
    """Keys of an operation that are neither structure nor a parameter of its op."""
    names = {p.name for p in op_params(op["op"])}
    return [k for k in op if k not in STRUCT_KEYS and k not in names]


def effective(job: dict, library: dict, op: dict) -> dict:
    """{param: (value, source)} for an operation as run_job will merge it.
    source is "op", "cutting" (the tool's data for the job's material) or
    "defaults"; parameters at the op function's default are absent. Raises
    ValueError like run_job when the tool lacks data for the material."""
    own = {k: v for k, v in op.items() if k not in STRUCT_KEYS}
    cut = cutting_data(library, op.get("tool"), job.get("material"))
    return param_sources(getattr(ops, op["op"]), own, job.get("defaults", {}), cut)


def parse_value(text: str):
    """Text from a parameter field: JSON (numbers, true/false, lists, objects),
    else the text itself as a string. Empty means "not set" (None)."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def format_value(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v)


def dumps(data, width: int = 100) -> str:
    """JSON as people write job files: indented, but short lists and objects on one line."""
    def enc(v, indent, col):
        """v at indent level `indent`, starting at column `col` of its line."""
        flat = json.dumps(v)
        if not isinstance(v, (dict, list)) or col + len(flat) <= width or not v:
            return flat
        pad = " " * (indent + 2)
        if isinstance(v, dict):
            items = [f"{pad}{json.dumps(k)}: " for k in v]
            items = [h + enc(x, indent + 2, len(h)) for h, x in zip(items, v.values())]
            return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
        return "[\n" + ",\n".join(pad + enc(x, indent + 2, len(pad)) for x in v) + "\n" + " " * indent + "]"
    return enc(data, 0, 0) + "\n"


def unique_name(base: str, taken) -> str:
    base = re.sub(r"\W+", "_", base).strip("_") or "item"
    if base not in taken:
        return base
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def new_job(tool_library: Path = DEFAULT_LIBRARY) -> dict:
    return {
        "post": "linuxcnc",
        "tool_library": str(tool_library),
        "stock": {"type": "box", "x": [0, 100], "y": [0, 100], "z": [-10, 0]},
        "defaults": {"safe_z": 5.0},
        "shapes": {},
        "operations": [],
    }


def describe_op(op: dict, i: int) -> str:
    """One line for the operation list (i: its position, which names it without an id)."""
    target = op.get(target_key(op["op"]))
    if isinstance(target, list):
        target = f"{len(target)} points"
    s = f"{op.get('id', f'#{i + 1}')}: {op['op']} {target or '?'} with {op.get('tool', '?')}"
    if "index" in op:
        s += f" (index {format_value(op['index'])})"
    return s


def _shape_files(spec: dict):
    """The shape specs (nested in union/difference too) that name a file."""
    if "file" in spec:
        yield spec
    for sub in spec.get("shapes", []):
        yield from _shape_files(sub)
    for k in ("a", "b"):
        if isinstance(spec.get(k), dict):
            yield from _shape_files(spec[k])


def file_refs(job: dict):
    """(container, key) of every file path in a job, relative to the job's folder."""
    for k in ("tool_library", "machine"):
        if k in job:
            yield job, k
    for spec in job.get("shapes", {}).values():
        for s in _shape_files(spec):
            yield s, "file"
    for spec in job.get("models", {}).values():
        if "file" in spec:
            yield spec, "file"


def rebase(job: dict, old_base: Path, new_base: Path) -> dict:
    """A copy of the job whose relative file paths still point at the same files
    when it is saved in new_base instead of old_base."""
    job = json.loads(json.dumps(job))
    for container, key in file_refs(job):
        p = Path(container[key])
        if not p.is_absolute():
            container[key] = portable(old_base / p, new_base)
    return job


def job_library(job: dict, base: Path) -> dict:
    """The raw tool dicts the job sees (check.job_library), or only its inline
    tools while the library file is missing or broken."""
    from .check import job_library as library
    try:
        return library(job, base)
    except (OSError, json.JSONDecodeError):
        return dict(job.get("tools", {}))


def materials(library: dict) -> list[str]:
    return sorted({m for t in library.values() for m in t.get("cutting", {})})


def tool_fields() -> list[dataclasses.Field]:
    """Editable fields of a library tool (Tool's dataclass fields except its name)."""
    return [f for f in dataclasses.fields(Tool) if f.name != "name"]


def save_library(path: Path, data: dict) -> None:
    """Write a tool library after the checks load_library makes (refuses, writes nothing, if they fail)."""
    library_from_dict(data)
    Path(path).write_text(dumps(data))


class Project:
    """A job being edited: the job dict, its file (None until first saved), and
    whether it changed since. Relative paths in the job resolve from `base`."""

    def __init__(self, job: dict | None = None, path: Path | None = None):
        self.job = job if job is not None else new_job()
        self.path = Path(path) if path else None
        self.dirty = False

    @classmethod
    def load(cls, path) -> "Project":
        path = Path(path)
        return cls(json.loads(path.read_text()), path.resolve())

    @property
    def base(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    @property
    def name(self) -> str:
        return self.path.stem.removesuffix("_job") if self.path else "untitled"

    def save(self, path=None) -> Path:
        """Save (to `path` if given: a Save As, which rebases relative file paths)."""
        if path is not None:
            path = Path(path).resolve()
            if path.parent != self.base.resolve():
                self.job = rebase(self.job, self.base, path.parent)
            self.path = path
        if self.path is None:
            raise ValueError("the project has no file yet")
        self.path.write_text(dumps(self.job))
        self.dirty = False
        return self.path

    def changed(self):
        self.dirty = True

    def rel(self, path) -> str:
        """How the job should refer to a file: relative to its folder where possible."""
        return portable(Path(path), self.base)

    def library_path(self) -> Path | None:
        lib = self.job.get("tool_library")
        return self.base / lib if lib else None

    def library(self) -> dict:
        return job_library(self.job, self.base)

    # --- shapes, models and operations ----------------------------------------------------------

    def add_shape(self, spec: dict, name: str) -> str:
        shapes = self.job.setdefault("shapes", {})
        name = unique_name(name, shapes)
        shapes[name] = spec
        self.changed()
        return name

    def add_shape_file(self, path) -> str:
        path = Path(path)
        kind = path.suffix.lower().lstrip(".")
        if kind not in ("svg", "dxf"):
            raise ValueError(f"{path.name}: shapes come from .svg or .dxf files")
        return self.add_shape({"type": kind, "file": self.rel(path)}, path.stem)

    def add_model_file(self, path) -> str:
        path = Path(path)
        models = self.job.setdefault("models", {})
        name = unique_name(path.stem, models)
        models[name] = {"file": self.rel(path)}
        self.changed()
        return name

    def rename(self, section: str, old: str, new: str) -> str:
        """Rename a shape or model and every operation that uses it."""
        items = self.job[section]
        new = unique_name(new, set(items) - {old})
        if new == old:
            return old
        self.job[section] = {(new if k == old else k): v for k, v in items.items()}
        key = "shape" if section == "shapes" else "model"
        for op in self.job.get("operations", []):
            if op.get(key) == old:
                op[key] = new
        self.changed()
        return new

    def users(self, section: str, name: str) -> list[str]:
        key = "shape" if section == "shapes" else "model"
        return [op.get("id", f"#{i + 1}") for i, op in enumerate(self.job.get("operations", [])) if op.get(key) == name]

    def add_op(self, kind: str) -> dict:
        """A new operation of this kind at the end: a fresh id, the first tool that
        suits it, and the first shape or model (the user picks the rest)."""
        if kind not in OP_KINDS:
            raise ValueError(f"unknown operation {kind!r}")
        opsl = self.job.setdefault("operations", [])
        op = {"id": unique_name(kind, {o.get("id") for o in opsl}), "op": kind}
        lib = self.library()
        want = ("vbit",) if kind == "vcarve" else ("flat", "ball")
        tools = [k for k, t in lib.items() if t.get("kind", "flat") in want]
        if tools:
            op["tool"] = min(tools, key=lambda k: (lib[k].get("number") or 0, k))
        tk = target_key(kind)
        if tk == "points":
            op["points"] = []
        else:
            names = list(self.job.get("shapes" if tk == "shape" else "models", {}))
            if names:
                op[tk] = names[0]
        opsl.append(op)
        self.changed()
        return op

    def move_op(self, i: int, delta: int) -> int:
        opsl = self.job["operations"]
        j = i + delta
        if not (0 <= i < len(opsl) and 0 <= j < len(opsl)):
            return i
        opsl[i], opsl[j] = opsl[j], opsl[i]
        self.changed()
        return j

    def remove_op(self, i: int) -> dict:
        op = self.job["operations"].pop(i)
        self.changed()
        return op

    def set_op_value(self, op: dict, key: str, value) -> None:
        """Set (or with None, remove) a key of an operation."""
        if value is None:
            if key in op:
                del op[key]
                self.changed()
        elif op.get(key, object()) != value:
            op[key] = value
            self.changed()
