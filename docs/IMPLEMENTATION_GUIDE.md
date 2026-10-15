# Building camcore on your Linux machine with Claude Code

This guide takes the code that already exists (2.5D, V-carve, wrapped rotary, ATC post, dry run, 3D viewer, LinuxCNC sim container) and turns it into the full CAM package on your own PC. Each step has a prompt to paste into Claude Code and a check that tells you the step is done.

Work one step per Claude Code session. Commit after every step that passes its check.

---

## 0. One-time setup

You need Linux (Debian/Ubuntu/Mint are easiest), Python 3.10 or newer, git, Docker and Node 18 or newer.

```bash
sudo apt install python3 python3-venv python3-pip git docker.io nodejs npm
sudo usermod -aG docker $USER        # then log out and back in
npm install -g @anthropic-ai/claude-code
```

Check: `python3 --version`, `docker run --rm hello-world` and `claude --version` all work.

## 1. Get the code onto your machine

1. Download `camcore.tar.gz` from the project thread (it is attached to the message with this guide).
2. Unpack it and make it a git repo (the roadmap is included as `docs/PLAN.md`):

```bash
mkdir -p ~/cnc && cd ~/cnc
tar xzf ~/Downloads/camcore.tar.gz          # creates ~/cnc/camcore
cd camcore
git init && git add -A && git commit -m "camcore starting point"
```

3. Optional: create an empty private GitHub repo and `git remote add origin ... && git push -u origin main`.

`CLAUDE.md` in the repo root tells Claude Code how the project is laid out, the safety rules and the commands. Claude Code reads it automatically.

## 2. Check the starting point

Start Claude Code in the repo (`cd ~/cnc/camcore && claude`) and paste:

> Set up a Python venv in .venv, install the package with dev extras, run the tests, then generate both example programs into out/ and run camcore-verify on each with --view. Report the test count, the verify results and where the viewer files are. Don't change any code.

**Done when:** all tests pass (27 at handover), both programs verify with 0 errors, and `out/*_view.html` opens in your browser and plays.

## 3. Get the LinuxCNC simulator running

The container was written in a cloud session that could not download Debian packages, so it has never been built. This step makes it work.

> Build the Docker image in sim/ and run `check` mode on out/sign.ngc and out/wrap.ngc. Fix whatever fails in the Dockerfile, entrypoint.sh, run_check.py or config/camcore_sim.ini until both programs run to the end with exit code 0. Then prove the check catches real problems: make a copy of sign.ngc with one move 1000 mm outside the X travel, and one with a T99 tool change, and confirm `check` fails on both with LinuxCNC's own error message. Add a short "verified" note to sim/README.md with the LinuxCNC version used.

Then try the GUI mode yourself: `docker run --rm -p 6080:6080 -v "$PWD/out:/work" camcore-sim gui /work/wrap.ngc` and open http://localhost:6080/vnc.html.

**Done when:** good programs pass, both broken copies fail, and you have watched AXIS run a program in the browser.

## 4. Make the virtual machine match your real machine

Copy your real LinuxCNC config folder (the `.ini`, `.hal` files and `tool.tbl`) into `machine/` in the repo first.

> In machine/ is my real LinuxCNC config. Write camcore/machine_import.py that reads my INI and tool table and produces (1) a camcore machine JSON (travel per axis, has_a, rapid rate from MAX_VELOCITY) and (2) a sim INI for sim/ with the same axis limits, velocities and coordinates but simulated HAL. Add a CLI `camcore-machine import machine/<my>.ini`. Ask me for my usual G54 offset for flat jobs and for rotary jobs and store both as named setups in the machine JSON. Add tests with a small sample INI.

**Done when:** `camcore-verify` and the sim both use your real limits, and a program that would hit a limit on your machine fails in both.

## 5. One command that gates every program

> Add `camcore check JOB.json` that generates the G-code, runs camcore-verify (with my machine JSON and the stock from the job), runs the LinuxCNC sim check in Docker, writes the viewer HTML, and only then writes the .ngc to the output folder. If anything fails, it writes nothing except a report. Add a --no-sim flag for quick iterations. Document it in README.md.

**Done when:** you only ever take programs from the output folder of `camcore check`.

## 6. Make 2.5D production-ready

Paste these one at a time. Each one should end with tests, an example job, and a clean `camcore check`.

> Add ramp entry (straight ramp along the contour, max angle parameter) and helical entry for pockets, so no operation plunges straight down unless the tool is marked centre-cutting and the job allows it.

> Add holding tabs to profile cuts: count or spacing, width, height; tabs are skipped by lifting Z on the last depth passes.

> Add lead-in/lead-out arcs for profiles, and G2/G3 arc output in the LinuxCNC post with arc fitting (fit arcs to polyline runs within a tolerance, default 0.01 mm). Keep linear output as an option. Pin the output with exact G-code tests.

> Add DXF import (ezdxf: lines, arcs, circles, lwpolylines, splines; join open chains into closed shapes within a tolerance) and apply SVG transform attributes in the SVG importer.

**Done when:** you cut a real 2.5D part and a V-carved sign (see "First cuts" below).

## 7. Automatic tool changer

> Extend the tool library and post for my ATC: optional tool-length probing after each change (a configurable O-word subroutine call or a G38.2 routine), a check in camcore-verify and camcore check that every tool number exists in my real tool.tbl, and dependency-aware tool ordering (an op can declare `after: [op ids]`; minimize tool changes without breaking those dependencies, and keep V-carve flat clearing before its V-bit pass). Also add per-tool default feeds and speeds by material in the library.

Before this, tell Claude Code what kind of changer you have (carousel or rack) and how LinuxCNC handles M6 on your machine (remap subroutine name, or plain iocontrol).

**Done when:** a 3-tool job runs in the sim with the right T/M6/G43 sequence, and a job with a tool missing from your table is refused.

## 8. Rotary from STL (the DeskProto-style feature)

This is the biggest step. Do it in three sessions.

> Session A: Add STL import with trimesh and a cylindrical heightmap: for a model placed on the X axis, compute r(x, A) = the outermost radius of the mesh at each X and angle (ray casting from outside towards the axis). Add tool-shape dilation so r_tool(x, A) is where the tip of a given tool can go without gouging. Test with a cylinder and a cone, where the answer is known exactly.

> Session B: Add rotary operations: roughing (Z levels from stock radius inwards with stock-to-leave, A-raster), finishing (A-raster, X-raster and helical), with stepover in degrees or mm. Output A moves with G93 through the LinuxCNC post. Extend camcore-verify's cylinder stock to handle these and add an example job with an STL (a chess pawn).

> Session C: Add a guided rotary flow `camcore rotary-wizard model.stl`: asks stock diameter and length, auto-centres the model on the axis, lets me rotate it about X, picks roughing and finishing tools from my library, offers coarse/normal/fine presets, then runs camcore check and opens the viewer.

**Done when:** the pawn passes camcore check, plays correctly in the viewer, and runs in the sim.

## 9. Indexed 3+1

> Add indexed rotary: a job can set A to fixed angles (list, or N equal sides) and run any 2.5D or V-carve op on each face, with the stock modelled correctly in camcore-verify.

## 10. Desktop app

> Build a PySide6 desktop app on top of the library: project file (the JSON job), tool library editor, operation list with parameters, the existing viewer embedded with QtWebEngine, the rotary wizard as a dialog, and an Export button that runs camcore check. No CAM logic in the GUI layer.

## 11. 3-axis 3D

> Add 3D operations for STL models using OpenCAMLib if it installs cleanly (pip or build from source), else a numpy drop-cutter: Z-level roughing, raster finishing with ball and flat tools, waterline finishing. Extend camcore-verify's heightmap stock for ball and bull-nose tools.

## 12. Polish (any order)

> Medial-axis V-carve (sharp corners, fewer moves) using a Voronoi of the boundary, compared against the existing offset V-carve for depth accuracy.

> Adaptive (constant engagement) pocket clearing.

> A vismach 3D model of my machine (gantry, spindle, rotary chuck, ATC rack) in the sim container, so the AXIS view shows the real machine and LinuxCNC can catch machine-body collisions.

---

## First cuts on the real machine

Even with every check green, prove each new kind of program like this:

1. **Air cut:** set G54 Z 50 mm above the real stock and run the whole program. Watch tool changes and A moves.
2. **Single block** through the first tool change and the first plunge, feed override at 25 %.
3. **Soft material first:** foam or MDF, then the real stock.
4. Hand on the E-stop the first time every new operation type runs.

## Working well with Claude Code

- Start each step in plan mode (Shift+Tab) so you see the plan before it edits.
- Tell it what you saw on the machine when something is off. Paste G-code lines or a screenshot of the viewer.
- After each step: `pytest -q`, `camcore check` on the examples, `git commit`.
- If a session gets long or confused, commit and start a fresh one with the next prompt.
