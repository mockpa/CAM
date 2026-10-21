# camcore user manual

camcore turns a description of a part (a **job**) into a LinuxCNC program for the PrintNC, and
proves on the way that the program is safe to run. This manual walks through the work in the order
you do it; the [job file reference](#9-job-file-reference) at the end lists every key.

Contents
1. [How camcore works](#1-how-camcore-works)
2. [Setup](#2-setup)
3. [A first job, start to finish](#3-a-first-job-start-to-finish)
4. [The desktop app](#4-the-desktop-app)
5. [Your own drawings and models](#5-your-own-drawings-and-models)
6. [Rotary work](#6-rotary-work)
7. [Checking, previewing and the simulator](#7-checking-previewing-and-the-simulator)
8. [Taking a program to the machine](#8-taking-a-program-to-the-machine)
9. [Job file reference](#9-job-file-reference)
10. [Tool library reference](#10-tool-library-reference)
11. [Machine profile reference](#11-machine-profile-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. How camcore works

```
job.json ──> toolpaths ──> G-code ──> camcore-verify ──> LinuxCNC sim ──> NAME.ngc
 (what)       (ops)        (post)     (crashes, limits,   (real LinuxCNC     (only if
                                       machine body)       interpreter)       all passed)
```

- A **job** is a JSON file: the stock, the shapes or 3D models, the tool library, and an ordered
  list of **operations** (pocket, profile, V-carve, drill, 3D and rotary ops). The desktop app is an
  editor for this file; you can also write it by hand.
- **`camcore check`** is the gate. It generates the G-code, replays it on a model of the machine and
  the stock (`camcore-verify`), runs it through real LinuxCNC in a container, writes a 3D viewer,
  and only then writes `NAME.ngc`. If anything fails you get a report and no program.
- **Only take programs from the output folder of `camcore check`** (or the app's Export). Anything
  else has not been checked.

Units are millimetres and degrees everywhere.

## 2. Setup

Once, in the `camcore` folder:

```bash
conda env create -p .conda -f environment.yml
conda activate ./.conda
pip install -e .[dev]                 # includes the desktop app
cd sim && docker build -t camcore-sim . && cd ..     # the LinuxCNC simulator
```

Every new terminal:

```bash
cd ~/cam_software/camcore
unset PYTHONPATH                      # the ROS setup in your shell breaks camcore's Python otherwise
conda activate ./.conda
```

Check it works: `pytest -q` (about 6 minutes, all green).

## 3. A first job, start to finish

The sign example (`examples/sign_job.json`) uses most of the 2.5D tools: a pocket, a V-carved
drawing with a flat bottom, four drilled holes and an outside profile that cuts the panel free.

### 3.1 Read the job

```json
{
  "post": "linuxcnc",
  "tool_library": "tools.json",
  "stock": {"type": "box", "x": [0, 160], "y": [0, 90], "z": [-18, 0]},
  "defaults": {"safe_z": 5.0, "ramp_angle": 3.0},
  "shapes": {
    "panel":  {"type": "rect", "x": 0, "y": 0, "w": 160, "h": 90},
    "recess": {"type": "rect", "x": 20, "y": 10, "w": 120, "h": 70},
    "art":    {"type": "svg", "file": "sign.svg", "fit_origin": [30, 18]}
  },
  "operations": [
    {"op": "pocket",  "shape": "recess", "tool": "em3", "depth": 2, "step_down": 1, "stepover_ratio": 0.45},
    {"op": "vcarve",  "shape": "art", "tool": "v60", "top_z": -2, "max_depth": 3, "clear_tool": "em3"},
    {"op": "drill",   "tool": "em3", "points": [[8, 8], [152, 8], [152, 82], [8, 82]], "depth": 6, "peck": 2},
    {"op": "profile", "shape": "panel", "tool": "em3", "side": "outside", "depth": 6, "step_down": 2}
  ]
}
```
(shortened; the file also sets feeds)

- **Coordinates**: X0 Y0 is the front-left corner of the stock, Z0 its top. Everything below the top
  is negative. This is where you will set G54 on the machine.
- **`stock`** is required: camcore-verify needs it to find crashes.
- **`tool: "em3"`** names a tool in `tools.json` (diameter, ATC pocket number, feeds).
- **`top_z: -2`** starts the V-carve on the floor of the recess the pocket cut.

### 3.2 Check it

```bash
camcore check examples/sign_job.json -o out/sign --no-sim
```

```
verify: 3249 moves, 0 errors, 0 warnings
LinuxCNC sim: SKIPPED (--no-sim). Do not run this program on the machine.
wrote out/sign/sign_scene.json
wrote out/sign/sign.ngc
wrote out/sign/sign_view.html
PASSED WITHOUT SIM
```

`--no-sim` takes seconds and is what you use while working. Drop it for the final check (a few
minutes); only then does the report say `PASSED`.

### 3.3 Look at it

Open `out/sign/sign_view.html` in a browser: **Play** runs the program on the stock with material
removal, **Result** jumps to the end, the slider scrubs, **Speed** sets the pace. Drag to rotate,
scroll to zoom, right-drag or Shift-drag to pan. Problems found by the check are listed with their G-code lines.

### 3.4 Change something

Edit the job (or use the app, next section) and check again. For example, make the pocket deeper:
`"depth": 3`. If you also make the V-carve start at the new floor (`"top_z": -3`) the drawing is cut
into the deeper recess.

### 3.5 Final check and the program

```bash
camcore check examples/sign_job.json -o out/sign
```

When it says `PASSED`, `out/sign/sign.ngc` is the program for the machine (section 8).

## 4. The desktop app

```bash
camcore gui examples/sign_job.json    # or just: camcore gui   (new job)
```

The app edits the job file; it does no CAM of its own, so everything in this manual applies to it.
The window has four tabs on the left for editing and two on the right for results.

| Where | What you do there |
|---|---|
| **File** menu | New, Open…, Save, Save As… (relative paths to drawings and the tool library are adjusted when you save elsewhere) |
| **Operations** tab | the cutting order (**Add**, **Up**/**Down**, **Duplicate**, **Remove**) and the form of the selected operation: everything it takes. An empty field is inherited: its grey text shows the value it will get and from where (the tool's data for the material, the job's defaults, or the built-in default). Problems that would stop the job are listed in red. |
| **Shapes & models** tab | add Rectangle, Circle, **Import drawing** (SVG/DXF), **STL model…**; each is editable as JSON; renaming follows into the operations |
| **Job** tab | post, material, tool library file, machine, tool order, stock, defaults, wrapped rotary, indexing |
| **Tool library** tab | the library as a table: number (= ATC pocket), diameter, kind, flutes, centre cutting, feeds and speeds per material; **Save library** |
| **Viewer** / **Report** tabs (right) | the program playing on the stock; the check's log with notes, warnings and errors |
| **Run → Preview** (Ctrl+R) | saves and runs the check without the sim into a scratch folder, then shows it in Viewer and Report. A look, never a program. |
| **Run → Export** (Ctrl+E) | saves and runs the full check with the sim into the output folder (default `out/` next to the job). The `.ngc` appears only if everything passed. |
| **Tools → Rotary wizard…** | the questions of `camcore rotary-wizard` as a dialog (section 6) |
| **Tools → Relief…** | lamellas, a 3D relief or pins from an image or STL: the questions of `camcore relief` with a shaded preview; checks every job it writes (section 5.6) |

Typical loop: change a value, Ctrl+R, look, repeat; Export once at the end.

## 5. Your own drawings and models

### 5.1 SVG and DXF

```json
"shapes": {
  "body":   {"type": "dxf", "file": "strat.dxf", "layers": ["OUTLINE"], "fit_origin": [10, 10]},
  "cavity": {"type": "dxf", "file": "strat.dxf", "layers": ["CAVITY"],  "fit_origin": [10, 10]},
  "logo":   {"type": "svg", "file": "logo.svg", "fit_origin": [40, 30]}
}
```

- **Closed contours only.** Nested contours become holes (a ring, a letter O). DXF pieces are joined
  end to end within `join_tolerance` (0.01 mm); a contour that still does not close is an error that
  says where. `"ignore_open": true` skips open pieces instead.
- **DXF**: LINE, ARC, CIRCLE, (LW)POLYLINE with bulges, SPLINE, ELLIPSE, blocks; units from the
  file's `$INSUNITS`. Use **layers** to separate what gets profiled from what gets pocketed. Text,
  dimensions and hatches are ignored: convert text to curves.
- **SVG**: paths, basic shapes, transforms. A `width`/`height` in mm (or cm, in, pt) sets the scale,
  otherwise 1 unit = 1 mm; `"scale"` multiplies.
- **`fit_origin`**: moves the drawing's lower-left corner to that point. Give shapes from the same
  drawing the same `fit_origin` only if they share their lower-left corner; otherwise place each.
- Combine shapes: `{"type": "union", "shapes": [...]}`, `{"type": "difference", "a": ..., "b": ...}`.
  Mirror one: `{"type": "mirror", "shape": {...}, "axis": "y"}` (left-right; `"x"`: top-bottom), its
  lower-left corner stays put.

### 5.2 Which operation for what

| You want | Operation | Key settings |
|---|---|---|
| Cut a part out | `profile`, `side: "outside"` | `depth` a bit past the stock bottom (e.g. 6.3 for 6 mm), `tabs` to hold it, `lead_radius` for a clean start |
| A slot along a line or an inside cutout | `profile`, `side: "on"` / `"inside"` | |
| Clear an area to depth | `pocket` | `stepover_ratio` 0.4; `method: "adaptive"` with 0.1–0.25 for hard materials and deep pockets |
| Lettering, decoration | `vcarve` with a V-bit | `method: "medial"` for sharp corners and fewer moves; `max_depth` + `clear_tool` for a flat bottom |
| An inlay (one wood set into another) | `inlay`, one job per board | section 5.5 |
| Holes | `drill` (centre-cutting tool) | `points`, `depth`, `peck` |
| 3D relief from an STL | `surface_rough`, then `surface_finish` | section 5.3 |

Things every 2.5D operation shares: `depth` (positive, below `top_z`), `step_down` (per pass),
`top_z` (where the material starts, default 0), `safe_z` (rapid height), `feed`, `plunge`, `climb`
(default true), `entry` (`"ramp"`, `"helix"` for pockets, or `"plunge"` for centre-cutting tools
only) and `ramp_angle` (degrees, default 3). Nothing plunges straight down unless you ask for it
and the tool allows it.

**Holding tabs** (profiles): `"tabs": {"count": 4, "width": 6, "height": 1.5}` or `"spacing": 80`
instead of `count`. `height` is the material left at the bottom. Where a contour is too short or
fully milled out, camcore adjusts and says so in a `note:`. To choose where the tabs go, give points
on the part's edge instead: `"at": [[15, 0], [45, 0]]`. Each tab goes on the nearest contour; a
point on no contour, or tabs that overlap, are errors.

**Adaptive clearing** (`"method": "adaptive"` on a pocket): the tool keeps the same width of cut
everywhere instead of slotting on the first loop and overloading in corners, so you can use the
full flute length as `step_down` with a small stepover. It enters each region by a helix and finishes
with a pass along the walls.

**Order**: operations run as written. `"id": "rough"` on one and `"after": ["rough"]` on another
makes the second wait; `"tool_order": "minimize_changes"` on the job saves tool changes without
breaking an `after`.

### 5.3 3D reliefs from STL (3 axes)

```json
"models": {"relief": {"file": "relief.stl", "xy_start": [5, 5], "z_top": 0}},
"operations": [
  {"op": "surface_rough",  "model": "relief", "tool": "em6",  "step_down": 2, "stock_to_leave": 0.5},
  {"op": "surface_finish", "model": "relief", "tool": "ball3", "strategy": "raster", "stepover": 0.3}
]
```

- `xy_start` puts the model's lowest X/Y at that point, `z_top` its highest point at that Z (0 = the
  stock top).
- `surface_finish` `"strategy": "raster"` for shallow areas, `"waterline"` for steep walls; use both,
  each limited with `"boundary": [x0, y0, x1, y1]`.
- Use closed (watertight) STL files. See `examples/relief_job.json`.

### 5.4 Feeds and speeds

Put feeds per material in the tool library (section 10) and set `"material": "hardwood"` on the
job: every operation then takes its `feed`, `plunge`, `step_down`, `stepover_ratio`, `ramp_angle`
and `rpm` from the tool. A value written on the operation wins; the job's `defaults` fill what is
left. A tool used in the job without data for the material is an error. The values in
`examples/tools.json` are cautious starting points for 2-flute carbide.

### 5.5 Inlays

An inlay takes two jobs with the same drawing, the same V-bit and the same numbers. The pocket job
cuts a pocket in the **base board** (the background), and the plug job cuts the mirrored design
standing up from the **insert board** (the wood that shows in the pocket):

```json
{"op": "inlay", "part": "pocket", "shape": "art", "tool": "v60", "clear_tool": "em3",
 "pocket_depth": 5, "glue_gap": 0.5, "surface_gap": 1.5}
{"op": "inlay", "part": "plug",   "shape": "art", "tool": "v60", "clear_tool": "em3",
 "pocket_depth": 5, "glue_gap": 0.5, "surface_gap": 1.5, "fit_with": "inlay_pocket_job.json"}
```

Give both the same, unmirrored `shape`: the plug job mirrors it. The plug is flipped face down into
the pocket. Its walls meet the pocket's, its face sits `glue_gap` above the pocket floor, and its
flange sits `surface_gap` above the base surface. Clamp pressure then goes into the walls, not the
floor. The V-bit must cut `pocket_depth - glue_gap + surface_gap` deep at its full diameter, or the job is refused.

- **Plug board**: by default the flange is cleared over the whole board (`clear_tool` runs off its
  edges), so nothing needs trimming. With `"plug_margin": 10` only 10 mm around the design is cleared.
  Trim the board inside that flange before gluing (the report says so).
- **Corners**: the default `"method": "medial"` (centre-cutting V-bit, plunges) gives sharp corners on
  both parts. `"offset"` rounds sharp corners off, which keeps the plug from seating.
- **The fit check**: with `"fit_with"` on the plug, `camcore check` simulates both programs, turns the
  plug over onto the pocket, lowers it until it touches and measures. It fails unless the bottom and
  flange gaps are within 0.1 mm of `glue_gap` and `surface_gap` and the walls meet. `camcore inlay-fit
  POCKET_job.json PLUG_job.json` prints the same numbers by itself.
- **Glue-up**: spread glue in the pocket and on the plug walls, press the plug in, and clamp
  across the whole plug with a caul. Once dry, plane or sand the back of the insert board down to the
  base surface.
- **Try it in MDF first**: wood moves, bits wear, and a real 60° bit is rarely exactly 60°. A test inlay
  shows whether you need a slightly bigger `glue_gap`.

### 5.6 Relief designs: lamellas, 3D reliefs and pins

A picture (bright = high; 8 or 16 bit) or an STL becomes wall art in one of three ways:

| Design | What you get | Machining |
|---|---|---|
| **Lamellas** | the relief cut into strips that stand on edge side by side on a back board; each strip's top edge follows the relief | one job per board: every strip cut out with a `profile`, tabs on its bottom (glued) edge |
| **3D relief** | the relief milled as a surface out of a block or glued-up board, with an optional frame left at full height | one job: `surface_rough` with an end mill, then `surface_finish` with a ball nose, with a tool change between |
| **Pins** | a grid of square, round or hex pins cut to a few lengths | none: a cut list, a position list and a pin plan |

```bash
camcore relief photo.png                       # asks everything, suggests an answer for each
camcore relief photo.png --yes --mode 3d --width 300 --depth 15 --no-sim   # take the suggestions
camcore relief model.stl --view y              # an STL seen from the front
```

The questions: the design, how to look at an STL, whether to adjust the picture (turn, mirror, crop
or stretch, smoothing in mm, black and white point, gamma, invert: skipped unless you say yes),
the artwork size, the material, then the design's own (strip thickness = board thickness, heights,
board size, tool, interlocking and tabs; depth, frame, stock and tools for 3D; pin shape, size and
number of lengths). Every question has a flag (`camcore relief --help`). In the app it is
**Tools → Relief…**, with a shaded preview that follows your answers.

The output folder gets `NAME_height.png` (the prepared relief, white = high, to check which way up it
is), then:
- **Lamellas**: `NAME_sheetN_job.json` and, if its check passes, `NAME_sheetN.ngc` per board;
  `NAME_parts.csv` (every strip: length, heights, where it goes, which board, where on it, turned or
  not); `NAME_assembly.svg` (the artwork from the front, every strip labelled); `NAME_boardN.svg` and
  `.dxf` (the parts on the board with labels and tabs). A red dot marks each strip's start on both plans,
  so you know which way round it goes.
- **3D relief**: `NAME_job.json`, `NAME.stl` (the block, X0 Y0 at its front left, Z0 on the stock top)
  and `NAME.ngc` if the check passes.
- **Pins**: `NAME_cut_list.csv` (per length: count and bar length with the saw cuts),
  `NAME_positions.csv` (A1, A2, ...: row A at the back) and `NAME_pin_plan.svg` / `.dxf`.

Every job is checked like any other (with the sim unless `--no-sim`), and they are ordinary job files:
open them in the app, change an operation and check again.

Things camcore does differently from the original Reliefwerk, on purpose:
- **Parts sit further apart.** Strip outlines stay 2 x tool diameter + 2 mm apart and the tool diameter
  + 2 mm inside the board edge. Closer, the cuts of two neighbours meet at the peaks of a wavy edge and
  the scrap in the valleys between them comes loose. Interlocking (`--interlock y`) pairs strips head
  to tail where that saves board, at the same spacing.
- **Boards are checked against the machine** before any toolpath: outside the travel, or on top of the
  ATC rack, is refused with the reason.
- **No finishing into fresh stock.** A 3D relief without roughing is refused unless it is no deeper than
  the ball's normal step down, and so is a roughing tool with flutes shorter than the depth.
- **Tabs stay on the bottom edge** (the glued one): sand them flush before glue-up.

Large 3D reliefs take time: about 35 minutes for 300 x 200 mm at the default 0.5 mm grid, so try a
coarser `--resolution` first.

## 6. Rotary work

Three kinds, all on the A axis (it turns around X; Z0 is on the axis centreline):

| Kind | What it does | How |
|---|---|---|
| **Wrapped** | a flat 2.5D job wrapped around a cylinder (Y becomes A) | `"rotary": {"diameter": 40}` on the job; `examples/wrap_job.json` |
| **Indexed 3+1** | ordinary 2.5D ops on faces at fixed A angles | `"index": {"sides": 4}` (or `90`, `[0, 180]`) on the job or an operation; `examples/indexed_job.json` |
| **Continuous from STL** | turned and carved shapes (chess pieces, necks, handles) | `rotary_rough` + `rotary_finish` on a model, or the wizard; `examples/pawn_job.json` |

### 6.1 The stock in the chuck

Rotary stock is a cylinder or a bar along X, and it says how much of it is in the jaws:

```json
"stock": {"type": "cylinder", "diameter": 30, "x": [-45, 50], "chuck_hold": 10}
```

The chuck is on the low-X side; its jaw face sits at `x[0] + chuck_hold` (here X -35). On the
PrintNC profile this also places the job: the rotary job's G54 is Y0 Z0 on the axis and X from the
jaw face position. Leave enough stock hanging out: the check knows the size of the chuck, the
collet nut and the Z plate, and refuses programs where they would meet. On the provisional profile
the spindle needs about 30 mm between the jaw face and the closest tool position.

### 6.2 The rotary wizard

```bash
camcore rotary-wizard my_part.stl
```

It asks, with a suggested answer for each (Enter takes it):

1. which model axis goes on the rotary axis, end for end, rotation about it
2. material, roughing tool, finishing tool
3. stock diameter, length held in the chuck, stock length (suggested long enough to keep the
   spindle off the jaws)
4. the smallest radius to leave (a support stub at the free end)
5. accuracy (`coarse` / `normal` / `fine`) and finishing strategy (`helical` is smoothest)

It writes `out/NAME_job.json`, checks it and opens the viewer. Every question has a flag, and `--yes`
takes the suggestions for the rest:

```bash
camcore rotary-wizard my_part.stl --yes --diameter 40 --preset fine --no-sim
```

The result is an ordinary job: open it in the app or edit it, and check again. In the app the same
wizard is **Tools → Rotary wizard…**.

## 7. Checking, previewing and the simulator

### 7.1 What `camcore check` finds

Errors (no program is written):
- rapid moves into material, cutting with the spindle stopped, cutting with no tool
- moves outside the machine's travel
- plunging into material with a tool that is not centre-cutting
- tool numbers without an ATC pocket or missing from the controller's tool table; starting the
  spindle with the touch probe loaded
- **machine body collisions**: the collet nut, spindle, Z plate or the tool against the ATC rack,
  tool setter, spoilboard or (rotary jobs) the chuck and headstock; the spindle or the gantry beam
  against the stock as it is cut so far
- Z coming down after a tool change before X and Y are set
- G-code the controller would reject (bad arcs, G93 without F, ...) and anything LinuxCNC itself
  refuses in the sim

Warnings (the program is still written, read them):
- cutting deeper than the flute length, missing `G43`, a tool without a `gauge_length`

Notes (`note:` lines) say where an operation adapted itself, e.g. fewer tabs than asked for.

### 7.2 Output folder

| File | What |
|---|---|
| `NAME.ngc` | the program (only if everything passed) |
| `NAME_report.txt` | what was checked and found; always written |
| `NAME_view.html` | 3D viewer: program, stock removal, issues, run time |
| `NAME_scene.json` | the machine model and sim setup for the simulator GUI |

### 7.3 The simulator GUI

Real LinuxCNC (AXIS) in your browser, with a 3D model of the machine next to it:

```bash
docker run --rm -p 6080:6080 -v "$PWD/out/sign:/work" camcore-sim gui /work/sign.ngc
```

Open http://localhost:6080/vnc.html, click **Connect**, then in AXIS: **F1** (E-stop off), **F2**
(machine on), **Home All**, **Run**. The model on the right shows the spoilboard, ATC rack, gantry,
spindle, tool, the rotary unit on rotary jobs, and the stock turning with A. It runs the same INI,
G54 and tool table the check used. Stop with Ctrl+C. Start the container with the program whose
setup you want: the G54 of a rotary job differs from a flat one.

If the port is taken (`port is already allocated`), an older GUI container is still running:
`docker ps`, then `docker stop <name>`.

## 8. Taking a program to the machine

1. **Copy** `out/NAME/NAME.ngc` to the controller, e.g. into `~/linuxcnc/nc_files` (USB stick or
   `scp out/NAME/NAME.ngc <user>@<pi>:~/linuxcnc/nc_files/`).
2. **Open** it in AXIS: File → Open (Ctrl+O). Check the preview sits where you expect.
3. **Set up**: home, load the stock, set G54 at the job's origin. Flat jobs: X0 Y0 at the stock's
   front-left corner, Z0 on its top. Rotary jobs: Y0 Z0 on the rotary axis, X as the check assumed
   (the report's machine profile, from the jaw face and `chuck_hold`). The check only means
   something if G54 is where it assumed.
4. **Tools**: the program calls `Tn M6` with n = ATC pocket. The rack must hold those tools, and their
   gauge lengths should match the library's `gauge_length`.
5. **Prove every new kind of program**:
   1. air cut: G54 Z 50 mm above the real stock, run it all, watch tool changes and A moves
   2. single block through the first tool change and the first entry, feed override 25 %
   3. soft material (MDF, foam) before the real stock
   4. hand on the E-stop the first time each new operation type runs

## 9. Job file reference

Top level:

| Key | Meaning |
|---|---|
| `post` | `"linuxcnc"` (default) or `"grbl"` (no A axis, M0 pause at tool changes) |
| `post_options` | `{"arcs": false}` for G1 only, `{"arc_tolerance": 0.005}` |
| `machine` | machine JSON, relative to the job (default `machine/printnc_provisional.json`) |
| `tool_library` | tool library file, relative to the job |
| `tools` | extra tools defined in the job, same format as the library |
| `material` | picks each tool's feeds and speeds |
| `stock` | **required**. `{"type": "box", "x": [..], "y": [..], "z": [bottom, top]}`, `{"type": "cylinder", "diameter": D, "x": [..], "chuck_hold": h}` or `{"type": "bar", "x": [..], "y": [..], "z": [..], "chuck_hold": h}` (bar: cross-section at A = 0, around the axis) |
| `defaults` | parameter values for every operation that takes them (e.g. `safe_z`) |
| `shapes` | named 2D shapes (below) |
| `models` | named STL models (below) |
| `operations` | the ordered list (below) |
| `tool_order` | `"minimize_changes"` |
| `rotary` | `{"diameter": D}`: wrap the 2.5D operations around the A axis |
| `index`, `index_clearance` | indexed 3+1 for all operations; retract margin above the stock when turning A (default 5) |

Shapes:

| `type` | Keys |
|---|---|
| `rect` | `x`, `y` (lower-left), `w`, `h` |
| `circle` | `cx`, `cy`, `r` |
| `polygon` | `points` `[[x, y], ...]`, optional `holes` |
| `svg` | `file`, `scale` |
| `dxf` | `file`, `layers`, `scale`, `join_tolerance` (0.01), `ignore_open` (false) |
| `union` | `shapes` `[...]` |
| `difference` | `a`, `b` |
| `mirror` | `shape` `{...}`, `axis` (`"y"`: left-right, `"x"`: top-bottom); lower-left corner kept |
| any | `fit_origin` `[x, y]`: lower-left corner moved there |

Models: `{"file": "x.stl"}` plus placement, applied in this order: `along` (model axis put on X,
default `"x"`), `flip`, `rotate_x` (degrees), `center_yz`, `x_start`, `translate` `[x, y, z]`; for 3-axis
jobs also `xy_start` `[x, y]` and `z_top`.

Every operation: `op`, `tool`, and `shape` / `model` / `points`; optional `id`, `after` `[ids]`,
`rpm` (default 18000), `index` and `face_z` (indexed), `name`. Parameters (defaults in brackets):

**profile**: `depth`, `step_down`, `side` (`"outside"`; `"inside"`, `"on"`), `feed` (1000),
`plunge` (300), `safe_z` (5), `climb` (true), `top_z` (0), `entry` (`"ramp"`), `ramp_angle` (3),
`tabs` (`{"count" | "spacing" | "at", "width", "height", "ramp_angle"}`), `lead_radius`

**pocket**: `depth`, `step_down`, `stepover_ratio` (0.4), `method` (`"offset"`; `"adaptive"`),
`feed` (1000), `plunge` (300), `safe_z` (5), `climb` (true), `top_z` (0), `entry` (`"helix"`; `"ramp"`,
`"plunge"`), `ramp_angle` (3), `helix_diameter` (0.8 × tool diameter)

**vcarve**: `method` (`"offset"`; `"medial"`), `step` (0.1, offset method), `tolerance` (0.01, medial),
`max_depth`, `start_depth` (0: the groove starts this far below `top_z` at the edge), `clear_tool` + `clear` (`{pocket parameters}`, flat bottom), `feed` (800), `plunge` (300),
`safe_z` (5), `top_z` (0), `climb` (true), `entry` (`"ramp"`; medial needs `"plunge"`), `ramp_angle` (3)

**inlay**: `pocket_depth`, `part` (`"pocket"`; `"plug"`), `glue_gap` (0.5), `surface_gap` (1.5),
`clear_tool` (required) + `clear` (`{pocket parameters}`), `plug_margin` (none: the whole plug board),
`fit_with` (plug: the pocket job, for the fit check), `method` (`"medial"`; `"offset"`), `entry`
(`"plunge"`), `step` (0.1, offset), `tolerance` (0.01, medial), `feed` (800), `plunge` (300), `safe_z` (5),
`top_z` (0), `climb` (true), `ramp_angle` (3)

**drill**: `points`, `depth`, `peck` (none: one pass), `plunge` (200), `safe_z` (5), `retract_z` (1),
`top_z` (0)

**surface_rough**: `step_down`, `stock_to_leave` (0.5), `stepover_ratio` (0.4), `feed` (1500),
`plunge` (300), `safe_z` (5), `floor`, `boundary` `[x0, y0, x1, y1]`, `sampling` (0.2), `entry`
(`"helix"`), `ramp_angle` (3), `helix_diameter`, `climb` (true)

**surface_finish**: `strategy` (`"raster"`; `"waterline"`), `stepover` or `stepover_ratio` (0.15),
`angle` (0, raster), `step_down` (waterline), `z_range`, `feed` (1500), `plunge` (300), `safe_z` (5),
`floor`, `boundary`, `sampling` (0.2), `tolerance` (0.005), `entry_clearance` (1), `ramp_angle` (10),
`max_descent` (30)

**rotary_rough**: `step_down`, `stepover_ratio` (0.5) or `stepover`, `stock_to_leave` (0.5), `feed`
(1500), `plunge` (300), `safe_z` (5), `x_range`, `floor` (1), `resolution` (0.5), `a_step`, `tolerance`
(0.02), `max_descent` (30)

**rotary_finish**: `strategy` (`"helical"`; `"a_raster"`, `"x_raster"`), `stepover_ratio` (0.15) or
`stepover`, `stepover_deg` (x_raster), `feed` (1500), `plunge` (300), `safe_z` (5), `x_range`, `floor`
(1), `resolution` (0.25), `a_step`, `tolerance` (0.005), `entry_clearance` (1), `max_descent` (30)

The app's operation form lists exactly these, with the inherited values filled in grey.

## 10. Tool library reference

```json
"em6": {"number": 2, "diameter": 6.0, "gauge_length": 45, "kind": "flat", "flute_length": 22,
        "center_cutting": false,
        "cutting": {"hardwood": {"rpm": 16000, "feed": 2500, "plunge": 600, "step_down": 3.0,
                                 "stepover_ratio": 0.45, "ramp_angle": 5}}}
```

| Key | Meaning |
|---|---|
| `number` | ATC pocket = LinuxCNC tool number (1–6 on the PrintNC; the probe is 99) |
| `diameter` | mm |
| `kind` | `flat`, `ball`, `bull` (with `corner_radius`), `vbit` (with `v_angle`, included angle) |
| `flute_length` | deeper cuts are warned about |
| `center_cutting` | may plunge straight down (default false) |
| `gauge_length` | spindle nose to tip, for the machine body check (default: the machine's `default_tool_length`, with a warning) |
| `cutting` | per material: `rpm`, `feed`, `plunge`, `step_down`, `stepover_ratio`, `ramp_angle` |

`length` is something else: it becomes the Z offset in the generated `tool.tbl` for the sim; leave it
out (the PrintNC's M6 remap measures tool length).

## 11. Machine profile reference

`machine/printnc_provisional.json` describes the machine for the check; `machine/README.md` lists
where each number comes from. **All of it is provisional until the machine stands.**

| Key | Meaning |
|---|---|
| `travel` | machine limits per axis (tool-tip machine coordinates) |
| `work_offset` | G54 the check assumes for flat jobs (and the sim uses) |
| `rapid_rate` | mm/min, for time estimates |
| `has_a`, `a_max_diameter` | rotary axis; largest stock the wizard allows |
| `atc_pockets`, `probe_tool`, `tool_table` | valid tool numbers; the controller's real `tool.tbl` once it exists |
| `sim` | `{"ini": "printnc_sim.ini"}` for the simulator |
| `body` | the machine body: spindle parts, gantry, fixed obstacles, rotary unit (format in `camcore/body.py`) |

Once the machine runs: put in the real travel, the real `tool.tbl`, the measured body, and each
tool's `gauge_length`.

## 12. Troubleshooting

| Message | What to do |
|---|---|
| `COLLISION: collet nut hits chuck ...` | more stock out of the jaws (lower `x[0]` or `chuck_hold`), a longer tool (`gauge_length`), or keep the cut further from the chuck |
| `COLLISION: ... hits the stock` | the spindle comes down beside a deep cut: use a longer tool or cut less deep |
| `rotary stock has no "chuck_hold"` | add it to the stock (mm held in the jaws) |
| `CRASH: rapid move into material` | raise `safe_z`, or check `top_z` of an op that starts below the stock top |
| `... is outside machine travel` | the part does not fit where G54 puts it; on rotary jobs the swing may be too large |
| `T.. plunges into material but is not marked center_cutting` | use `entry: "ramp"`/`"helix"`, or mark the tool if it really is centre-cutting |
| `tool ... has no cutting data for material ...` | add that material under the tool's `cutting`, or remove `material` from the job |
| `contour ... does not close` (DXF) | fix the gap in CAD, raise `join_tolerance`, or `ignore_open` |
| `port is already allocated` (sim GUI) | `docker ps`, `docker stop <name>` |
| Python import errors, numpy mismatch | `unset PYTHONPATH` (ROS) and `conda activate ./.conda` |
