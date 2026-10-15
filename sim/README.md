# LinuxCNC simulator in a container

Runs your program through real LinuxCNC (2.9.4, Debian trixie package `1:2.9.4-2+deb13u1`) on a
simulated XYZA machine with an automatic tool changer, so LinuxCNC's own interpreter checks soft
limits, tool numbers and G-code rules before anything reaches the real machine.

## Verified (2026-10-15, LinuxCNC 2.9.4)
- `examples/sign.ngc` and `examples/wrap.ngc` run to the end with exit code 0 (about 55 s and 80 s).
- A copy of `sign.ngc` with `G0 X1580` (1000 mm past X max) fails:
  `Linear move on line 13 would exceed X's positive limit`.
- A copy with `T99 M6` fails: `Requested tool 99 not found in the tool table`.

Fixes it needed on first build: Debian trixie instead of bookworm (bookworm only has a 2.9.0~pre1
snapshot), setuid bit removed from `rtapi_app` (Docker drops `CAP_IPC_OWNER`, so the root
`rtapi_app` could not open HAL shared memory), `EMCIO = io` in the INI (without it E-stop reset
never completes), and `run_check.py` waits for LinuxCNC from child processes before importing
`linuxcnc`.

## Build once
```bash
cd sim
python -m camcore.tooltable ../examples/tools.json config/tool.tbl   # tool table from your library
docker build -t camcore-sim .
```

## Headless check (CI style)
```bash
docker run --rm -v "$PWD/../examples:/work" camcore-sim check /work/sign.ngc
```
Exit code 0 means LinuxCNC ran the whole program with no error. Feed override is raised to 5000 % so it finishes fast.

## Watch it in the AXIS GUI
```bash
docker run --rm -p 6080:6080 -v "$PWD/../examples:/work" camcore-sim gui /work/wrap.ngc
```
Open http://localhost:6080/vnc.html, then use AXIS as normal (F1, F2, Home All, Run).

## Make it match your machine
- Copy your real axis limits into `config/camcore_sim.ini` (or mount your own INI as `/work/machine.ini`).
- Set your G54 work offset in `config/sim.var` (parameters 5221 to 5224 = X, Y, Z, A).
- Put your real tool table in `/work/tool.tbl` to check against it.
