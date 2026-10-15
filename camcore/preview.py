"""Top-down SVG preview of toolpaths: cuts shaded by depth, rapids dashed."""
from __future__ import annotations

from .toolpath import Toolpath


def to_svg(paths: list[Toolpath], margin: float = 5.0, show_rapids: bool = False) -> str:
    segs = []   # (x0, y0, x1, y1, z, kind)
    for tp in paths:
        pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        for m in tp.moves:
            new = {a: getattr(m, a) if getattr(m, a) is not None else pos[a] for a in "xyz"}
            if (new["x"], new["y"]) != (pos["x"], pos["y"]):
                segs.append((pos["x"], pos["y"], new["x"], new["y"], min(pos["z"], new["z"]), m.kind))
            pos = new
    cuts = [s for s in segs if s[5] == "feed"]
    xs = [v for s in cuts for v in (s[0], s[2])]
    ys = [v for s in cuts for v in (s[1], s[3])]
    zmin = min(s[4] for s in cuts)
    x0, y0 = min(xs) - margin, min(ys) - margin
    w, h = max(xs) - x0 + margin, max(ys) - y0 + margin
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {h:.2f}" '
           f'width="{w*6:.0f}" height="{h*6:.0f}" style="background:#f4efe6">']
    for sx0, sy0, sx1, sy1, z, kind in segs:
        if kind == "rapid" and not show_rapids:
            continue
        t = min(1.0, z / zmin) if zmin < 0 else 0
        color = "#d33" if kind == "rapid" else f"rgb({int(200-170*t)},{int(150-120*t)},{int(90-70*t)})"
        dash = ' stroke-dasharray="1,1"' if kind == "rapid" else ""
        out.append(f'<line x1="{sx0-x0:.3f}" y1="{h-(sy0-y0):.3f}" x2="{sx1-x0:.3f}" '
                   f'y2="{h-(sy1-y0):.3f}" stroke="{color}" stroke-width="0.15"{dash}/>')
    out.append("</svg>")
    return "\n".join(out)
