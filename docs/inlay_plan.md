# Inlay operation: plan for Claude Code

> **Status: implemented** on branch `inlay`. Changes from this draft, decided during review:
> - **One job per part** (`"part": "pocket"` or `"plug"`; no `"both"`). `camcore check` writes one program per
>   job with one stock. The plug job names its pocket job with `"fit_with"`, and `check` runs the fit check.
> - **The plug flange is flat all the way to the frame edge.** A V-carve of "frame minus design" would leave a
>   sloped rim at the frame edge, and that rim would rest on the base board. The frame defaults to the whole
>   plug stock (nothing to trim); `plug_margin` gives a smaller one, with a note to trim inside the flange.
> - **Medial by default.** The offset method rounds off sharp corners, and the fit check showed the plug
>   standing proud because of it. The plug also plunges into notches sharper than 90 deg (a tangent cusp, like
>   the top of a heart, is too thin for any carving path to reach).
> - **No `rough_tool` / rest machining yet**; `"clear": {"method": "adaptive"}` covers wide designs.
> - **Fit check details**: verify grid 0.2 mm, programs replayed as G1, only inside the plug frame. It also
>   fails when the walls stand apart: a 90 deg plug in a 60 deg pocket seats at the right height on the rim.

Put this file in your CAM repo as `docs/inlay_plan.md`, then tell Claude Code:
"Implement docs/inlay_plan.md on a new branch called inlay. Follow CLAUDE.md. Use plan mode first."

Checked against mockpa/CAM at commit `ce5e2d1` (2026-10-15, the user manual). Nothing below is implemented yet.

## What already exists in the repo (reuse it)
- Flat-bottom V-carve: `camcore/job.py` around line 182. When a `vcarve` op has `clear_tool`, it computes
  `ops.vcarve_flat_bottom_area(shape, tool, max_depth)`, runs `ops.pocket` on it first (passing the op's
  `"clear": {...}` args through, so `"method": "adaptive"` already works), and makes the V-bit pass wait for it.
  The inlay should build on this, not copy it.
- `vcarve()` in `camcore/ops/vcarve.py` takes `top_z`, `max_depth`, `method` (`"offset"` or `"medial"`).
  `"medial"` needs `entry: "plunge"` and a centre-cutting V-bit.
- Shapes in `build_shape` (`camcore/job.py` line 21): rect, circle, polygon, svg, dxf, union, difference.
  There is no mirror shape. The SVG importer applies `transform`, so `scale(-1,1)` in an SVG works as a workaround today.
- `docs/PLAN.md` lists inlays as "later". `docs/MANUAL.md` line 189 mentions inlays only under V-carve.

## The geometry
Use the same V-bit for both parts so the walls match.

- **Pocket (female, base board):** a flat-bottom V-carve of the design, max depth `pocket_depth`.
- **Plug (male, insert board):**
  1. Mirror the design about the Y axis, and move it back so its lower-left corner is where the original's was.
  2. V-carve the region around it: a frame `plug_margin` mm outside the design, minus the mirrored design.
  3. Start depth s = `pocket_depth - glue_gap`. This puts the plug outline s below the plug board top; the walls continue up to the top at the bit angle.
  4. Carve m = `surface_gap` deeper than s. The flat flange at s + m is cleared first.
- **When glued:** the plug is flipped into the pocket. The walls meet, the plug face sits `glue_gap` above the pocket floor, and the flange sits `surface_gap` above the base surface, so clamping pressure goes into the walls. See the cross-section diagram in the project thread.

## Job syntax
```json
{"op": "inlay", "shape": "art", "tool": "v60", "clear_tool": "em3",
 "pocket_depth": 5, "glue_gap": 0.5, "surface_gap": 1.5, "plug_margin": 5,
 "part": "both", "method": "offset", "clear": {"method": "adaptive"}, "rough_tool": "em6"}
```
- `part` is `"pocket"`, `"plug"` or `"both"`. With `"both"`, the job writes NAME_pocket.ngc and NAME_plug.ngc, each with its own stock and setup in the `camcore check` report.
- `rough_tool` is optional, for wide designs. A big end mill clears the open floor (pocket) or flange (plug) first. `clear_tool` then cleans only the strip between the rough pass and the V-bit's flat edge (rest machining of that strip, not a second full pocket).
- Refuse, with a clear error, settings that cannot work:
  - `glue_gap >= pocket_depth`
  - `surface_gap <= 0`
  - a V-bit too small for `pocket_depth` (the bit's full depth must cover s + m for the plug)
  - medial without a centre-cutting bit

## Also add
- A generic shape `{"type": "mirror", "shape": {...}, "axis": "y"}` (also `"x"`), keeping the lower-left corner in place.
- A fit check:
  1. Take the simulated stock heightmaps of both programs from `camcore.verify`.
  2. Flip the plug and slide it down until the walls first touch.
  3. Report the bottom gap, the flange gap and any interference.

  `camcore check` fails if either gap is more than 0.1 mm off `glue_gap` / `surface_gap`, or if the parts interfere.
- Tests:
  - Exact depths and the mirror on a square and an L shape.
  - Validation errors.
  - The fit check on a good job and on a deliberately wrong one (plug cut with a 90 deg bit, pocket with 60 deg).
  - `examples/inlay_job.json` using `examples/sign.svg`, passing `camcore check` including the LinuxCNC sim.
- Docs:
  - README feature entry.
  - A short "Inlays" section in `docs/MANUAL.md`: which board gets which part, glue, clamp, plane off, test in MDF first.
  - Tick the item off in `docs/PLAN.md`.

## Other gaps found while reading the repo (separate tasks, not part of this one)
- **Drilling** (`camcore/ops/drill.py`):
  - holes are hand-typed XY points only, with no import from DXF/SVG circles
  - plain G0/G1 moves, no G81/G83 cycles
  - no dwell, no chip-break
  - centre-cutting tools only, so holes bigger than the drill need a helical pocket
- **Workpiece/edge finding:** there is no probing code (no G38). G54 is set by hand at the machine (MANUAL section 8).
  The touch probe is only registered as T99, and verify refuses a spindle start with it loaded.
  Suggested: a probing routine (corner X/Y/Z, bore/boss centre, rotary axis height) using G38.2 and `G10 L20`, tested in the sim.
- **Open points from `machine/README.md`:**
  - the PrintNC profile is provisional (travel, G54, gauge lengths, tool.tbl)
  - verify does not check `a_max_diameter`
  - with the rotary axis 75 mm above the spoilboard, the tool only reaches about 63 mm above the axis, so the planned 150 mm swing does not fit
- **Reliefwerk:** its integration plan is a separate document (`cam/reviews/reliefwerk_integration_plan.pdf` in the project files).

## Test environment note
The test suite uses pytest-qt and PySide6. On a headless box without `libEGL.so.1`, pytest stops at startup with an INTERNALERROR.
On your desktop this is fine. In CI, install `libegl1` (`-p no:pytestqt` did not get past it here).
