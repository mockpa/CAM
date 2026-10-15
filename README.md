# camcore

Python core for a CAM application. Phase 0/1 of [the plan](../PLAN.md): 2.5D and V-carve toolpaths with LinuxCNC (and GRBL) output. 3D and 4-axis come next.

## What works today
- **Profile**: inside / outside / on-line, multiple step-downs, climb or conventional.
- **Pocket**: contour-parallel clearing with islands, step-downs, stepover as a fraction of tool diameter.
- **V-carve**: offset-based, depth follows the distance to the edge; optional `max_depth` + `clear_tool` for flat-bottom carving (an endmill clears the floor first).
- **Drill**: peck drilling.
- **Rotary (wrapped)**: `"rotary": {"diameter": 40}` wraps any job around the A axis (Y becomes A, Z0 on the axis centreline), with G93 inverse-time feeds.
- **Tool library / ATC**: `"tool_library": "tools.json"` with tool numbers; LinuxCNC post emits `Tn M6`, `G43 Hn` and stages the next tool; `"tool_order": "minimize_changes"` groups ops by tool.
- **Import**: SVG (path with M/L/H/V/C/Q/Z, polygon, polyline, rect, circle; even-odd holes), plus rect/circle/polygon/union/difference shapes in the job file.
- **Post**: LinuxCNC (default: T/M6 + G43 tool changes, A rotary axis, G64 blending) and GRBL 1.1 (pauses with `M0` on tool change, no A axis).
- **Preview**: top-down SVG of the cuts, shaded by depth.
- **Virtual dry run** (`camcore-verify`): replays the G-code on a model of your machine and stock and reports crashes (rapids into material, cutting with the spindle stopped), travel-limit violations, unknown tool numbers, missing `G43`, cuts deeper than the flute length and G93 moves without F. Also estimates machine time.
- **Animated 3D viewer** (`--view`): one HTML file that plays the program with live material removal, A-axis rotation, tool changes and the check results. Works offline.
- **LinuxCNC simulator container** (`sim/`): runs the program through real LinuxCNC on a simulated XYZA machine with tool changer. Not yet tested, see `sim/README.md`.

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

## Run it
```bash
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
  tools.py      tool definitions (flat, ball, V-bit) and their profiles
  toolpath.py   Toolpath IR: rapid/feed moves with X Y Z A
  ops/          profile, pocket, vcarve, drill (geometry in, IR out)
  post/         IR to G-code; base class, LinuxCNC, GRBL
  svg.py        SVG import
  job.py        JSON job format -> toolpaths
  preview.py    SVG preview
  verify.py     virtual dry run: machine + stock model and checks
  viewer.py     animated 3D viewer (three.js, vendored, MIT)
  tooltable.py  tool library -> LinuxCNC .tbl
  cli.py        command line
sim/            LinuxCNC simulator container
```

## Not yet
Arcs (G2/G3) and arc fitting, tabs, lead-in/out, ramp entry (it currently plunges straight down; use a centre-cutting bit), SVG transforms, DXF import, medial-axis V-carve, rotary from STL, indexed 3+1, 3-axis 3D, simulation, GUI.
