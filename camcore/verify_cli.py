"""camcore-verify PROGRAM.ngc --machine machine.json [--tools tools.json] [--stock stock.json] [--view out.html]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .verify import Machine, machine_from_library, run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="camcore-verify", description="Dry-run G-code against a virtual machine and stock")
    ap.add_argument("program")
    ap.add_argument("--machine", required=True, help="machine JSON (travel, work offset, tool table, stock)")
    ap.add_argument("--tools", help="camcore tool library to fill the tool table from")
    ap.add_argument("--stock", help="stock JSON, overrides the machine file's stock")
    ap.add_argument("--res", type=float, default=0.5, help="simulation grid in mm")
    ap.add_argument("--view", help="write an animated 3D viewer HTML")
    ap.add_argument("--title", help="viewer title (default: program file name)")
    ap.add_argument("--cdn", action="store_true", help="load three.js from a CDN instead of embedding it")
    args = ap.parse_args(argv)

    mdata = json.loads(Path(args.machine).read_text())
    machine = Machine.load(args.machine)
    if args.tools:
        machine_from_library(machine, args.tools)
    stock = json.loads(Path(args.stock).read_text()) if args.stock else mdata.get("stock")
    text = Path(args.program).read_text()
    issues, motions, sim = run(text, machine, stock, args.res)

    errors = [i for i in issues if i.level == "error"]
    for i in issues:
        print(i)
    print(f"{len(motions)} moves, {len(errors)} errors, {len(issues) - len(errors)} warnings")
    if args.view:
        from .viewer import write_viewer
        write_viewer(args.view, motions, machine, stock, sim, issues, title=args.title or Path(args.program).name,
                     program_text=text, inline_three=not args.cdn)
        print(f"viewer written to {args.view}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
