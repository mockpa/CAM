"""Command line: camcore JOB.json [-o out.nc], or camcore check JOB.json (see check.py)"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .job import load_job, run_job
from .post import job_post


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["check"]:
        from .check import main as check_main
        sys.exit(check_main(argv[1:]))
    ap = argparse.ArgumentParser(prog="camcore", description="Generate G-code from a JSON job")
    ap.add_argument("job")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--post", help="override the job's post-processor")
    ap.add_argument("--preview", help="write a top-down SVG preview of the toolpaths")
    args = ap.parse_args(argv)

    job, base = load_job(args.job)
    paths = run_job(job, base)
    gcode = job_post(job, args.post).emit(paths)

    for tp in paths:
        f, r = tp.length()
        print(f"{tp.name:<16} tool={tp.tool.name:<10} moves={len(tp.moves):<6} "
              f"cut={f/1000:.2f} m rapid={r/1000:.2f} m", file=sys.stderr)
    for out in (args.output, args.preview):
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
    if args.preview:
        from .preview import to_svg
        with open(args.preview, "w") as fh:
            fh.write(to_svg(paths))
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(gcode)
    else:
        sys.stdout.write(gcode)


if __name__ == "__main__":
    main()
