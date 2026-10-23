# Machine profiles

`printnc_provisional.*` describe the PrintNC V4 Metalcutter that is still being built. The numbers come
from the build plan, not from a running machine:

- travel 1035 x 1035 x 150 mm, home switches back/left/top (X min, Y max, Z top)
- rapids about 17 m/min (critical speed of the 1200 mm SFU1610 screws)
- A axis planned, up to about 150 mm swing diameter
- Benchworks ATC, 6 pockets (`atc_pockets`, tool number = pocket); the M6 remap measures tool length
- touch probe is tool 99 (`probe_tool`; verify refuses a spindle start with it loaded)
- `tool_table`: set it to the real LinuxCNC `tool.tbl` once it exists; verify then refuses unknown tool numbers
  and `camcore check` runs the sim with that table
- the G54 offset (20, 20, -120) is a placeholder

`printnc_sim.ini` keeps the planned speeds and accelerations (the sim GUI runs with them); `camcore check`
raises them for its sim run only, which then takes seconds instead of the program's machine time.

`a_max_diameter` limits the stock diameter in `camcore rotary-wizard`; verify does not check it yet.

`"body"` describes the machine body for the collision check in camcore-verify and for the sim
GUI's vismach model (format in `camcore/body.py`). All of it is provisional, in tool-tip machine
coordinates (G54 Z -120 with 18 mm stock puts the spoilboard at Z -138):

- spindle, relative to the spindle nose: ER20 collet nut (32 mm, 16 below the nose), shaft,
  G-Penny housing (80 mm, from 30 to 250 above the nose), Z plate (160 wide, behind the spindle)
- gantry beam (120 x 80 tube), its bottom 168 mm above the spoilboard, 65..145 mm behind the tool axis
- spoilboard (the tool may cut into it, the spindle may not), ATC rack at the back edge
  (X 700..1000), tool setter at X 660
- rotary unit, only on the table for rotary jobs: 100 mm chuck and headstock, jaw face at
  X 180, axis at Y 400, Z -63 (75 mm above the spoilboard). A rotary job's G54 comes from this
  and the stock's `"chuck_hold"`: Y0 Z0 on the axis, the jaw face at stock X start + hold.

Each tool needs `"gauge_length"` (spindle nose to tip) in the library; without it the check
assumes `default_tool_length` and warns. Measure all of it once the machine stands. Open point
from the build plan: with the axis 75 mm above the spoilboard, a tool tip can only reach about 63
mm above the axis (Z travel), so the planned 150 mm swing does not fit yet.

| File | Used by |
|---|---|
| `printnc_provisional.json` | `camcore-verify --machine` |
| `printnc_sim.ini` | sim, mounted as `/work/machine.ini` |
| `printnc_sim.var` | sim G54 offset for manual sim runs, mounted as `/work/machine.var`. `camcore check` generates it from `work_offset`; a test keeps the file in sync. |

`camcore check` uses this profile by default. Manual sim run against it (from the repo root, with the program in `out/`):

```bash
cp machine/printnc_sim.ini out/machine.ini && cp machine/printnc_sim.var out/machine.var
docker run --rm -v "$PWD/out:/work" camcore-sim check /work/sign.ngc
```

When the machine runs, IMPLEMENTATION_GUIDE step 4 replaces these files with an import of the real
LinuxCNC config.
