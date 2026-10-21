"""Writes the relief examples (relief plan): waves.png, Reliefwerk's built-in
sample at 240 x 160 px, and from it waves3d_job.json + waves3d.stl, a 3D relief
of 100 x 60 mm, 8 mm deep, inside a 5 mm frame, out of a 12 mm board.

python examples/relief/make_example.py
"""
from pathlib import Path

import numpy as np
from PIL import Image

from camcore.relief import heightfield as hf
from camcore.relief.relief3d import Relief3D, write_job

HERE = Path(__file__).parent
WAVES = dict(width=100, height=60, blur=1.0)
WAVES3D = Relief3D(depth=8, thickness=12, frame=5)


def waves_png(path):
    Image.fromarray(np.round(hf.sample_relief(240, 160) * 255).astype(np.uint8)).save(path)


def main(out=HERE):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    waves_png(out / "waves.png")
    F = hf.prepare(hf.load(out / "waves.png"), **WAVES)
    return write_job(F, WAVES3D, out, "waves3d", HERE.parent / "tools.json",
                     source={"file": "waves.png", **WAVES})


if __name__ == "__main__":
    print(main())
