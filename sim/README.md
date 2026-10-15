# LinuxCNC simulator in a container

Runs your program through real LinuxCNC (2.9, Debian bookworm package) on a simulated
XYZA machine with an automatic tool changer, so LinuxCNC's own interpreter checks soft
limits, tool numbers and G-code rules before anything reaches the real machine.

Status: written but not yet run, because the cloud session that built it cannot reach the Debian
package servers. First build on your PC may need small fixes.

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
