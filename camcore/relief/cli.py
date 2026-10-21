"""camcore relief INPUT: a relief design from an image or STL (relief plan phase 6).

Asks for the design (lamellas, a 3D relief or pins), writes ordinary jobs plus the
paperwork into the output folder and runs `camcore check` on every job, as
`camcore rotary-wizard` does. Every question has a flag, --yes takes the suggested
answer for the rest, and the same QUESTIONS drive the app's relief dialog.

Output (NAME = the input's name):
  lamellas  NAME_sheetN_job.json + checked .ngc per board, NAME_parts.csv,
            NAME_assembly.svg, NAME_boardN.svg/.dxf
  3d        NAME_job.json, NAME.stl and the checked .ngc
  pins      NAME_cut_list.csv, NAME_positions.csv, NAME_pin_plan.svg/.dxf (nothing to machine)
  all       NAME_height.png: the prepared relief, white = high, as seen from the front
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..check import DEFAULT_MACHINE, check
from ..wizard import DEFAULT_LIBRARY, Asker, WizardError, choice, fmt, number, yes_no
from . import heightfield as hf
from .lamella import LamellaSettings
from .lamella_job import SheetSettings, machine_window, write_jobs
from .pins import SHAPES, PinSettings, pin_grid
from .relief3d import DIRECTIONS as FINISH_DIRS
from .relief3d import Relief3D, write_job
from .sheets import write_pin_paperwork

MODES = ("lamellas", "3d", "pins")
OVERCUT = 0.3


class Session:
    """Input, tool library, machine and the answers so far (`a`)."""

    def __init__(self, source: Path, lib: dict, mdata: dict, library_path=None):
        self.source, self.lib, self.mdata = Path(source), lib, mdata
        self.library_path = library_path or "the tool library"
        self.a: dict = {}
        self._raw = None

    @property
    def is_stl(self) -> bool:
        return self.source.suffix.lower() == ".stl"

    def raw(self) -> np.ndarray:
        """The raw field (an STL seen from the chosen side)."""
        key = self.a.get("stl_view", "z")
        if self._raw is None or self._raw[0] != key:
            self._raw = (key, hf.load(self.source, stl_view=key))
        return self._raw[1]

    def aspect(self) -> float:
        """Height / width of the raw field after turning."""
        h, w = self.raw().shape
        return w / h if self.a.get("rotate", 0) in (90, 270) else h / w

    def materials(self) -> list[str]:
        m = sorted({m for t in self.lib.values() for m in t.get("cutting", {})})
        if not m:
            raise WizardError(f"{self.library_path}: no tool has cutting data")
        return m

    def tools(self, kinds, min_flutes: float = 0.0) -> list[str]:
        """Tools of these kinds with cutting data for the material and flutes long enough."""
        return sorted(k for k, t in self.lib.items() if t.get("kind", "flat") in kinds
                      and self.a.get("material") in t.get("cutting", {})
                      and t.get("flute_length", math.inf) >= min_flutes - 1e-9)

    def tool_choice(self, kinds, min_flutes: float, what: str):
        ok = self.tools(kinds, min_flutes)
        if not ok:
            raise WizardError(f"no {'/'.join(kinds)} tool in {self.library_path} has cutting data for "
                              f"{self.a.get('material')} and flutes of {min_flutes:g} mm for the {what}")
        return ok

    def suggest(self, options: list[str]) -> str:
        """The largest diameter (fastest), then the lowest number."""
        return max(options, key=lambda k: (self.lib[k]["diameter"], -self.lib[k].get("number", 0)))

    def window(self) -> dict:
        return machine_window(self.mdata)


@dataclass(frozen=True)
class Question:
    """One relief question, like wizard.Question, plus where it applies: `modes`, and
    `gate` (the key of a yes/no answer that must be yes; otherwise the default is
    taken without asking, unless a flag gives the value)."""
    key: str
    text: Callable
    default: Callable
    parse: Callable
    options: Callable | None = None
    flag: Callable = None
    modes: tuple = MODES
    gate: str | None = None
    when: Callable = field(default=lambda s: True)

    def applies(self, s: Session) -> bool:
        return (self.key == "mode" or s.a.get("mode") in self.modes) and self.when(s)


def _int(lo=None, hi=None):
    def parse(v):
        x = number(lo, hi)(v)
        if x != int(x):
            raise WizardError(f"{v!r} is not a whole number")
        return int(x)
    return parse


def _board(axis):
    def parse(s):
        lo, hi = s.window()[axis]
        return number(1, hi, "", f": the machine reaches {hi:g} mm from the G54 zero")
    return parse


def _default_board(s, axis, want):
    return fmt(min(want, s.window()[axis][1]))


def _levels(v):
    n = _int(0, 64)(v)
    if n == 1:
        raise WizardError("levels must be 0 (any length) or at least 2")
    return n


Q = Question
QUESTIONS = [
    Q("mode", lambda s: f"Design ({'/'.join(MODES)})", lambda s: "lamellas", lambda s: choice(MODES),
      lambda s: list(MODES), lambda args: args.mode),
    Q("stl_view", lambda s: "Look at the STL from (z = above, y = the front, x = the side)", lambda s: "z",
      lambda s: choice(hf.VIEWS), lambda s: list(hf.VIEWS), lambda args: args.view, when=lambda s: s.is_stl),
    Q("adjust", lambda s: "Adjust the image (turn, mirror, crop, smoothing, contrast)? (y/n)", lambda s: "n",
      lambda s: yes_no, lambda s: ["n", "y"], lambda args: None),
    Q("rotate", lambda s: "Turn clockwise, degrees (0/90/180/270)", lambda s: "0",
      lambda s: lambda v: int(choice(("0", "90", "180", "270"))(v)), lambda s: ["0", "90", "180", "270"],
      lambda args: args.rotate, gate="adjust"),
    Q("mirror", lambda s: "Mirror left to right (y/n)", lambda s: "n", lambda s: yes_no, lambda s: ["n", "y"],
      lambda args: args.mirror, gate="adjust"),
    Q("width", lambda s: "Artwork width, mm" + (" (the frame goes around it)" if s.a["mode"] == "3d" else ""),
      lambda s: fmt(300.0 if s.a["mode"] == "3d" else 600.0), lambda s: number(10, 5000),
      flag=lambda args: args.width),
    Q("height", lambda s: "Artwork height, mm", lambda s: fmt(round(s.a["width"] * s.aspect())),
      lambda s: number(10, 5000), flag=lambda args: args.height),
    Q("fit", lambda s: "Fit the image: crop to the shape or stretch (cover/stretch)", lambda s: "cover",
      lambda s: choice(hf.FITS), lambda s: list(hf.FITS), lambda args: args.fit, gate="adjust"),
    Q("blur", lambda s: "Smoothing, mm", lambda s: fmt(2.0), lambda s: number(0, 50), flag=lambda args: args.blur,
      gate="adjust"),
    Q("black", lambda s: "Black point, % (darker becomes the lowest)", lambda s: fmt(0.0), lambda s: number(0, 99),
      flag=lambda args: args.black, gate="adjust"),
    Q("white", lambda s: "White point, % (lighter becomes the highest)", lambda s: fmt(100.0),
      lambda s: number(s.a["black"] + 1, 100, f": it must be above the black point ({s.a['black']:g} %)"),
      flag=lambda args: args.white, gate="adjust"),
    Q("gamma", lambda s: "Gamma (above 1 lowers the mid tones)", lambda s: fmt(1.0), lambda s: number(0.2, 5),
      flag=lambda args: args.gamma, gate="adjust"),
    Q("invert", lambda s: "Invert, dark = high (y/n)", lambda s: "n", lambda s: yes_no, lambda s: ["n", "y"],
      lambda args: args.invert, gate="adjust"),
    Q("material", lambda s: f"Material ({', '.join(s.materials())})",
      lambda s: "hardwood" if "hardwood" in s.materials() else s.materials()[0],
      lambda s: choice(s.materials()), lambda s: s.materials(), lambda args: args.material, modes=("lamellas", "3d")),
    # lamellas
    Q("direction", lambda s: "Lamellas run (v = top to bottom, h = side to side)", lambda s: "v",
      lambda s: choice(("v", "h")), lambda s: ["v", "h"], lambda args: args.direction, modes=("lamellas",)),
    Q("thickness", lambda s: "Board thickness = lamella thickness, mm", lambda s: fmt(18.0), lambda s: number(3, 100),
      flag=lambda args: args.thickness, modes=("lamellas",)),
    Q("gap", lambda s: "Gap between lamellas, mm", lambda s: fmt(0.0), lambda s: number(0, 50),
      flag=lambda args: args.gap, modes=("lamellas",)),
    Q("base", lambda s: "Height of the lowest point (black), mm", lambda s: fmt(15.0), lambda s: number(1, 300),
      flag=lambda args: args.base, modes=("lamellas",)),
    Q("relief", lambda s: "Extra height for white, mm", lambda s: fmt(60.0), lambda s: number(0, 400),
      flag=lambda args: args.relief, modes=("lamellas",)),
    Q("lam_tool", lambda s: "Cutting tool", lambda s: s.suggest(s.tool_choice(("flat", "bull"), s.a["thickness"] + OVERCUT, "cut")),
      lambda s: choice(s.tool_choice(("flat", "bull"), s.a["thickness"] + OVERCUT, "cut")),
      lambda s: s.tool_choice(("flat", "bull"), s.a["thickness"] + OVERCUT, "cut"), lambda args: args.tool,
      modes=("lamellas",)),
    Q("board_width", lambda s: "Board width (machine X), mm", lambda s: _default_board(s, "x", 1000.0), _board("x"),
      flag=lambda args: args.board_width, modes=("lamellas",)),
    Q("board_height", lambda s: "Board depth (machine Y), mm", lambda s: _default_board(s, "y", 600.0), _board("y"),
      flag=lambda args: args.board_height, modes=("lamellas",)),
    Q("allow_turn", lambda s: "Parts may turn 90 deg on the board (grain across) (y/n)", lambda s: "y",
      lambda s: yes_no, lambda s: ["y", "n"], lambda args: args.turn, modes=("lamellas",)),
    Q("interlock", lambda s: "Interlock parts head to tail to save board (y/n)", lambda s: "n", lambda s: yes_no,
      lambda s: ["n", "y"], lambda args: args.interlock, modes=("lamellas",)),
    Q("tabs", lambda s: "Holding tabs per part (on its bottom edge)", lambda s: "2", lambda s: _int(0, 8),
      flag=lambda args: args.tabs, modes=("lamellas",)),
    # 3D
    Q("depth", lambda s: "Relief depth (black), mm", lambda s: fmt(20.0), lambda s: number(1, 200),
      flag=lambda args: args.depth, modes=("3d",)),
    Q("frame", lambda s: "Frame left at full height around the relief, mm", lambda s: fmt(0.0),
      lambda s: number(0, 300), flag=lambda args: args.frame, modes=("3d",)),
    Q("stock_thickness", lambda s: "Stock thickness, mm", lambda s: fmt(s.a["depth"] + 5),
      lambda s: number(s.a["depth"] + 1, 200, f": it must be at least 1 mm more than the depth ({s.a['depth']:g} mm)"),
      flag=lambda args: args.thickness, modes=("3d",)),
    Q("rough", lambda s: "Rough first (y/n; finishing alone only for very shallow reliefs)", lambda s: "y",
      lambda s: yes_no, lambda s: ["y", "n"], lambda args: args.rough, modes=("3d",)),
    Q("rough_tool", lambda s: "Roughing tool", lambda s: s.suggest(s.tool_choice(("flat", "bull"), s.a["depth"], "roughing")),
      lambda s: choice(s.tool_choice(("flat", "bull"), s.a["depth"], "roughing")),
      lambda s: s.tool_choice(("flat", "bull"), s.a["depth"], "roughing"), lambda args: args.rough_tool,
      modes=("3d",), when=lambda s: s.a["rough"]),
    Q("finish_tool", lambda s: "Finishing tool", lambda s: min(s.tool_choice(("ball",), 0, "finishing"),
                                                                  key=lambda k: s.lib[k]["diameter"]),
      lambda s: choice(s.tool_choice(("ball", "bull", "flat"), 0, "finishing")),
      lambda s: s.tool_choice(("ball", "bull", "flat"), 0, "finishing"), lambda args: args.finish_tool, modes=("3d",)),
    Q("finish_dir", lambda s: "Finishing passes along (x/y/xy)", lambda s: "x", lambda s: choice(tuple(FINISH_DIRS)),
      lambda s: list(FINISH_DIRS), lambda args: args.finish_dir, modes=("3d",)),
    Q("resolution", lambda s: "Model grid, mm (finer = slower)", lambda s: fmt(0.5), lambda s: number(0.1, 5),
      flag=lambda args: args.resolution, modes=("3d",)),
    # pins
    Q("shape", lambda s: f"Pin shape ({'/'.join(SHAPES)})", lambda s: "square", lambda s: choice(SHAPES),
      lambda s: list(SHAPES), lambda args: args.shape, modes=("pins",)),
    Q("size", lambda s: "Pin size (edge, diameter or across flats), mm", lambda s: fmt(30.0), lambda s: number(3, 300),
      flag=lambda args: args.size, modes=("pins",)),
    Q("pin_gap", lambda s: "Gap between pins, mm", lambda s: fmt(2.0), lambda s: number(0, 50),
      flag=lambda args: args.gap, modes=("pins",)),
    Q("pin_base", lambda s: "Shortest pin (black), mm", lambda s: fmt(10.0), lambda s: number(1, 300),
      flag=lambda args: args.base, modes=("pins",)),
    Q("pin_relief", lambda s: "Extra length for white, mm", lambda s: fmt(50.0), lambda s: number(0, 400),
      flag=lambda args: args.relief, modes=("pins",)),
    Q("levels", lambda s: "Different lengths (0 = any, rounded to 0.5 mm)", lambda s: "8", lambda s: _levels,
      flag=lambda args: args.levels, modes=("pins",)),
    Q("kerf", lambda s: "Saw cut, mm", lambda s: fmt(3.0), lambda s: number(0, 10), flag=lambda args: args.kerf,
      modes=("pins",)),
]

def ask_all(s: Session, ask: Asker, args, log=lambda m: None):
    """Answer every question that applies, in order (gated ones only if their gate is yes,
    or if a flag answers them)."""
    for q in QUESTIONS:
        if not q.applies(s):
            continue
        given = q.flag(args) if args is not None else None
        if q.gate is not None and not s.a.get(q.gate) and given is None:
            s.a[q.key] = q.parse(s)(str(q.default(s)))
            continue
        s.a[q.key] = ask(q.text(s), q.default(s), q.parse(s), given)
        if q.key == "height":
            log(f"artwork {s.a['width']:g} x {s.a['height']:g} mm")


def field_of(s: Session) -> hf.HeightField:
    a = s.a
    return hf.prepare(s.raw(), a["width"], a["height"], fit=a["fit"], rotate=a["rotate"], mirror=a["mirror"],
                      blur=a["blur"], black=a["black"], white=a["white"], gamma=a["gamma"], invert=a["invert"])


def source_record(s: Session, out: Path) -> dict:
    from ..wizard import portable

    keys = ("width", "height", "fit", "rotate", "mirror", "blur", "black", "white", "gamma", "invert")
    rec = {"file": portable(s.source, out), **{k: s.a[k] for k in keys}}
    if s.is_stl:
        rec["stl_view"] = s.a["stl_view"]
    return rec


def height_png(F: hf.HeightField, path: Path):
    from PIL import Image

    Image.fromarray(np.round(np.clip(F.z, 0, 1) * 255).astype(np.uint8)).save(path)


def design(s: Session, out: Path, name: str, library_path: Path, machine_path: Path | None) -> list[Path]:
    """Write the design's files; return the job files to check (none for pins)."""
    a = s.a
    F = field_of(s)
    out.mkdir(parents=True, exist_ok=True)
    height_png(F, out / f"{name}_height.png")
    src = source_record(s, out)
    if a["mode"] == "lamellas":
        ls = LamellaSettings(direction=a["direction"], thickness=a["thickness"], gap=a["gap"], base=a["base"],
                             relief=a["relief"])
        st = SheetSettings(width=a["board_width"], height=a["board_height"], tool=a["lam_tool"], overcut=OVERCUT,
                           allow_turn=a["allow_turn"], interlock=a["interlock"], tabs=a["tabs"])
        return write_jobs(F, ls, st, out, name, library_path, a["material"], machine_path, src)
    if a["mode"] == "3d":
        d = Relief3D(depth=a["depth"], thickness=a["stock_thickness"], frame=a["frame"], resolution=a["resolution"],
                     rough=a["rough"], rough_tool=a.get("rough_tool", "em6"), finish_tool=a["finish_tool"],
                     finish_dir=a["finish_dir"])
        return [write_job(F, d, out, name, library_path, a["material"], machine_path, src)]
    st = PinSettings(shape=a["shape"], size=a["size"], gap=a["pin_gap"], base=a["pin_base"],
                     relief=a["pin_relief"], levels=a["levels"], kerf=a["kerf"])
    write_pin_paperwork(F, st, pin_grid(F, st), out, name)
    return []


def run(args, input_fn=input, log=print) -> int:
    source = Path(args.input)
    out = Path(args.out_dir)
    library_path = Path(args.tools) if args.tools else DEFAULT_LIBRARY
    machine_path = Path(args.machine) if args.machine else None
    mdata = json.loads((machine_path or DEFAULT_MACHINE).read_text())
    lib = json.loads(library_path.read_text())
    name = args.name or source.stem
    s = Session(source, lib, mdata, library_path)
    log(f"input {source}, tool library {library_path}, machine {machine_path or DEFAULT_MACHINE}")
    ask_all(s, Asker(args.yes, input_fn, log), args, log)
    try:
        jobs = design(s, out, name, library_path, machine_path)
    except ValueError as e:                 # the design checks (board, tools, depth, ...)
        raise WizardError(str(e)) from None
    log(f"wrote the design into {out}")
    ok = True
    for job in jobs:
        log(f"checking {job.name} ...")
        ok = check(job, out, machine_path, sim=not args.no_sim, log=log) and ok
    if jobs and ok and not args.no_open:
        stem = jobs[0].stem.removesuffix("_job")
        webbrowser.open((out / f"{stem}_view.html").resolve().as_uri())
    if not jobs:
        log("pins: nothing to machine, the cut list and plans are in the output folder")
    return 0 if ok else 1


def main(argv=None, input_fn=input) -> int:
    ap = argparse.ArgumentParser(prog="camcore relief",
                                 description="Relief design from an image or STL: lamellas, a 3D relief or pins; "
                                             "writes the jobs and paperwork and runs camcore check on every job")
    ap.add_argument("input", help="image (bright = high) or STL")
    ap.add_argument("-o", "--out-dir", default="out", help="output folder")
    ap.add_argument("--name", help="file name stem (default: the input's)")
    ap.add_argument("--tools", help=f"tool library (default: {DEFAULT_LIBRARY})")
    ap.add_argument("--machine", help="machine JSON (default: machine/printnc_provisional.json)")
    ap.add_argument("-y", "--yes", action="store_true", help="take the suggested answer for every question not given")
    ap.add_argument("--no-sim", action="store_true", help="skip the LinuxCNC sim (quick look only)")
    ap.add_argument("--no-open", action="store_true", help="don't open the viewer")
    g = ap.add_argument_group("design")
    g.add_argument("--mode", help="lamellas, 3d or pins")
    g.add_argument("--view", help="STL seen from z (above), y (front) or x (side)")
    g.add_argument("--width", help="artwork width, mm")
    g.add_argument("--height", help="artwork height, mm")
    g.add_argument("--material")
    g.add_argument("--base", help="lowest height (lamellas) or shortest pin, mm")
    g.add_argument("--relief", help="extra height for white, mm")
    g.add_argument("--gap", help="between lamellas or pins, mm")
    g.add_argument("--thickness", help="board (lamellas) or stock (3d) thickness, mm")
    i = ap.add_argument_group("image")
    i.add_argument("--rotate", help="turn clockwise: 0, 90, 180 or 270")
    i.add_argument("--mirror", help="y/n")
    i.add_argument("--fit", help="cover (crop) or stretch")
    i.add_argument("--blur", help="smoothing, mm")
    i.add_argument("--black", help="black point, %%")
    i.add_argument("--white", help="white point, %%")
    i.add_argument("--gamma")
    i.add_argument("--invert", help="y/n: dark = high")
    lam = ap.add_argument_group("lamellas")
    lam.add_argument("--direction", help="v (top to bottom) or h (side to side)")
    lam.add_argument("--tool", help="cutting tool")
    lam.add_argument("--board-width", help="mm")
    lam.add_argument("--board-height", help="mm")
    lam.add_argument("--turn", help="y/n: parts may turn 90 deg")
    lam.add_argument("--interlock", help="y/n")
    lam.add_argument("--tabs", help="per part")
    d3 = ap.add_argument_group("3d")
    d3.add_argument("--depth", help="relief depth, mm")
    d3.add_argument("--frame", help="mm")
    d3.add_argument("--rough", help="y/n")
    d3.add_argument("--rough-tool")
    d3.add_argument("--finish-tool")
    d3.add_argument("--finish-dir", help="x, y or xy")
    d3.add_argument("--resolution", help="model grid, mm")
    p = ap.add_argument_group("pins")
    p.add_argument("--shape", help="square, round or hex")
    p.add_argument("--size", help="mm")
    p.add_argument("--levels", help="different lengths, 0 = any")
    p.add_argument("--kerf", help="saw cut, mm")
    args = ap.parse_args(argv)
    try:
        return run(args, input_fn)
    except WizardError as e:
        print(f"camcore relief: {e}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\ncamcore relief: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
