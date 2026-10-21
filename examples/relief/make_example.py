"""Writes the relief examples (relief plan): waves.png, Reliefwerk's built-in
sample at 240 x 160 px, and from it waves3d_job.json + waves3d.stl, a 3D relief
of 100 x 60 mm, 8 mm deep, inside a 5 mm frame, out of a 12 mm board, and
waves_lam_sheet1_job.json, the same image as 240 x 160 mm of lamellas 12 mm thick
(10 mm base + 25 mm relief), interlocked on a 600 x 400 mm board.

python examples/relief/make_example.py
"""
from pathlib import Path

import numpy as np
from PIL import Image

from camcore.relief import heightfield as hf
from camcore.relief.lamella import LamellaSettings
from camcore.relief.lamella_job import SheetSettings, write_jobs
from camcore.relief.relief3d import Relief3D, write_job

HERE = Path(__file__).parent
WAVES = dict(width=100, height=60, blur=1.0)
WAVES3D = Relief3D(depth=8, thickness=12, frame=5)
WAVES_LAM = dict(width=240, height=160, blur=1.0)
LAM = LamellaSettings(thickness=12, base=10, relief=25, step=1)
SHEET = SheetSettings(width=600, height=400, interlock=True)


def waves_png(path):
    Image.fromarray(np.round(hf.sample_relief(240, 160) * 255).astype(np.uint8)).save(path)


def main(out=HERE):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    waves_png(out / "waves.png")
    F = hf.prepare(hf.load(out / "waves.png"), **WAVES)
    job3d = write_job(F, WAVES3D, out, "waves3d", HERE.parent / "tools.json",
                      source={"file": "waves.png", **WAVES})
    F = hf.prepare(hf.load(out / "waves.png"), **WAVES_LAM)
    sheets = write_jobs(F, LAM, SHEET, out, "waves_lam", HERE.parent / "tools.json",
                        source={"file": "waves.png", **WAVES_LAM})
    return [job3d, *sheets]


if __name__ == "__main__":
    print(main())
