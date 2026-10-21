# Relief designs (Reliefwerk) in camcore: plan

> **Status:** phases 1-6 (height field, 3D relief job, lamella geometry, nesting and board jobs, pins and
> paperwork, `camcore relief`) done on branch `relief`, with `profile` tabs at given points. Phase 7 open.
> Source: Paul's Reliefwerk 1.0 (`reliefwerk.zip`: reliefwerk.py, rw_core.py, rw_modes.py, rw_preview.py),
> reviewed 2026-10-18. Decisions (all defaults taken): Reliefwerk is Paul's own code and may go in the
> public repo; pins get paperwork only (no back-board job); lamella tabs stay on the bottom edge; the
> standalone Tk app is retired once phase 7 ships.

## Goal and rules
Add Reliefwerk's three designs (lamellas, 3D relief, pins) as a design front end that writes ordinary
camcore jobs. Reliefwerk's own G-code writer is not ported: every program still comes out of
`camcore check`, so ATC, ramp entries, verify, body check and the LinuxCNC sim protect relief jobs too.

1. **One path to the machine.** The relief code writes job JSON plus geometry (DXF, STL), never G-code,
   and hands the job to `check.check`, as `camcore rotary-wizard` does.
2. **No new machining code where camcore has it.** Lamella outlines use `profile` (tabs, ramps, climb);
   the 3D relief uses `surface_rough` / `surface_finish`. Reliefwerk's plunges and raster loops are dropped.
3. **Boundaries stay.** `camcore/relief/` may import `job`, `shapes`, `stl`, `check`; `ops`, `post` and
   `verify` don't change for it, and `verify` never imports it.
4. **Tests with every step;** each new module gets geometry tests and an example job that passes
   `camcore check` with the sim.
5. **No weakened checks.** 6. **`examples/*.ngc` must not change.** 7. **One phase per session.**

## Architecture
`camcore/relief/` sits beside `wizard.py`: design in, ordinary job out, then `check.check`.

| Reliefwerk part | camcore | Notes |
|---|---|---|
| Image / STL import, blur, levels, gamma, invert (`rw_core`) | `relief/heightfield.py` | STL through `stl.py` and an exact drop to a grid |
| Lamella slicing and profiles (`lam_model`) | `relief/lamella.py` | shapely polygons in mm, no tool offset |
| Nesting (`MaxRects`) | `relief/nesting.py` | outlines grown by tool diameter + web; sheet checked against machine travel |
| Lamella G-code (`lam_gcode_sheet`) | dropped | one job per sheet, a `profile` op per lamella |
| 3D relief G-code (`fr_gcode_*`) | dropped | `surface_rough` + `surface_finish` on a generated STL, like `examples/relief_job.json` |
| Pins (`pin_model`, cut list) | `relief/pins.py` | paperwork only |
| Parts list, assembly plan, pin plan | `relief/sheets.py` | English labels, units in headers |
| Shaded preview (`rw_preview`) | small shaded height image | toolpaths use the existing viewer |
| Tk GUI and CLI | `camcore relief` + a dialog in the PySide6 app | one question list (`wizard.Asker` pattern) |

## Phases
1. **Height field core** (`relief/heightfield.py`). Image loading (8/16-bit, alpha), crop or stretch to
   W x H, rotate, mirror, blur in mm, black/white point, gamma, invert; output `HeightField`. STL via
   `stl.py` and a drop to a grid. *Done when* tests pin size, orientation (row 0 = back edge, +Y up) and
   value range, and a known STL drops to the expected heights within 0.05 mm. **Done.**
2. **3D relief job** (`relief/relief3d.py`). Height field to a closed STL block; job with box stock,
   `surface_rough` (flat end mill, stock to leave), `surface_finish` raster (ball), optional frame via
   `boundary`. *Done when* the example passes `camcore check` with the sim, a `worst_depth`-style check
   finds no gouge, and finishing without roughing is refused or warned (the 12 mm flute problem).
   Measure OpenCAMLib time on 600 x 400 mm at 0.5 mm. **Done** (`relief/relief3d.py`, example `examples/relief/waves3d_job.json`).
   Found on the way: `surface._check_loop` refused correct waterline contours at hairpin tips (it probed
   across the contour); it now tests the cutter shrunk and grown by 0.01 mm at the point itself.
   **Measured:** 300 x 200 mm, 15 mm deep, model grid 0.5 mm, em6 + ball3 on hardwood: 35.5 min and 3.0 GB
   for the toolpaths (roughing 520 000 moves, finishing 250 000); the 110 x 70 mm example takes 50 s.
   600 x 400 mm would be about 2.5 h and over 10 GB: too slow for the app's Preview. Still open (see Risks).
3. **Lamella geometry** (`relief/lamella.py`). Strips (direction, thickness, gap, base, relief height,
   profile step, averaging over thickness); each a valid polygon simplified within 0.02 mm; valleys
   narrower than the cutter filled so `profile` leaves no sliver. **Done** (`relief/lamella.py`; identical to
   Reliefwerk's `lam_model` on its example). `profile`'s offset already bridges narrow valleys (no gouge, no
   sliver); `cut_outline` gives that closed outline for nesting and the plans.
4. **Nesting and sheet jobs** (`relief/nesting.py`, `relief/lamella_job.py`). MaxRects with rotation
   on/off; a sheet beyond machine travel or on a fixed obstacle (from the machine JSON) is refused
   before any toolpath; one job per sheet, `profile` per lamella (outside, depth = thickness + overcut,
   step-down, ramp, tabs), labels as op ids. Every part keeps its tabs. Tabs go on each part's bottom
   edge with `profile`'s `"tabs": {"at": [...]}` (added for this; existing jobs are unchanged).
   **Spacing (changed from this plan's first draft).** "Tool diameter + web" between parts (and
   Reliefwerk's spacing) lets the cuts of two neighbours meet at the peaks of a wavy top edge, and the
   scrap in every valley between them is cut free: loose pieces without tabs. Part outlines (as the
   cutter leaves them) are kept 2 x tool diameter + web apart and tool diameter + web inside the board
   edge, so the scrap is one connected skeleton nowhere thinner than the web (2 mm default).
   **Interlocking option** (`"interlock": true`, default off = Reliefwerk's packing). Two lamellas of the
   same length become one packing rectangle: the second turned 180 deg in the sheet plane, its relief
   edge facing down into the first's, ends aligned. The offset between them is the smallest that keeps
   the true 2D distance between the two outlines (shapely, not just vertical) at least 2 x tool
   diameter + web everywhere, ends included, the same rule as between any two parts. Pairing is greedy:
   tallest part first, partner = the part that gives the lowest pair height; a pair is only kept when
   it is lower than the two parts stacked with normal spacing and fits an empty board (else they go on
   singly). A 180 deg turn keeps the grain direction, so it is allowed with rotation off. Tabs stay on
   each part's bottom edge, which is on the outside of the pair. *Done when* (in addition to the above)
   tests pin the pair offset on two known profiles (a ramp and its twin interlock at the clearance /
   cos(slope)), every interlocked pair keeps the minimum distance, pairs never cost more area than
   separate parts, and the example design needs no more sheets than without interlocking.
   **Done** (`examples/relief/waves_lam_sheet1_job.json`). `tests/test_relief_sheets.py` replays a board
   with verify and finds the material left in one piece (parts on their tabs, scrap on the skeleton).
5. **Pins and paperwork** (`relief/pins.py`, `relief/sheets.py`). Pin grid, levels, cut list, position
   list, pin plan SVG/DXF; parts list and assembly plan for lamellas. CSV totals match the model. **Done**:
   pin grids identical to Reliefwerk's `pin_model` (all shapes, levels and stepless); lamella paperwork
   (`NAME_parts.csv`, `NAME_assembly.svg`, `NAME_boardN.svg/.dxf`) written by `lamella_job.write_jobs`; a dot
   marks each lamella's start on the board and assembly plans. DXF via ezdxf.
6. **CLI** (`camcore relief`). Questions through `wizard.Asker` (flags, `--yes`, prompts); output folder
   with jobs, STL/DXF, paperwork, then `check.check` on every job; `--no-sim`. Scripted-input tests for
   all three modes. **Done** (`relief/cli.py`): the seven image questions sit behind one "Adjust the image?"
   (default no) so a plain run asks little; every job's `check` runs with the sim unless `--no-sim`; pins have
   no job (paperwork only). `NAME_height.png` shows the prepared relief for a quick look at its orientation.
7. **Desktop app.** Relief dialog from the same question list with a shaded height preview; offscreen
   GUI tests; MANUAL.md relief chapter.

## Testing
Unit tests (`tests/test_relief_*.py`, synthetic inputs, exact numbers), golden jobs in `examples/relief/`
compared key for key, `verify.run` with zero errors (plus the depth-in-cutter check for the 3D relief),
`camcore check` with the sim before a phase is done, then air cut and MDF on the PrintNC.
Regression guards: the byte-for-byte fixtures stay, `verify` imports nothing from `camcore/relief/`, and
Reliefwerk's known failures (1200 mm sheet, finishing without roughing, straight plunge) are tested.

## Risks
- OpenCAMLib on a large relief is slow (measured in phase 2: 35 min / 3 GB for 300 x 200 mm): decimate the
  STL to the finishing tolerance, tile by `boundary`, and look at why roughing makes 520 000 moves.
- Machine values are provisional: the sheet limit always comes from the machine JSON.
- Many lamellas per sheet crowd the app's op list: group profiles per op if `profile` allows it.
- Tabs on the glued bottom edge: keep them and say "sand flush" in the paperwork.
- Interlocked parts put two cuts close together along a wavy line: the web between them is never thinner
  than the set web (2 mm default), and verify's body and gouge checks still see every program.

Left out on purpose: Reliefwerk's G-code writer, its STL rasteriser, its time estimates (verify gives real
times) and German UI text.
