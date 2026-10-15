# Machine profiles

`printnc_provisional.*` describe the PrintNC V4 Metalcutter that is still being built. The numbers come
from the build plan, not from a running machine:

- travel 1035 x 1035 x 150 mm, home switches back/left/top (X min, Y max, Z top)
- rapids about 17 m/min (critical speed of the 1200 mm SFU1610 screws)
- A axis planned, up to about 150 mm swing diameter
- touch probe is tool 99 (it lives in an ATC pocket; the spindle must never start with it loaded)
- the G54 offset (20, 20, -120) is a placeholder

`a_max_diameter` and `probe_tool` are recorded for later steps; nothing enforces them yet.

| File | Used by |
|---|---|
| `printnc_provisional.json` | `camcore-verify --machine` |
| `printnc_sim.ini` | sim, mounted as `/work/machine.ini` |
| `printnc_sim.var` | sim G54 offset, mounted as `/work/machine.var` (must match `work_offset` in the JSON) |

Sim check against this profile (from the repo root, with the program in `out/`):

```bash
cp machine/printnc_sim.ini out/machine.ini && cp machine/printnc_sim.var out/machine.var
docker run --rm -v "$PWD/out:/work" camcore-sim check /work/sign.ngc
```

When the machine runs, IMPLEMENTATION_GUIDE step 4 replaces these files with an import of the real
LinuxCNC config.
