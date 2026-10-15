"""Write a LinuxCNC tool table (.tbl) from a camcore tool library."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def to_tbl(library: dict) -> str:
    rows = []
    for name, t in sorted(library.items(), key=lambda kv: kv[1].get("number", 0)):
        n = t.get("number")
        if not n:
            continue
        rows.append(f"T{n} P{n} Z{t.get('length', 0):.4f} D{t['diameter']:.4f} ;{name}")
    return "\n".join(rows) + "\n"


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        sys.exit("usage: python -m camcore.tooltable tools.json tool.tbl")
    Path(argv[1]).write_text(to_tbl(json.loads(Path(argv[0]).read_text())))


if __name__ == "__main__":
    main()
