"""camcore check JOB.json: the one gate every program goes through.

Generates the G-code, dry-runs it with camcore-verify (machine JSON + the job's
stock), runs it through LinuxCNC in the sim container, writes the viewer, and
only then writes the .ngc to the output folder. If anything fails, the output
folder gets a report and nothing else; an older .ngc or viewer of the same job
is deleted, so a stale program can never be taken by mistake.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .body import scene
from .job import load_job, run_job
from .post import job_post
from .tooltable import to_tbl
from .verify import Machine, machine_from_library, run

DEFAULT_MACHINE = Path(__file__).resolve().parent.parent / "machine" / "printnc_provisional.json"
SIM_IMAGE = "camcore-sim"
SIM_TIMEOUT = 3 * 3600                        # s
PROGRESS = re.compile(r"CAMCORE-PROGRESS line (\d+)\s*$")   # printed by sim/run_check.py every 2 s


def job_library(job: dict, base: Path) -> dict:
    """The job's tool library as raw dicts (library file plus inline tools)."""
    lib = {}
    if "tool_library" in job:
        lib.update(json.loads((base / job["tool_library"]).read_text()))
    lib.update(job.get("tools", {}))
    return lib


def sim_var(work_offset: dict) -> str:
    """LinuxCNC parameter file with G54 (5221..5224 = X Y Z A) set from the machine JSON."""
    vals = {5220: 1.0, 5221: work_offset["x"], 5222: work_offset["y"], 5223: work_offset["z"],
            5224: work_offset.get("a", 0.0)}
    return "".join(f"{k}\t{v:.6f}\n" for k, v in vals.items())


SIM_MAX_VELOCITY = 30000.0        # mm/s (deg/s for A): floor for the check's sim only
SIM_MAX_ACCELERATION = 1e6        # mm/s^2 (deg/s^2)
_SPEED_KEY = re.compile(r"^(\s*)(MAX_(?:LINEAR_|ANGULAR_)?(VELOCITY|ACCELERATION))(\s*=\s*)([-+0-9.eE]+)(.*)$")


def fast_sim_ini(text: str) -> str:
    """The sim INI for `camcore check`: every axis, joint and trajectory speed and
    acceleration limit raised to at least SIM_MAX_VELOCITY / SIM_MAX_ACCELERATION.

    The check asks whether LinuxCNC accepts and runs the whole program (soft limits,
    interpreter and motion errors). LinuxCNC slows every move down to meet these
    limits, so they only decide how long the sim takes: with the PrintNC's real ones
    the sign example took 187 s, with these 28 s. The sim GUI keeps the real INI."""
    section, out = "", []
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].upper()
        body = line.rstrip("\r\n")
        m = _SPEED_KEY.match(body)
        if m and (section in ("TRAJ", "DISPLAY") or section.startswith(("AXIS_", "JOINT_"))):
            floor = SIM_MAX_VELOCITY if m[3] == "VELOCITY" else SIM_MAX_ACCELERATION
            value = max(float(m[5]), floor)
            text_value = f"{value:.0f}" if value.is_integer() else repr(value)
            line = f"{m[1]}{m[2]}{m[4]}{text_value}{m[6]}{line[len(body):]}"
        out.append(line)
    return "".join(out)


def sim_tool_table(mdata: dict, machine_path: Path, library: dict) -> str:
    """The controller's real tool.tbl if the machine names one (as on the machine), else one from the library."""
    if mdata.get("tool_table"):
        return (machine_path.parent / mdata["tool_table"]).read_text()
    return to_tbl(library)


def run_sim(gcode: str, mdata: dict, machine_path: Path, library: dict, image: str,
            work_offset: dict | None = None, progress=None) -> tuple[bool, str]:
    """Run the program through LinuxCNC in the sim container, with G54 at `work_offset`
    (default: the machine JSON's). Returns (ok, log). `progress(line)` is called with
    the program line the sim has reached as it runs; those lines stay out of the log."""
    if shutil.which("docker") is None:
        return False, "docker not found (install it, or use --no-sim for a quick iteration)"
    sim = mdata.get("sim")
    if not sim or "ini" not in sim:
        return False, f"{machine_path} has no \"sim\": {{\"ini\": ...}} entry"
    with tempfile.TemporaryDirectory(prefix="camcore-sim-") as tmp:
        work = Path(tmp)
        work.chmod(0o755)                     # the container user must be able to read it
        (work / "program.ngc").write_text(gcode)
        (work / "machine.ini").write_text(fast_sim_ini((machine_path.parent / sim["ini"]).read_text()))
        (work / "machine.var").write_text(sim_var(work_offset or mdata["work_offset"]))
        (work / "tool.tbl").write_text(sim_tool_table(mdata, machine_path, library))
        p = subprocess.Popen(["docker", "run", "--rm", "-v", f"{work}:/work", image, "check", "/work/program.ngc"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        timed_out = threading.Event()
        timer = threading.Timer(SIM_TIMEOUT, lambda: (timed_out.set(), p.kill()))
        timer.start()
        out = []
        try:
            for line in p.stdout:
                m = PROGRESS.match(line)
                if not m:
                    out.append(line)
                elif progress is not None:
                    progress(int(m[1]))
            p.wait()
        finally:
            timer.cancel()
        if timed_out.is_set():
            return False, "LinuxCNC sim timed out after 3 h"
    log = "".join(out).strip()
    if "Unable to find image" in log or "pull access denied" in log:
        log += f"\n\nBuild the sim image first: cd sim && docker build -t {image} ."
    return p.returncode == 0, log


def check(job_path, out_dir="out", machine_path=None, sim: bool = True, image: str = SIM_IMAGE,
          log=print, progress=None) -> bool:
    """`progress(line, total)`, if given, follows the LinuxCNC sim through the program."""
    job_path = Path(job_path)
    name = job_path.stem.removesuffix("_job")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ngc, view, report = out / f"{name}.ngc", out / f"{name}_view.html", out / f"{name}_report.txt"
    scene_file = out / f"{name}_scene.json"
    for f in (ngc, view, scene_file):         # never leave an older program next to a new report
        f.unlink(missing_ok=True)

    lines = [f"camcore check {job_path}"]
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        lines.append(f"FAILED: {msg}")

    job, base = load_job(job_path)
    if machine_path is None:
        machine_path = base / job["machine"] if "machine" in job else DEFAULT_MACHINE
    machine_path = Path(machine_path)
    mdata = json.loads(machine_path.read_text())
    library = job_library(job, base)
    lines.append(f"machine: {machine_path}")

    gcode = motions = stock_sim = issues = None
    machine = machine_from_library(Machine.load(machine_path), library)
    from . import verify
    if verify.KERNEL is None:
        log("note: verify runs in pure Python, several times slower (CAMCORE_VERIFY_PYTHON is set, or "
            "camcore_verify_kernel is not installed: pip install ./rust/verify_kernel)")
    try:
        paths = run_job(job, base)
        gcode = job_post(job).emit(paths)
        lines += [f"note: {n}" for tp in paths for n in tp.notes]
    except Exception as e:                    # noqa: BLE001 - any failure here must end in a report
        fail(f"G-code generation: {type(e).__name__}: {e}")

    stock = job.get("stock")
    if gcode is not None and stock is None:
        fail("the job has no \"stock\"; camcore-verify needs it to find crashes")
    if gcode is not None and stock is not None:
        issues, motions, stock_sim = run(gcode, machine, stock)
        errors = [i for i in issues if i.level == "error"]
        lines.append(f"verify: {len(motions)} moves, {len(errors)} errors, {len(issues) - len(errors)} warnings")
        lines += [f"  {i}" for i in issues]
        if errors:
            fail("camcore-verify found errors")

    if ok and any(op.get("op") == "inlay" and op.get("fit_with") for op in job.get("operations", [])):
        from .inlay_fit import fit_for
        log("checking the inlay fit against the pocket job...")
        try:
            result = fit_for(job_path, job, base, machine_path)
            lines.append("inlay fit: " + ("OK" if result.ok else "FAILED"))
            lines += [f"  {s}" for s in result.lines()]
            if not result.ok:
                fail("the inlay plug does not fit its pocket")
        except Exception as e:            # noqa: BLE001 - any failure here must end in a report
            fail(f"inlay fit: {type(e).__name__}: {e}")

    if ok and sim:
        log("running the LinuxCNC sim (about a minute for ~10000 moves)...")
        total = len(gcode.splitlines())
        sim_ok, sim_log = run_sim(gcode, mdata, machine_path, library, image, machine.offset_for(stock),
                                  progress=(lambda n: progress(n, total)) if progress else None)
        lines.append("LinuxCNC sim: " + ("OK" if sim_ok else "FAILED"))
        lines += [f"  {s}" for s in sim_log.splitlines()]
        if not sim_ok:
            fail("LinuxCNC sim")
    elif ok:
        lines.append("LinuxCNC sim: SKIPPED (--no-sim). Do not run this program on the machine.")

    if ok:
        from .viewer import write_viewer
        write_viewer(view, motions, machine, stock, stock_sim, issues, title=ngc.name, program_text=gcode)
        if machine.body is not None:           # the sim GUI's vismach model (sim/camcore_vismach.py)
            offset = machine.offset_for(stock)
            data = scene(machine.body, offset, stock, machine.tools)
            if mdata.get("sim", {}).get("ini"):   # so the GUI runs the same LinuxCNC setup as the check
                data["sim"] = {"ini": (machine_path.parent / mdata["sim"]["ini"]).read_text(),
                               "var": sim_var(offset), "tool_tbl": sim_tool_table(mdata, machine_path, library)}
            scene_file.write_text(json.dumps(data))
            lines.append(f"wrote {scene_file}")
        ngc.write_text(gcode)                 # last: the .ngc only exists if everything above passed
        lines += [f"wrote {ngc}", f"wrote {view}"]
        lines.append("PASSED" if sim else "PASSED WITHOUT SIM")
    report.write_text("\n".join(lines) + "\n")
    for s in lines:
        log(s)
    log(f"report: {report}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="camcore check",
                                 description="Generate, verify and sim-check a job; write the .ngc only if all pass")
    ap.add_argument("job")
    ap.add_argument("-o", "--out-dir", default="out", help="output folder (default: out)")
    ap.add_argument("--machine", help="machine JSON (default: the job's \"machine\", else "
                                      "machine/printnc_provisional.json)")
    ap.add_argument("--no-sim", action="store_true", help="skip the LinuxCNC sim for a quick iteration")
    ap.add_argument("--sim-image", default=SIM_IMAGE, help=f"sim Docker image (default: {SIM_IMAGE})")
    args = ap.parse_args(argv)
    ok = check(args.job, args.out_dir, args.machine, sim=not args.no_sim, image=args.sim_image)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
