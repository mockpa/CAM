# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# camcore

CAM software for a LinuxCNC router/mill with an automatic tool changer and a 4th (A) rotary axis.
Modes: 2.5D, V-carve, wrapped rotary (done); rotary from STL, indexed 3+1, 3-axis 3D (planned).
The full roadmap is in `docs/PLAN.md`. The step-by-step build plan is in `docs/IMPLEMENTATION_GUIDE.md`. Work one guide step per session.

## Commands
- Install: `python3 -m venv .venv && . .venv/bin/activate && pip install -e .[dev]`
- Tests: `pytest -q` (must stay green; add tests with every feature)
- Single test: `pytest -q tests/test_ops.py::test_wrap_maps_y_to_a_and_depth_to_radius`
- Generate G-code: `camcore examples/sign_job.json -o out/sign.ngc --preview out/sign.svg` (`--post grbl` overrides the job's post)
- Dry run + 3D viewer: `camcore-verify out/sign.ngc --machine examples/machine.json --tools examples/tools.json --view out/sign_view.html`
- Rotary dry run: add `--stock examples/stock_cylinder.json`
- Tool table for the sim: `python -m camcore.tooltable examples/tools.json sim/config/tool.tbl`
- LinuxCNC sim: `cd sim && docker build -t camcore-sim . && docker run --rm -v "$PWD/../out:/work" camcore-sim check /work/sign.ngc` (exit 0 = LinuxCNC ran it all; about 1 min per example). Verified with LinuxCNC 2.9.4, see `sim/README.md`.
- Sim GUI: `docker run --rm -p 6080:6080 -v "$PWD/out:/work" camcore-sim gui /work/wrap.ngc`, then open http://localhost:6080/vnc.html

`out/` is gitignored scratch output. `examples/*.ngc` are committed fixtures: `tests/test_verify.py::test_examples_verify_clean` replays them. After any post or op change that alters output, regenerate `examples/sign.ngc` and `examples/wrap.ngc` from their job files and confirm they still verify clean.

## Architecture (keep these boundaries)
Pipeline: JSON job → `job.run_job` → list of `Toolpath` (IR) → `post.get_post(name).emit(paths)` → G-code → `verify.run` (independent replay).

- `camcore/job.py`: builds named shapes (rect/circle/polygon/svg/union/difference), merges `defaults` into each operation, and dispatches by name with `getattr(ops, spec["op"])(shape, tool, **params)`. A new op must be exported from `ops/__init__.py`, and its keyword args are the job-file keys. Special cases live here: `drill` takes `points` instead of a shape; a `vcarve` with `clear_tool` adds a pocket pass first. `rotary` wrapping is applied to every path after all ops are built, and the optional `"tool_order": "minimize_changes"` regroup runs last.
- `camcore/ops/*`: geometry + tool + params in, `Toolpath` IR out. Ops never write G-code.
- `camcore/toolpath.py`: the IR (rapid/feed moves with X Y Z A; `None` means the axis is unchanged). Every mode emits it; posts, viewer and checks consume it.
- `camcore/post/*`: IR to G-code per controller, registered in `post/__init__.py` `POSTS`. `linuxcnc.py` is the primary target (T/M6 + G43, next-tool staging, G93 for A moves, G64 P blending). `grbl.py` is secondary (M0 pause on tool change, refuses A moves).
- `camcore/ops/rotary.py`: wrap = Y becomes A (deg = Y / r), Z0 is on the rotary centreline, so cutting Z = radius + depth.
- `camcore/verify.py`: independent G-code replay with machine + stock model (heightmap for box stock, radius map over (X, A) for cylinder stock). It parses G-code text and must never import ops or posts, so it stays an independent check of their output. `verify_cli.py` is its CLI.
- `camcore/viewer.py` + `viewer_template.html`: self-contained HTML player (vendored three.js r128 in `camcore/vendor/`, shipped as package data).
- `sim/`: Docker image with Debian trixie's `linuxcnc-uspace` (2.9.4, non-setuid `rtapi_app`), XYZA sim config in `sim/config/`, headless `run_check.py`. `entrypoint.sh` picks up `/work/tool.tbl` and `/work/machine.ini` if they are mounted.

## Conventions
- Units: mm and degrees everywhere internally.
- Machine conventions: rotary A turns around X; Z0 on the rotary centreline for rotary jobs; Z0 at the stock top for flat jobs.
- Tools come from `examples/tools.json`-style libraries; `number` is the ATC pocket and the LinuxCNC tool number.
- Every new op needs: a unit test on geometry (depths, offsets), a run through `camcore.verify.run` with zero errors, and an example job.
- Never weaken a verify check to make a test pass. If a check is wrong, fix the check and add a test that shows why.
- Generated G-code must run in the LinuxCNC sim (`sim/`) before it is called done.

## Safety
This software drives a real machine. Any change to posts, rotary math or verify needs a test that pins the exact G-code or numbers it produces.
