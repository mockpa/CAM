# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# camcore

CAM software for a LinuxCNC router/mill with an automatic tool changer and a 4th (A) rotary axis.
Modes: 2.5D, V-carve, wrapped rotary, rotary from STL (done); rotary wizard, indexed 3+1, 3-axis 3D (planned).
The full roadmap is in `docs/PLAN.md`. The step-by-step build plan is in `docs/IMPLEMENTATION_GUIDE.md`. Work one guide step per session.

## Commands
- Install: `python3 -m venv .venv && . .venv/bin/activate && pip install -e .[dev]`
- Tests: `pytest -q` (must stay green; add tests with every feature)
- Single test: `pytest -q tests/test_ops.py::test_wrap_maps_y_to_a_and_depth_to_radius`
- Gate a job (generate + verify + LinuxCNC sim + viewer; `.ngc` written only if all pass): `camcore check examples/sign_job.json` (`--no-sim` for quick iterations, `-o DIR` output folder)
- Generate G-code only: `camcore examples/sign_job.json -o out/sign.ngc --preview out/sign.svg` (`--post grbl` overrides the job's post)
- Dry run + 3D viewer: `camcore-verify out/sign.ngc --machine examples/machine.json --tools examples/tools.json --view out/sign_view.html`
- Rotary dry run: add `--stock examples/stock_cylinder.json`
- Tool table for the sim: `python -m camcore.tooltable examples/tools.json sim/config/tool.tbl`
- LinuxCNC sim: `cd sim && docker build -t camcore-sim . && docker run --rm -v "$PWD/../out:/work" camcore-sim check /work/sign.ngc` (exit 0 = LinuxCNC ran it all; about 1 min per example). Verified with LinuxCNC 2.9.4, see `sim/README.md`.
- Sim GUI: `docker run --rm -p 6080:6080 -v "$PWD/out:/work" camcore-sim gui /work/wrap.ngc`, then open http://localhost:6080/vnc.html

`out/` is gitignored scratch output. `examples/*.ngc` are committed fixtures: `tests/test_verify.py::test_examples_verify_clean` replays them. After any post or op change that alters output, regenerate `examples/sign.ngc`, `examples/wrap.ngc` and `examples/pawn.ngc` from their job files and confirm they still verify clean (`tests/test_rotary_ops.py` compares the pawn byte for byte).

## Architecture (keep these boundaries)
Pipeline: JSON job → `job.run_job` → list of `Toolpath` (IR) → `post.get_post(name).emit(paths)` → G-code → `verify.run` (independent replay) → LinuxCNC sim. `camcore/check.py` (`camcore check`, dispatched from `cli.py`) runs the whole chain and is the only path that should produce programs for the machine.

- `camcore/job.py`: builds named shapes (rect/circle/polygon/svg/union/difference), merges `defaults` into each operation, and dispatches by name with `getattr(ops, spec["op"])(shape, tool, **params)`. A new op must be exported from `ops/__init__.py`, and its keyword args are the job-file keys. Special cases live here: `drill` takes `points` instead of a shape; a `vcarve` with `clear_tool` adds a pocket pass first. `rotary` wrapping is applied to every path after all ops are built, and the optional `"tool_order": "minimize_changes"` regroup runs last.
- `camcore/ops/*`: geometry + tool + params in, `Toolpath` IR out. Ops never write G-code.
- `camcore/toolpath.py`: the IR (rapid/feed moves with X Y Z A; `None` means the axis is unchanged). Every mode emits it; posts, viewer and checks consume it.
- `camcore/post/*`: IR to G-code per controller, registered in `post/__init__.py` `POSTS`; `job_post(job)` applies the job's `"post_options"`. The LinuxCNC post fits G2/G3 arcs to feed runs with `camcore/arcfit.py` (tolerance against the original segments, not just the vertices); ops still emit only lines. `linuxcnc.py` is the primary target (T/M6 + G43, next-tool staging, G93 for A moves, G64 P blending). In G93 paths every axis is written the first time it appears (the position after a tool change is unknown), and a feed before X, Z and A are known is refused. `grbl.py` is secondary (M0 pause on tool change, refuses A moves).
- `camcore/ops/rotary.py`: wrap = Y becomes A (deg = Y / r), Z0 is on the rotary centreline, so cutting Z = radius + depth.
- `camcore/verify.py`: independent G-code replay with machine + stock model (heightmap for box stock; for cylinder stock a radius map over (X, A) whose cells are rays from the axis, cut by the exact tool body with `Stock._ray_interval`; the viewer's `rayInterval` is a JS port, kept identical). It parses G-code text (G0/G1, and G2/G3 replayed as chords through the same checks) and must never import ops or posts, so it stays an independent check of their output. `verify_cli.py` is its CLI.
- Import: `svg.py` (own parser, affine transforms applied before flattening), `dxf.py` (ezdxf `make_path` + `flattening`), both via `shapes.py` (`join_chains`, even-odd `rings_to_geom`). Open/unsupported geometry is an error, never silently dropped.
- Rotary from STL: `stl.py` loads and places the mesh (model frame = machine frame at A = 0, rotary axis = model X; `revolve` builds solids of revolution). `cylmap.py`: `radial_map` ray-casts r(x, A) (own vectorised Moller-Trumbore, no rtree/embree), `tool_map` dilates it per cutter in closed form, treating everything between the axis and r along each ray as material (conservative). The tool looks along u(A) = (0, -sin A, cos A), matching the viewer and verify's cylinder stock (ISO sign: +A turns the part by -A); `tests/test_cylmap.py` pins this.
- `ops/rotary3d.py`: `rotary_rough` / `rotary_finish`, dispatched by `job.py` for ops with `"model"` (stock diameter from the cylinder stock). Paths interpolate `_envelope` (3x3 max of the no-gouge grid), which is provably above the sampled model between nodes; plain interpolation gouged 0.17 mm at a ball's edge contact. `decimate` compares dropped points with the move at the same X/A position (the controller moves X, A, Z together), with A relative to the move start; nearest-point distance hid 0.13 mm Z errors on steep flanks. `tests/test_rotary_ops.py::worst_gouge` checks paths against the model by brute force: keep it passing. Rotary-op paths are not wrapped, and `job.continue_a` shifts each by whole turns to start near the previous A.
- Ops report adaptations- Ops report adaptations (fewer tabs, no lead, ...) in `Toolpath.notes`; `camcore check` puts them in the report.
- `camcore/viewer.py` + `viewer_template.html`: self-contained HTML player (vendored three.js r128 in `camcore/vendor/`, shipped as package data).
- `sim/`: Docker image with Debian trixie's `linuxcnc-uspace` (2.9.4, non-setuid `rtapi_app`), XYZA sim config in `sim/config/`, headless `run_check.py`. `entrypoint.sh` picks up `/work/tool.tbl`, `/work/machine.ini` and `/work/machine.var` (G54) if they are mounted.

- `machine/`: provisional profile of the target machine (PrintNC V4 Metalcutter, still being built): verify JSON plus a matching sim INI and G54 `.var`. Keep the three in sync; `examples/machine.json` stays the small fixture the tests use. See `machine/README.md`.

## Conventions
- Units: mm and degrees everywhere internally.
- Climb = wall on the right of travel (M3 spindle): `rings(..., wall_outside=True)` for pockets, inside profiles and V-carve.
- Offsets and circles go through `ops/common.offset()` / `quad_segs()` so chords stay within 0.005 mm of the true arc; coarser geometry won't be refit to arcs.
- Machine conventions: rotary A turns around X; Z0 on the rotary centreline for rotary jobs; Z0 at the stock top for flat jobs.
- Tools come from `examples/tools.json`-style libraries; `number` is the ATC pocket and the LinuxCNC tool number. Per-material feeds live under a tool's `"cutting"` (stripped by `Tool.from_dict`, merged in `job.run_job` by op signature). Ordering is `job.order_paths` (dependencies via `"id"`/`"after"`).
- ATC (PrintNC: Benchworks, 6 pockets, M6 remap measures length): camcore emits only `Tn M6` + `G43 Hn`. verify enforces `atc_pockets`, `probe_tool` (never with spindle on) and the real `tool_table` from the machine JSON. `center_cutting` (default false) gates every straight plunge.
- Ops never plunge into material unless `entry="plunge"` and the tool is centre-cutting: entries live in `camcore/ops/entry.py` (ramp along the contour, helix for pockets). Profiles are built in `camcore/ops/contour.py`: distance-along-contour parametrisation where depth = max(base ramp/level, tab profile), emitted exactly at kinks and crossings (no sampling). verify enforces the same rule independently.
- Every new op needs: a unit test on geometry (depths, offsets), a run through `camcore.verify.run` with zero errors, and an example job.
- Never weaken a verify check to make a test pass. If a check is wrong, fix the check and add a test that shows why.
- Generated G-code must pass `camcore check` (with the sim) before it is called done.
- Jobs must carry a `"stock"`; `camcore check` refuses jobs without one.

## Safety
This software drives a real machine. Any change to posts, rotary math or verify needs a test that pins the exact G-code or numbers it produces.
