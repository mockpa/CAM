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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .job import load_job, run_job
from .post import job_post
from .tooltable import to_tbl
from .verify import Machine, machine_from_library, run

DEFAULT_MACHINE = Path(__file__).resolve().parent.parent / "machine" / "printnc_provisional.json"
SIM_IMAGE = "camcore-sim"


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


def run_sim(gcode: str, mdata: dict, machine_path: Path, library: dict, image: str) -> tuple[bool, str]:
    """Run the program through LinuxCNC in the sim container. Returns (ok, log)."""
    if shutil.which("docker") is None:
        return False, "docker not found (install it, or use --no-sim for a quick iteration)"
    sim = mdata.get("sim")
    if not sim or "ini" not in sim:
        return False, f"{machine_path} has no \"sim\": {{\"ini\": ...}} entry"
    with tempfile.TemporaryDirectory(prefix="camcore-sim-") as tmp:
        work = Path(tmp)
        work.chmod(0o755)                     # the container user must be able to read it
        (work / "program.ngc").write_text(gcode)
        shutil.copy(machine_path.parent / sim["ini"], work / "machine.ini")
        (work / "machine.var").write_text(sim_var(mdata["work_offset"]))
        if mdata.get("tool_table"):           # the controller's real table, as on the machine
            shutil.copy(machine_path.parent / mdata["tool_table"], work / "tool.tbl")
        else:
            (work / "tool.tbl").write_text(to_tbl(library))
        try:
            p = subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work", image,
                                "check", "/work/program.ngc"],
                               capture_output=True, text=True, timeout=3 * 3600)
        except subprocess.TimeoutExpired:
            return False, "LinuxCNC sim timed out after 3 h"
    log = (p.stdout + p.stderr).strip()
    if "Unable to find image" in log or "pull access denied" in log:
        log += f"\n\nBuild the sim image first: cd sim && docker build -t {image} ."
    return p.returncode == 0, log


def check(job_path, out_dir="out", machine_path=None, sim: bool = True, image: str = SIM_IMAGE,
          log=print) -> bool:
    job_path = Path(job_path)
    name = job_path.stem.removesuffix("_job")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ngc, view, report = out / f"{name}.ngc", out / f"{name}_view.html", out / f"{name}_report.txt"
    for f in (ngc, view):                     # never leave an older program next to a new report
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
        machine = machine_from_library(Machine.load(machine_path), library)
        issues, motions, stock_sim = run(gcode, machine, stock)
        errors = [i for i in issues if i.level == "error"]
        lines.append(f"verify: {len(motions)} moves, {len(errors)} errors, {len(issues) - len(errors)} warnings")
        lines += [f"  {i}" for i in issues]
        if errors:
            fail("camcore-verify found errors")

    if ok and sim:
        log("running the LinuxCNC sim (a few minutes for ~10000 moves)...")
        sim_ok, sim_log = run_sim(gcode, mdata, machine_path, library, image)
        lines.append("LinuxCNC sim: " + ("OK" if sim_ok else "FAILED"))
        lines += [f"  {s}" for s in sim_log.splitlines()]
        if not sim_ok:
            fail("LinuxCNC sim")
    elif ok:
        lines.append("LinuxCNC sim: SKIPPED (--no-sim). Do not run this program on the machine.")

    if ok:
        from .viewer import write_viewer
        write_viewer(view, motions, machine, stock, stock_sim, issues, title=ngc.name, program_text=gcode)
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
