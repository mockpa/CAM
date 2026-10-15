"""Animated 3D viewer: one self-contained HTML file that plays a G-code program
with live material removal, A-axis rotation, tool changes and check results."""
from __future__ import annotations

import bisect
import html
import json
from pathlib import Path

HERE = Path(__file__).parent
CDN_THREE = '<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"></script>'


def build_data(motions, machine, stock, issues, program_text: str, title: str) -> dict:
    src = program_text.splitlines()
    r = lambda v: round(v, 4)
    moves = [[1 if m.kind == "feed" else 0, r(m.end[0]), r(m.end[1]), r(m.end[2]), r(m.end[3]),
              m.tool, m.line, round(m.seconds, 5), 1 if m.spindle else 0] for m in motions]
    lines_of = [m.line for m in motions]
    out_issues = []
    for i in issues:
        idx = bisect.bisect_right(lines_of, i.line) - 1
        out_issues.append({"level": i.level, "line": i.line, "message": i.message, "move": idx})
    return {
        "title": title,
        "stock": stock,
        "tools": {str(k): {kk: v for kk, v in t.items() if kk in ("name", "diameter", "kind", "v_angle", "flute_length")}
                  for k, t in machine.tools.items()},
        "moves": moves,
        "lines": {str(m.line): src[m.line - 1].strip() for m in motions},
        "issues": out_issues,
    }


def render(data: dict, inline_three: bool = True) -> str:
    tpl = (HERE / "viewer_template.html").read_text()
    if inline_three:
        three = "<script>" + (HERE / "vendor" / "three.min.js").read_text() + "</script>"
    else:
        three = CDN_THREE
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return (tpl.replace("__TITLE__", html.escape(data["title"]))
               .replace("__THREE_SCRIPT__", three)
               .replace("__DATA__", payload))


def write_viewer(path, motions, machine, stock, sim, issues, title: str, program_text: str | None = None,
                 inline_three: bool = True):
    if program_text is None:
        raise ValueError("program_text is required")
    data = build_data(motions, machine, stock, issues, program_text, title)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(render(data, inline_three))
