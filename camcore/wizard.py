"""camcore rotary-wizard MODEL.stl: a guided rotary-from-STL job (guide step 8C).

Asks how the model lies on the axis, the stock, the tools and an accuracy
preset, writes OUT/NAME_job.json, runs `camcore check` on it and opens the
viewer. Every question has a flag, and --yes takes the suggested answer for
the rest, so the wizard also runs from scripts. The job it writes is an
ordinary job file: edit it and run `camcore check` again.

Layout along X (chuck on the low-X side):

    x0 ........ x0 + hold | clearance | 0 ...... model ...... L | free end
    [ in the chuck jaws  ]            [ placed model, from X0  ]  stock ends here
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import webbrowser
from pathlib import Path

from .check import DEFAULT_MACHINE, check
from .job import build_model

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "examples" / "tools.json"

STOCK_MARGIN = 2.0     # mm of stock around the model's largest radius, per side
FREE_END = 2.0         # mm of stock beyond the model's far end
JAW_CLEARANCE = 2.0    # mm between the chuck jaws and the closest a tool can reach

# rough grid, stock left for finishing, finish grid, scallop height (ball) or stepover ratio (flat)
PRESETS = {
    "coarse": {"rough_resolution": 1.0, "stock_to_leave": 0.8, "finish_resolution": 0.5,
               "scallop": 0.05, "flat_stepover_ratio": 0.3},
    "normal": {"rough_resolution": 0.5, "stock_to_leave": 0.5, "finish_resolution": 0.25,
               "scallop": 0.02, "flat_stepover_ratio": 0.15},
    "fine": {"rough_resolution": 0.25, "stock_to_leave": 0.3, "finish_resolution": 0.15,
             "scallop": 0.005, "flat_stepover_ratio": 0.08},
}
STRATEGIES = ("helical", "a_raster", "x_raster")


class WizardError(ValueError):
    pass


def model_size(mesh) -> tuple[float, float]:
    """(length along X, largest distance from the X axis) of a placed mesh. A
    triangle's farthest point from a line is one of its corners, so the
    vertices give the exact radius."""
    v = mesh.vertices
    return float(v[:, 0].max() - v[:, 0].min()), float(((v[:, 1] ** 2 + v[:, 2] ** 2).max()) ** 0.5)


def longest_axis(mesh) -> str:
    ext = mesh.bounds[1] - mesh.bounds[0]
    return "xyz"[int(ext.argmax())]


def finish_stepover(tool: dict, preset: dict) -> float:
    """Stepover for the finishing tool: the scallop height of the preset for a
    ball, a fraction of the diameter otherwise. Never more than half the diameter."""
    d = tool["diameter"]
    if tool.get("kind") == "ball":
        r = d / 2
        h = min(preset["scallop"], r)
        s = 2 * math.sqrt(2 * r * h - h * h)
    else:
        s = d * preset["flat_stepover_ratio"]
    return math.floor(round(min(s, d / 2) * 100, 6)) / 100


def suggest_rough(lib: dict, material) -> str | None:
    """Largest flat end mill (else ball) with cutting data for the material."""
    for kind in ("flat", "ball"):
        c = [k for k, t in lib.items() if t.get("kind", "flat") == kind and material in t.get("cutting", {})]
        if c:
            return max(c, key=lambda k: (lib[k]["diameter"], k))
    return None


def suggest_finish(lib: dict, material) -> str | None:
    """Smallest ball end mill (else flat) with cutting data for the material."""
    for kind in ("ball", "flat"):
        c = [k for k, t in lib.items() if t.get("kind", "flat") == kind and material in t.get("cutting", {})]
        if c:
            return min(c, key=lambda k: (lib[k]["diameter"], k))
    return None


def layout(length: float, stock_length: float, hold: float, reach: float) -> tuple[float, float]:
    """Stock X range [x0, end] for a model from X0 to `length`. Refuses a stock
    so short that a tool reaching `reach` below X0 comes near the chuck jaws."""
    end = math.ceil(length + FREE_END)
    x0 = end - stock_length
    if x0 + hold > -(reach + JAW_CLEARANCE) + 1e-9:
        need = end + hold + reach + JAW_CLEARANCE
        raise WizardError(f"stock of {stock_length:g} mm is too short: with {hold:g} mm in the chuck the tools "
                          f"come within {JAW_CLEARANCE:g} mm of the jaws; it needs at least {need:g} mm")
    return float(x0), float(end)


def suggest_length(length: float, hold: float, reach: float) -> float:
    return math.ceil(length + FREE_END) + hold + math.ceil(reach + JAW_CLEARANCE)


def build_job(stl: Path, out_dir: Path, a: dict, lib: dict, library_path: Path, machine_path: Path | None,
              length: float) -> dict:
    """The job for the answers `a` (see run()); paths are relative to out_dir, where it is written."""
    rel = lambda p: portable(Path(p), out_dir)
    preset = PRESETS[a["preset"]]
    rough_r, finish_r = lib[a["rough_tool"]]["diameter"] / 2, lib[a["finish_tool"]]["diameter"] / 2
    x0, end = layout(length, a["stock_length"], a["hold"], max(rough_r, finish_r))
    name = stl.stem
    job = {
        "post": "linuxcnc",
        "tool_library": rel(library_path),
        "material": a["material"],
        "stock": {"type": "cylinder", "diameter": a["stock_diameter"], "x": [x0, end]},
        "models": {name: model_spec(rel(stl), a)},
        "defaults": {"safe_z": 5.0},
        "operations": [
            {"id": "rough", "op": "rotary_rough", "model": name, "tool": a["rough_tool"],
             "x_range": [0.0, end + 1.0], "floor": a["floor"], "stock_to_leave": preset["stock_to_leave"],
             "resolution": preset["rough_resolution"]},
            {"id": "finish", "op": "rotary_finish", "model": name, "tool": a["finish_tool"], "after": ["rough"],
             "strategy": a["strategy"], "stepover": finish_stepover(lib[a["finish_tool"]], preset),
             "x_range": [0.0, math.ceil((length + finish_r) * 2) / 2], "floor": a["floor"],
             "resolution": preset["finish_resolution"]},
        ],
        "wizard": {"chuck_hold": a["hold"], "preset": a["preset"]},
    }
    if machine_path is not None:
        job["machine"] = rel(machine_path)
    return job


def portable(path: Path, out_dir: Path) -> str:
    """`path` relative to out_dir if they share a folder below /, else absolute."""
    p, o = path.resolve(), out_dir.resolve()
    return os.path.relpath(p, o) if os.path.commonpath([p, o]) != p.anchor else str(p)


def suggest_floor(stock_diameter: float, flute: float) -> float:
    """10 % of the stock diameter (at least 1 mm), but deep enough for the flutes to reach."""
    return max(1.0, round(0.1 * stock_diameter, 1), math.ceil((stock_diameter / 2 - flute) * 10) / 10)


def model_spec(file: str, a: dict) -> dict:
    return {"file": file, "along": a["along"], "flip": a["flip"], "rotate_x": a["rotate_x"],
            "center_yz": True, "x_start": 0.0}


# --- questions -----------------------------------------------------------------------------------

def number(lo=None, hi=None, why_lo="", why_hi=""):
    def parse(s):
        try:
            v = float(s)
        except ValueError:
            raise WizardError(f"{s!r} is not a number") from None
        if not math.isfinite(v) or (lo is not None and v < lo):
            raise WizardError(f"{v:g} is too small{why_lo}")
        if hi is not None and v > hi:
            raise WizardError(f"{v:g} is too large{why_hi}")
        return v
    return parse


def choice(options):
    def parse(s):
        if s not in options:
            raise WizardError(f"{s!r} is not one of {', '.join(options)}")
        return s
    return parse


def yes_no(s):
    s = str(s).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    raise WizardError(f"{s!r}: answer y or n")


class Asker:
    """Ask one question: a flag value wins, then --yes takes the default, else prompt until valid."""

    def __init__(self, yes: bool, input_fn, log):
        self.yes, self.input, self.log = yes, input_fn, log

    def __call__(self, question, default, parse, given=None):
        if given is not None:
            v = parse(str(given))
            self.log(f"{question}: {given}")
            return v
        if self.yes:
            self.log(f"{question}: {default}")
            return parse(str(default))
        while True:
            raw = self.input(f"{question} [{default}]: ").strip()
            try:
                return parse(raw or str(default))
            except WizardError as e:
                self.log(f"  {e}")


def fmt(v: float) -> str:
    return f"{v:g}"


def run(args, input_fn=input, log=print) -> int:
    from .stl import load_stl

    stl = Path(args.model)
    out = Path(args.out_dir)
    library_path = Path(args.tools) if args.tools else DEFAULT_LIBRARY
    machine_path = Path(args.machine) if args.machine else None
    mdata = json.loads((machine_path or DEFAULT_MACHINE).read_text())
    lib = json.loads(library_path.read_text())
    ask = Asker(args.yes, input_fn, log)

    raw = load_stl(stl)
    ext = raw.bounds[1] - raw.bounds[0]
    log(f"model {stl}: {len(raw.faces)} triangles, {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm (X x Y x Z)"
        + ("" if raw.is_watertight else "  (not watertight: check the result in the viewer)"))
    log(f"tool library {library_path}, machine {machine_path or DEFAULT_MACHINE}")

    a = {}
    a["along"] = ask("Model axis that goes on the rotary axis (x/y/z)", longest_axis(raw), choice("xyz"), args.axis)
    a["flip"] = ask("Turn it end for end, so the other end faces the chuck (y/n)", "n", yes_no,
                    "y" if args.flip else None)
    a["rotate_x"] = ask("Rotate about the rotary axis, degrees", fmt(0.0), number(-360, 360), args.rotate)
    model = build_model(model_spec(str(stl.resolve()), a), Path("."))
    length, r_max = model_size(model)
    log(f"placed on the axis (bounding box centred): X 0 .. {length:.2f} mm, largest radius {r_max:.2f} mm")

    materials = sorted({m for t in lib.values() if t.get("kind", "flat") in ("flat", "ball")
                        for m in t.get("cutting", {})})
    if not materials:
        raise WizardError(f"{library_path}: no flat or ball end mill has cutting data")
    a["material"] = ask(f"Material ({', '.join(materials)})", "hardwood" if "hardwood" in materials else materials[0],
                        choice(materials), args.material)
    usable = sorted(k for k, t in lib.items() if t.get("kind", "flat") in ("flat", "ball")
                    and a["material"] in t.get("cutting", {}))
    for k in usable:
        t = lib[k]
        log(f"  {k:<8} T{t.get('number', '?')}  {t['diameter']:g} mm {t.get('kind', 'flat')}")
    a["rough_tool"] = ask("Roughing tool", suggest_rough(lib, a["material"]), choice(usable), args.rough_tool)
    a["finish_tool"] = ask("Finishing tool", suggest_finish(lib, a["material"]), choice(usable), args.finish_tool)
    reach = max(lib[a["rough_tool"]]["diameter"], lib[a["finish_tool"]]["diameter"]) / 2

    swing = mdata.get("a_max_diameter")
    a["stock_diameter"] = ask("Stock diameter, mm", fmt(math.ceil(round(2 * r_max + 2 * STOCK_MARGIN, 3))),
                              number(round(2 * r_max, 3), swing, f": the model needs {2 * r_max:.2f} mm",
                                     f": the A axis swings at most {swing} mm"), args.diameter)
    a["hold"] = ask("Length held in the chuck jaws, mm", fmt(10.0), number(0), args.hold)
    stock_len = number(0)
    a["stock_length"] = ask("Stock length, mm", fmt(suggest_length(length, a["hold"], reach)),
                            lambda s: _checked_length(stock_len(s), length, a["hold"], reach), args.length)
    rs = a["stock_diameter"] / 2
    short = min((a["rough_tool"], a["finish_tool"]), key=lambda k: lib[k].get("flute_length", 20.0))
    flute = lib[short].get("flute_length", 20.0)
    a["floor"] = ask("Smallest radius to leave (support at the free end), mm",
                     fmt(suggest_floor(a["stock_diameter"], flute)),
                     number(max(0.5, rs - flute), rs - 0.5,
                            f": the {short} flutes are {flute:g} mm long, the stock radius is {rs:g} mm",
                            " for the stock"), args.floor)
    a["preset"] = ask("Accuracy (coarse/normal/fine)", "normal", choice(list(PRESETS)), args.preset)
    a["strategy"] = ask(f"Finishing strategy ({'/'.join(STRATEGIES)})", "helical", choice(STRATEGIES), args.strategy)

    out.mkdir(parents=True, exist_ok=True)
    job = build_job(stl, out, a, lib, library_path, machine_path, length)
    job_path = out / f"{stl.stem}_job.json"
    job_path.write_text(json.dumps(job, indent=2) + "\n")
    log(f"wrote {job_path} (edit it and run `camcore check {job_path}` to change anything)")

    ok = check(job_path, out, machine_path, sim=not args.no_sim, log=log)
    view = out / f"{stl.stem}_view.html"
    if ok and not args.no_open:
        webbrowser.open(view.resolve().as_uri())
    return 0 if ok else 1


def _checked_length(v, length, hold, reach):
    layout(length, v, hold, reach)
    return v


def main(argv=None, input_fn=input) -> int:
    ap = argparse.ArgumentParser(prog="camcore rotary-wizard",
                                 description="Guided rotary job from an STL: place, stock, tools, accuracy, "
                                             "then camcore check and the viewer")
    ap.add_argument("model", help="STL file (mm)")
    ap.add_argument("-o", "--out-dir", default="out", help="output folder for the job, program and viewer")
    ap.add_argument("--tools", help=f"tool library (default: {DEFAULT_LIBRARY})")
    ap.add_argument("--machine", help="machine JSON (default: machine/printnc_provisional.json)")
    ap.add_argument("-y", "--yes", action="store_true", help="take the suggested answer for every question not given")
    ap.add_argument("--axis", help="model axis that goes on the rotary axis: x, y or z (default: the longest)")
    ap.add_argument("--flip", action="store_true", help="turn the model end for end")
    ap.add_argument("--rotate", help="degrees about the rotary axis")
    ap.add_argument("--material")
    ap.add_argument("--rough-tool")
    ap.add_argument("--finish-tool")
    ap.add_argument("--diameter", help="stock diameter, mm")
    ap.add_argument("--hold", help="stock length held in the chuck, mm")
    ap.add_argument("--length", help="stock length, mm")
    ap.add_argument("--floor", help="smallest radius left, mm")
    ap.add_argument("--preset", help="coarse, normal or fine")
    ap.add_argument("--strategy", help="finishing: helical, a_raster or x_raster")
    ap.add_argument("--no-sim", action="store_true", help="skip the LinuxCNC sim (quick look only)")
    ap.add_argument("--no-open", action="store_true", help="don't open the viewer")
    args = ap.parse_args(argv)
    try:
        return run(args, input_fn)
    except WizardError as e:
        print(f"camcore rotary-wizard: {e}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\ncamcore rotary-wizard: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
