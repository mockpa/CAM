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

`a_max_diameter` is recorded for the rotary steps; nothing enforces it yet.

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
