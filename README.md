# camcore

Python core for a CAM application ([the plan](docs/PLAN.md)): 2.5D, V-carve, 3-axis 3D and 4-axis (wrapped, indexed and continuous rotary) toolpaths with LinuxCNC (and GRBL) output.

**New here? Read the [user manual](docs/MANUAL.md)**: a first job start to finish, the desktop app, your own
drawings, rotary work, the simulator, taking a program to the machine, and a reference of every job key.
This README is the feature list.
Step-by-step walkthroughs with screenshots: [tutorials](docs/tutorials/README.md).

## What works today
- **Profile**: inside / outside / on-line, multiple step-downs, climb or conventional. Each contour is cut
  through all depths without retracting.
- **Lead-in/lead-out arcs** (profiles, `"lead_radius": 3`): a circle in the waste, tangent to the contour start.
  Each depth level descends on it (no steeper than `ramp_angle`), arcs onto the contour, cuts the full loop and
  arcs off. Where the circle would touch the part it shrinks; if it still does not fit, that contour gets none.
  Not for `"side": "on"`.
- **Climb / conventional**: climb (the default) keeps the wall on the right of the tool with an M3 spindle:
  outside profiles run clockwise, pockets, inside profiles and V-carve counter-clockwise along the outer wall.
- **Holding tabs** (profiles): `"tabs": {"count": 4, "width": 6, "height": 1.5}` (or `"spacing"` in mm instead of
  `"count"`, optional `"ramp_angle"`, default 30 deg). `width` is the tab at the part edge, `height` the material
  left above the bottom of the cut. Only passes below the tab top are affected: the tool rises over each tab and
  ramps back down after it, so tabs are trapezoids. See `examples/cutout_job.json`. A contour too short for the
  requested tabs gets as many as fit; one whose inside waste is milled away (slug under 1 mm) gets none. Each
  such adjustment is listed as a `note:` by `camcore` and in the `camcore check` report.
- **Pocket**: contour-parallel clearing with islands, step-downs, stepover as a fraction of tool diameter.
  `"method": "adaptive"` keeps a constant width of cut (`stepover_ratio` 0.1 to 0.25, `step_down` up to the
  flute length): the tool steers through a model of the material left, so it never slots or overloads in
  corners; each region is entered by a helix, then a ramped wall pass finishes the walls. Regions too narrow
  for a helix fall back to offset loops (with a note). See `examples/adaptive_job.json`.
- **V-carve**: depth follows the distance to the edge; optional `max_depth` + `clear_tool` for flat-bottom carving (an endmill clears the floor first).
  `"method": "offset"` (default) stacks inset contours every `step`; `"method": "medial"` runs the tip once along the
  medial axis (Voronoi of the boundary) at the depth where the cone touches both walls: sharp corners, a fraction of the
  moves and time (the sign's V pass: 59 lines instead of 446, 0.5 instead of 5 min) and a closer groove (`tolerance`, default
  0.01 mm of wall material). It descends along the V walls and plunges at the deepest points, so it needs
  `"entry": "plunge"` and a centre-cutting V-bit. Example: `examples/sign_medial_job.json`.
- **Inlays** (`"op": "inlay"`): a V-carved pocket in the base board and a mirrored plug in the insert board, one
  job each (`"part": "pocket"` / `"plug"`), same V-bit and the same `pocket_depth`, `glue_gap` (plug face above the
  pocket floor) and `surface_gap` (plug flange above the base surface). The plug is the design mirrored and V-carved
  from `pocket_depth - glue_gap` down, so its walls are the pocket's; `clear_tool` clears the pocket floor and the
  plug's flange (the whole plug board, or `plug_margin` around the design). Medial by default, with plunges into
  notches too sharp for any carving path. Settings that cannot fit are refused. `"fit_with": "pocket_job.json"` on
  the plug makes `camcore check` flip the simulated plug into the simulated pocket and fail unless both gaps are
  within 0.1 mm and the walls meet; `camcore inlay-fit POCKET PLUG` runs that alone. See
  `examples/inlay_pocket_job.json` / `inlay_plug_job.json`.
- **Shapes** can be mirrored: `{"type": "mirror", "shape": {...}, "axis": "y"}` (or `"x"`), lower-left corner kept.
- **Drill**: peck drilling (centre-cutting tools only).
- **Entry moves**: nothing plunges straight down by default. Pockets enter on a helix (falling back to a ramp
  along the loop where it does not fit) and link from loop to loop at depth; profiles and V-carve ramp along the
  contour. Per operation: `"entry": "helix" | "ramp" | "plunge"`, `"ramp_angle"` (max degrees, default 3, at most
  30), `"helix_diameter"` (pockets, default 0.8 x tool diameter). `"plunge"` is refused unless the tool is
  marked `"center_cutting": true` in the library. `camcore-verify` independently reports any feed into material
  steeper than 45 deg with a tool that is not centre-cutting.
- **Rotary (wrapped)**: `"rotary": {"diameter": 40}` wraps any job around the A axis (Y becomes A, Z0 on the axis centreline), with G93 inverse-time feeds.
- **Indexed 3+1**: `"index": {"sides": 4}` on the job (or on one operation) turns A to fixed angles and runs
  any 2.5D or V-carve operation (pocket, profile, drill, V-carve with its clearing pass) on each face. Other forms:
  `"index": 90`, `"index": [0, 180]`, `{"angles": [...]}`, `{"sides": 6, "start": 30}`; an operation's own
  `"index"` overrides the job's. Needs a rotary stock: `{"type": "cylinder", "diameter": 40, "x": [..]}` or a bar
  `{"type": "bar", "x": [0, 120], "y": [-20, 20], "z": [-20, 20]}` (its cross-section at A = 0, around the axis).
  - Each face is drawn as seen from above once A has turned: X along the axis, Y across (Y0 on the axis), Z0 at
    the top of the face, so `depth`, `top_z` and `safe_z` mean what they do on a flat job. The face top defaults
    to the stock's highest point at that A (cylinder: its radius; bar: the face, or the edge at 45 deg);
    `"face_z": 15` sets it.
  - Before turning A the tool retracts above everything the part can swing through (largest stock radius +
    `"index_clearance"`, default 5 mm), turns A with nothing else moving, and retracts again after the face.
    A takes the short way round.
  - Output stays G94 with G2/G3 arcs; only the `G0 A..` lines are new. With `"tool_order": "minimize_changes"`
    the order also stays on the current face where it can. A job can mix indexed operations with wrapped ones
    (`"rotary"`), but then `"index"` goes on the operations, not the job.
  - Handedness: the face up at A = a is the part's ray (0, -sin a, cos a), as everywhere else in camcore. If
    your rotary turns the other way, features on side faces come out mirrored: check with an air cut.
  See `examples/indexed_job.json`: a 40 x 40 bar with a slot on all four faces, holes on two and a V-carved
  label on top.
- **Tool library / ATC**: `"tool_library": "tools.json"` with tool numbers (= ATC pocket); the LinuxCNC post emits
  `Tn M6`, `G43 Hn` and stages the next tool. Tool length is measured by the machine's M6 remap, not by camcore.
  The machine JSON can set `"atc_pockets": 6` (other numbers are errors), `"probe_tool": 99` (may be loaded, the
  spindle must never start with it) and `"tool_table": "tool.tbl"` (the controller's real table: tool numbers
  missing from it are errors, and the LinuxCNC sim runs with it).
- **Feeds and speeds per material**: a library tool can carry `"cutting": {"aluminium": {"rpm": .., "feed": ..,
  "plunge": .., "step_down": .., "stepover_ratio": .., "ramp_angle": ..}, ...}` and a job sets `"material"`.
  An operation's own values win, then the tool's material data, then the job's `"defaults"`; the last two only
  fill parameters the operation takes. A tool without data for the job's material is an error. The values in
  `examples/tools.json` (aluminium, hardwood, softwood, mdf, acrylic) are conservative starting points for
  2-flute carbide: tune them for your tools.
- **Cutting order**: operations run as written, except that one with `"after": ["id", ...]` waits for those
  (`"id"` names an operation). `"tool_order": "minimize_changes"` stays on the loaded tool while any operation
  for it is ready, without breaking an `after`; a flat-bottom V-carve's clearing pass always precedes its
  V-bit pass.
- **Import**: shapes in the job file (rect, circle, polygon, union, difference), SVG and DXF; nested contours
  become holes (even-odd). `"fit_origin": [x, y]` moves a drawing's lower-left corner there.
  - **SVG** (`{"type": "svg", "file": ..., "scale": 1}`): path (M L H V C S Q T A Z), polygon, polyline, rect,
    circle, ellipse; `transform` on elements and nested groups. A physical `width`/`height` (mm, cm, in, pt, pc)
    with a viewBox sets the scale; otherwise 1 user unit = 1 mm.
  - **DXF** (`{"type": "dxf", "file": ..., "layers": ["CUT"], "join_tolerance": 0.01, "ignore_open": false}`):
    LINE, ARC, CIRCLE, LWPOLYLINE (bulges), POLYLINE, SPLINE, ELLIPSE, block INSERTs; units from `$INSUNITS`.
    Open pieces are joined end to end; a contour that still does not close is an error naming where, unless
    `ignore_open`. Text, dimensions and hatches are ignored. See `examples/bracket_job.json`.
  - Curves are flattened with chords within 0.005 mm, so they come back out as G2/G3 arcs.
- **Rotary from STL** (continuous 4th axis, DeskProto-style): `"models": {"pawn": {"file": "pawn.stl"}}` loads
  a mesh lying on the rotary (X) axis; `along` (model axis put on X), `flip`, `rotate_x`, `center_yz`, `x_start`
  and `translate` place it, in that order. Operations
  take `"model"` instead of `"shape"` and the stock diameter from the job's cylinder stock:
  - `rotary_rough`: Z levels of `step_down` from the stock radius inwards, one helical pass per level over the X
    span that still needs cutting, leaving `stock_to_leave` (flat or ball end mills). A turns the other way on
    each level, so it does not wind up.
  - `rotary_finish`: `strategy` `"helical"` (smoothest), `"a_raster"` (full turns at fixed X) or `"x_raster"`
    (sweeps along X, `stepover_deg` apart).
  - Common: `x_range` (tool centre limits, default the model's X extent), `floor` (never below this radius:
    keeps a support stub at a free end), `resolution` (grid in mm; finer = closer to steep walls), `stepover`
    or `stepover_ratio`. Every pass enters with a one-turn ramp; tools that are not centre-cutting never
    descend steeper than `max_descent` (30 deg).
  - Paths never gouge the sampled model: they follow a max envelope of the grid of no-gouge tip heights. On
    steep flanks this leaves up to about slope x `resolution` of material.
  - Output is X/A/Z in G93 inverse time; one G1 can wind many turns where the profile is straight.
  See `examples/pawn_job.json` (`examples/make_pawn.py` builds the STL): verify and the LinuxCNC sim pass,
  about 6 min on the machine.
  Underneath: `cylmap.radial_map` samples r(x, A), the model's outermost radius at each X and machine angle;
  `cylmap.tool_map` turns it into the lowest no-gouge tip radius for a flat, ball or V-bit cutter. At machine
  angle A the tool looks at the model along (0, -sin A, cos A), the same mapping as the viewer and verify.
- **Rotary wizard**: `camcore rotary-wizard model.stl` asks a few questions and builds the rotary job for you:
  which model axis lies on the rotary axis (default: the longest), end for end, rotation about X, material,
  roughing and finishing tool from the library (default: largest flat end mill, smallest ball), stock diameter
  (default: model + 2 mm per side, at most the machine's `a_max_diameter`), length held in the chuck and stock
  length, the smallest radius to leave, accuracy (`coarse`/`normal`/`fine`) and finishing strategy. It centres
  the model on the axis with its chuck end at X0, writes `out/NAME_job.json`, runs `camcore check` and opens
  the viewer. The chuck is on the low-X side; the stock is made long enough that no tool comes within 2 mm of
  the jaws, and the default floor keeps every cut within the shortest flute length. Every question has a flag
  (`camcore rotary-wizard --help`); `--yes` takes the suggested answer for the rest:
  `camcore rotary-wizard examples/pawn.stl --yes --preset fine --strategy x_raster`.
  The job file is a normal job: edit it and run `camcore check` on it again.
- **3-axis 3D from STL**: `surface_rough` and `surface_finish` on a model in machine coordinates (box stock,
  Z0 on top; a model takes `"xy_start": [x, y]` and `"z_top": z` to place it). Cutter locations come from
  [OpenCAMLib](https://github.com/aewallin/opencamlib) (conda-forge, see `environment.yml`), exact against the
  triangles for flat, ball, bull-nose (`"kind": "bull", "corner_radius": 1`) and V cutters. See
  `examples/relief_job.json` (made by `examples/make_relief.py`).
  - `surface_rough`: Z levels `step_down` apart from the stock top down to the model plus `stock_to_leave`
    (`floor` overrides the lowest level). Each level is the boundary (default: the stock) minus the waterline of
    the cutter grown by the leave, cleared like a pocket (helix or ramp entries, links at depth).
  - `surface_finish` `"strategy": "raster"`: zigzag passes `stepover` apart at `angle` degrees from X, dropped
    onto the model. Where the needed height bends (edges, walls) a pass is split until a straight move is within
    `tolerance` (0.005 mm) of it, and decimation never takes a move under a computed point. Best on shallow
    areas; a tool that is not centre-cutting never descends steeper than `max_descent`.
  - `surface_finish` `"strategy": "waterline"`: climb-milled contours at Z levels `step_down` apart over
    `z_range`, each entered by a ramp along the contour. Best on steep walls; it leaves flat areas alone, so
    pair it with a raster pass (limit both with `"boundary": [x0, y0, x1, y1]`, the tool centre's area).
  - OpenCAMLib mishandles exactly vertical triangles (contours grew spikes many mm long), so they are left out
    of what it sees, which is exact for closed meshes, and every waterline point is checked with the
    drop-cutter before use: a bad contour stops the job. Use closed (watertight) STL files.
- **Post**: LinuxCNC (default: T/M6 + G43 tool changes, A rotary axis, G64 blending) and GRBL 1.1 (pauses with `M0` on tool change, no A axis).
- **Arc output** (LinuxCNC): runs of feed moves become G2/G3 arcs (helical where Z changes) when an arc stays
  within 0.01 mm of every original segment, at most 180 deg per arc. The sign example shrinks from about 16000 to
  1800 lines. Options per job: `"post_options": {"arcs": false}` for G1 only, `"arc_tolerance": 0.005`. Rotary
  (A-axis) toolpaths stay linear in G93. Geometry offsets and circles are generated fine enough (chords within
  0.005 mm) that arcs fit.
- **Preview**: top-down SVG of the cuts, shaded by depth.
- **Virtual dry run** (`camcore-verify`): replays the G-code on a model of your machine and stock and reports crashes (rapids into material, cutting with the spindle stopped), travel-limit violations, unknown tool numbers, missing `G43`, cuts deeper than the flute length and G93 moves without F. G2/G3 arcs (XY plane, helical) are
  replayed through the same checks; arcs whose end radius differs from the start radius are errors. Also estimates machine time.
  Rotary stock (cylinder or bar) is a map of rays from the axis over (X, A), cut exactly by the tool body
  wherever the tool stands, also beside the axis (indexed work), so turning A with the tool too low over a
  bar's edges is reported as a crash.
- **Machine body check**: the machine JSON's `"body"` (spindle, collet nut, Z plate, gantry beam, ATC rack,
  tool setter, spoilboard, and on rotary jobs the chuck and headstock) is swept along every move; any contact,
  and the spindle or gantry meeting the stock, is an error. Tools need `"gauge_length"` (spindle nose to tip).
  Rotary stock carries `"chuck_hold"`, which places the job at the chuck (its G54). After every tool change
  the LinuxCNC post moves to the first X Y (A) at the G53 Z0 height before Z comes down, and verify refuses
  a descent at an unknown position. See `camcore/body.py` and `machine/README.md`.
- **Animated 3D viewer** (`--view`): one HTML file that plays the program with live material removal, A-axis rotation, tool changes and the check results. Works offline.
- **LinuxCNC simulator container** (`sim/`): runs the program through real LinuxCNC (2.9.4) on a simulated XYZA machine with tool changer; verified, see `sim/README.md`. `camcore check` runs it for you. Its GUI mode shows a vismach 3D model of the machine next to AXIS, drawn from the scene `camcore check` writes.

## Check a job (the only way to get a program for the machine)
```bash
camcore check examples/sign_job.json            # writes out/sign.ngc, out/sign_view.html, out/sign_report.txt
camcore check examples/sign_job.json --no-sim   # quick iteration, skips the LinuxCNC sim
```
`camcore check` generates the G-code, dry-runs it with `camcore-verify` against the machine JSON and the job's
`"stock"`, runs it through LinuxCNC in the sim container (`sim/`, build the image once), writes the viewer, and only
then writes the `.ngc`. If anything fails, the output folder gets only `NAME_report.txt`, and an older `NAME.ngc` /
viewer from a previous run is deleted. Only take programs from the output folder of `camcore check`.

- Machine: `--machine`, else the job's `"machine"` key (relative to the job file), else
  `machine/printnc_provisional.json`. The machine JSON's `"sim": {"ini": ...}` names the sim INI; the sim's G54
  offset and tool table are generated from the machine JSON and the job's tool library.
- Jobs need a `"stock"`: `{"type": "box", "x": [..], "y": [..], "z": [..]}` or
  `{"type": "cylinder", "diameter": D, "x": [..]}`.
- `-o DIR` sets the output folder (default `out`), `--sim-image` the Docker image (default `camcore-sim`).
- Exit code 0 only if everything passed. Without the sim the report says `PASSED WITHOUT SIM`.

## Desktop app
```bash
pip install -e .[gui]                 # PySide6 (with QtWebEngine for the embedded viewer)
camcore gui examples/sign_job.json    # or camcore-gui; without a file it starts a new job
```
The app edits an ordinary job file; it has no CAM logic of its own.
- **Operations**: the ordered list (Add, Up/Down, Duplicate, Remove) and a form for the selected one: id,
  `after`, tool, shape/model/points, indexing, and every parameter the op takes. An empty parameter is inherited;
  its grey text shows the value it will get and where from (the tool's data for the job's material, the job's
  `defaults`, or the op's own default). Problems that would stop the job (a required value missing, no cutting
  data for the material, an unknown key) are listed in red above the parameters.
- **Shapes & models**: rectangles, circles, SVG/DXF drawings and STL models, each editable as JSON. Renaming
  follows into the operations.
- **Job**: post, material, tool library, machine, tool order, stock, defaults, wrapped rotary, indexing.
- **Tool library**: the job's library file as a table (number = ATC pocket, diameter, kind, flutes, centre
  cutting) with feeds and speeds per material. Save refuses a library `load_library` would refuse
  (for example duplicate tool numbers).
- **Preview** (Ctrl+R) saves the job and runs `camcore check --no-sim` into a scratch folder, then plays the
  program in the embedded viewer. It is a look, never a program for the machine.
- **Export** (Ctrl+E) saves the job and runs the full `camcore check` with the LinuxCNC sim into the output
  folder (default: `out/` next to the job). As on the command line, the `.ngc` is written only if everything
  passes; the Report tab shows the log.
- **Tools > Rotary wizard**: the questions of `camcore rotary-wizard` in one dialog, with the same suggestions
  and checks (each suggestion follows the answers above it). It writes the same job file, opens it and checks it.

## Run it
```bash
conda env create -p .conda -f environment.yml   # Python + OpenCAMLib (conda-forge only)
conda activate ./.conda
pip install -e .[dev]
camcore examples/sign_job.json -o sign.ngc --preview sign_preview.svg
camcore examples/wrap_job.json -o wrap.ngc      # wrapped rotary
camcore-verify examples/sign.ngc --machine examples/machine.json --tools examples/tools.json --view sign_view.html
camcore-verify examples/wrap.ngc --machine examples/machine.json --tools examples/tools.json \
    --stock examples/stock_cylinder.json --view wrap_view.html
pytest
```

## Layout
```
camcore/
  tools.py      tool definitions (flat, ball, bull nose, V-bit) and their profiles
  toolpath.py   Toolpath IR: rapid/feed moves with X Y Z A
  ops/          profile, pocket, vcarve (+ medial.py, its medial axis), drill, wrap, index,
                rotary_rough/finish, surface_rough/finish (geometry in, IR out)
  post/         IR to G-code; base class, LinuxCNC, GRBL
  svg.py        SVG import
  dxf.py        DXF import
  stl.py        STL import
  wizard.py     camcore rotary-wizard (guided rotary job from an STL)
  project.py    job files as the desktop app edits them (no Qt)
  gui/          camcore-gui: PySide6 desktop app (editing and display only)
  cylmap.py     cylindrical heightmap r(x, A) of a mesh and its tool dilation
  surface.py    3-axis drop-cutter and waterline on a mesh (OpenCAMLib wrapper)
  job.py        JSON job format -> toolpaths
  preview.py    SVG preview
  verify.py     virtual dry run: machine + stock model and checks
  body.py       machine body (spindle, gantry, obstacles, rotary unit) for the collision check and the sim model
  viewer.py     animated 3D viewer (three.js, vendored, MIT)
  tooltable.py  tool library -> LinuxCNC .tbl
  cli.py        command line
sim/            LinuxCNC simulator container
```

## Not yet
rest machining after 3D roughing, a 2D preview of shapes in the app, the machine body in the HTML viewer.
