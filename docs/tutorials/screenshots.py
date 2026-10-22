"""Regenerates the tutorial screenshots in docs/tutorials/img/.

    python docs/tutorials/screenshots.py              # every picture (Docker for the sim ones), about 5 min
    python docs/tutorials/screenshots.py --no-sim     # without Docker: no sim pictures
    python docs/tutorials/screenshots.py --reuse      # keep check output already in out/tutorials
    python docs/tutorials/screenshots.py --only app_  # only pictures whose name starts with app_

Needs Google Chrome or Chromium (3D viewer and drawings, rendered headless with SwiftShader),
PySide6 (the app, grabbed offscreen) and, for the sim pictures, Docker with the camcore-sim image.
Run from the repository root with the conda environment active.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = Path(__file__).resolve().parent / "img"
OUT = ROOT / "out" / "tutorials"
EX = ROOT / "examples"

# job file -> output folder name; every one goes through `camcore check`
JOBS = {
    "sign": EX / "sign_job.json",
    "sign_medial": EX / "sign_medial_job.json",
    "cutout": EX / "cutout_job.json",
    "bracket": EX / "bracket_job.json",
    "adaptive": EX / "adaptive_job.json",
    "relief": EX / "relief_job.json",
    "wrap": EX / "wrap_job.json",
    "indexed": EX / "indexed_job.json",
    "pawn": EX / "pawn_job.json",
    "inlay_pocket": EX / "inlay_pocket_job.json",
    "inlay_plug": EX / "inlay_plug_job.json",
    "waves3d": OUT / "waves" / "waves3d_job.json",
    "waves_lam_sheet1": OUT / "waves" / "waves_lam_sheet1_job.json",
    "broken": OUT / "broken_job.json",
}

# ---------------------------------------------------------------------------------------------
# inputs


# the commands tutorial 11 shows (camcore relief, every answer given as a flag)
RELIEF_RUNS = [
    ["--name", "waves3d", "--mode", "3d", "--width", "100", "--height", "60",
     "--depth", "8", "--thickness", "12", "--frame", "5"],
    ["--name", "waves_lam", "--mode", "lamellas", "--width", "240", "--height", "160",
     "--thickness", "12", "--base", "10", "--relief", "25", "--board-width", "600", "--board-height", "400",
     "--interlock", "y"],
    ["--name", "waves_pins", "--mode", "pins", "--width", "240", "--height", "160",
     "--shape", "square", "--size", "20", "--gap", "2", "--base", "10", "--relief", "40", "--levels", "8"],
]


def relief_design():
    """Tutorial 11's designs into out/tutorials/waves; their jobs are checked again by run_checks."""
    for run in RELIEF_RUNS:
        subprocess.run([sys.executable, "-m", "camcore.cli", "relief", str(EX / "relief" / "waves.png"),
                        "-o", str(OUT / "waves"), "--yes", "--no-sim", "--no-open", *run],
                       cwd=ROOT, check=True, capture_output=True)


def broken_job():
    """The sign job with the mistake tutorial 13 fixes: safe_z below the stock top."""
    job = json.loads((EX / "sign_job.json").read_text())
    job["tool_library"] = str(EX / "tools.json")
    for s in job["shapes"].values():
        if "file" in s:
            s["file"] = str(EX / s["file"])
    job["defaults"]["safe_z"] = -1.0
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "broken_job.json").write_text(json.dumps(job, indent=2) + "\n")


# checked with the LinuxCNC sim: only the sign job, whose log the app's Report picture shows
# ("PASSED"); the rest are quick checks, which write the same viewer and sim GUI files
SIM_CHECKED = {"sign"}


def run_checks(sim: bool, reuse: bool):
    relief_design()
    broken_job()
    for name, job in JOBS.items():
        d = OUT / name
        if reuse and any(d.glob("*_view.html")):
            continue
        args = [sys.executable, "-m", "camcore.cli", "check", str(job), "-o", str(d)]
        if not (sim and name in SIM_CHECKED):
            args.append("--no-sim")
        print("check", name, flush=True)
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / "check.log").write_text(r.stdout + r.stderr)
        if r.returncode and name != "broken":
            sys.exit(f"camcore check {job} failed:\n{r.stdout}{r.stderr}")
    # a failed check writes no viewer: tutorial 13 looks at the program with camcore-verify
    d = OUT / "broken"
    (d / "stock.json").write_text('{"type": "box", "x": [0, 160], "y": [0, 90], "z": [-18, 0]}\n')
    subprocess.run([sys.executable, "-m", "camcore.cli", str(OUT / "broken_job.json"), "-o", str(d / "broken.ngc")],
                   cwd=ROOT, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "camcore.verify_cli", str(d / "broken.ngc"), "--machine",
                    str(ROOT / "machine" / "printnc_provisional.json"), "--tools", str(EX / "tools.json"),
                    "--stock", str(d / "stock.json"), "--view", str(d / "broken_view.html")],
                   cwd=ROOT, capture_output=True)


def view(name: str) -> Path:
    (v,) = (OUT / name).glob("*_view.html")
    return v

# ---------------------------------------------------------------------------------------------
# headless Chrome


def chrome() -> str:
    for c in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable"):
        if shutil.which(c):
            return shutil.which(c)
    sys.exit("needs Google Chrome or Chromium")


def chrome_shot(url: str, png: Path, w: int, h: int, budget: int = 6000):
    subprocess.run([chrome(), "--headless=new", "--no-sandbox", "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={w},{h}", f"--virtual-time-budget={budget}", f"--screenshot={png}", url],
                   check=True, capture_output=True)


# Synthetic pointer events cannot be captured, so capture is a no-op; `shot` drives the
# viewer's own orbit / zoom / pan handlers and controls.
PRELUDE = """<script>
Element.prototype.setPointerCapture = function () {};
window.shot = {
  canvas() { return document.querySelector("#stage canvas"); },
  zoom(f) { this.canvas().dispatchEvent(new WheelEvent("wheel", {deltaY: Math.log(f) * 1000, cancelable: true})); },
  drag(dx, dy, button) {
    const c = this.canvas(), o = {pointerId: 7, clientX: 0, clientY: 0, button: button || 0};
    c.dispatchEvent(new PointerEvent("pointerdown", o));
    c.dispatchEvent(new PointerEvent("pointermove", {...o, clientX: dx, clientY: dy}));
    c.dispatchEvent(new PointerEvent("pointerup", o));
  },
  orbit(dx, dy) { this.drag(dx, dy, 0); },
  pan(dx, dy) { this.drag(dx, dy, 2); },
  at(f) { const s = document.getElementById("scrub"); s.value = Math.round(f * 1000); s.dispatchEvent(new Event("input")); },
  hide(id) { document.getElementById(id).click(); },
};
</script>"""


def viewer_shot(html: Path, png: Path, actions: str = "", w: int = 1400, h: int = 850):
    """The 3D viewer after `actions` (JS using `shot`)."""
    text = PRELUDE + html.read_text()
    text +=f"<script>addEventListener('load', () => setTimeout(() => {{ {actions} }}, 200));</script>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=html.parent, delete=False) as fh:
        fh.write(text)
    try:
        chrome_shot(Path(fh.name).as_uri(), png, w, h)
    finally:
        os.unlink(fh.name)


def svg_shot(svg: Path, png: Path, w: int = 1400, h: int = 900):
    page = (f"<html><body style='margin:0;background:#fff;display:flex;align-items:center;justify-content:center;"
            f"height:100vh'><img src='{svg.as_uri()}' style='max-width:96vw;max-height:96vh'></body></html>")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(page)
    try:
        chrome_shot(Path(fh.name).as_uri(), png, w, h, budget=1000)
    finally:
        os.unlink(fh.name)


def shrink(png: Path):
    """Screenshots as 256-colour PNGs: a fraction of the size, no visible loss on UI pictures."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(png, optimize=True)

# ---------------------------------------------------------------------------------------------
# pictures


def toolpath_preview(name: str) -> Path:
    """`camcore JOB --preview`: the top-down SVG of the toolpaths."""
    d = OUT / f"{name}_gen"
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "camcore.cli", str(JOBS[name]), "-o", str(d / f"{name}.ngc"),
                    "--preview", str(d / f"{name}_preview.svg")], cwd=ROOT, check=True, capture_output=True)
    return d / f"{name}_preview.svg"


def viewer_pictures():
    v = viewer_shot
    return {
        "first_viewer": lambda p: v(view("sign"), p),
        "first_viewer_mid": lambda p: v(view("sign"), p, "shot.at(0.35); shot.zoom(0.75)"),
        "first_viewer_stock": lambda p: v(view("sign"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.7)"),
        "cutout_tabs": lambda p: v(view("cutout"), p, "shot.hide('trapids'); shot.hide('tstock'); shot.orbit(-75, -70); shot.zoom(0.38); shot.pan(0, 60)"),
        "bracket_viewer": lambda p: v(view("bracket"), p, "shot.hide('trapids'); shot.zoom(0.8)"),
        "vcarve_offset": lambda p: v(view("sign"), p, "shot.hide('trapids'); shot.orbit(-75, 50); shot.zoom(0.5); shot.pan(0, 250)"),
        "vcarve_medial": lambda p: v(view("sign_medial"), p, "shot.hide('trapids'); shot.orbit(-75, 50); shot.zoom(0.5); shot.pan(0, 250)"),
        "adaptive_path": lambda p: v(view("adaptive"), p, "shot.hide('trapids'); shot.orbit(0, 60); shot.zoom(0.75)"),
        "adaptive_result": lambda p: v(view("adaptive"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.8)"),
        "relief_viewer": lambda p: v(view("relief"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.6)"),
        "relief_paths": lambda p: v(view("relief"), p, "shot.at(0.6); shot.hide('trapids'); shot.zoom(0.6)"),
        "wrap_viewer": lambda p: v(view("wrap"), p),
        "indexed_viewer": lambda p: v(view("indexed"), p, "shot.hide('trapids'); shot.orbit(40, -20)"),
        "pawn_viewer": lambda p: v(view("pawn"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.8)"),
        "pawn_rough": lambda p: v(view("pawn"), p, "shot.at(0.12); shot.hide('trapids'); shot.zoom(0.8)"),
        "inlay_pocket_viewer": lambda p: v(view("inlay_pocket"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.7)"),
        "inlay_plug_viewer": lambda p: v(view("inlay_plug"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.7)"),
        "waves3d_viewer": lambda p: v(view("waves3d"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.zoom(0.75)"),
        "lamella_viewer": lambda p: v(view("waves_lam_sheet1"), p, "shot.hide('trapids'); shot.hide('tpath'); shot.orbit(-75, 50); shot.zoom(0.75); shot.pan(0, 150)"),
        "broken_viewer": lambda p: v(view("broken"), p, "document.querySelector('#ilist button').click(); shot.zoom(0.8)"),
        "bracket_preview": lambda p: svg_shot(toolpath_preview("bracket"), p, 760, 560),
        "lamella_assembly": lambda p: svg_shot(OUT / "waves" / "waves_lam_assembly.svg", p),
        "lamella_board": lambda p: svg_shot(OUT / "waves" / "waves_lam_board1.svg", p),
        "pins_plan": lambda p: svg_shot(OUT / "waves" / "waves_pins_pin_plan.svg", p),
    }


APP_SCRIPT = r'''
import os, sys, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["CAMCORE_GUI_NO_WEBENGINE"] = "1"
from pathlib import Path
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication
app = QApplication([])
from camcore.gui.main_window import MainWindow
from camcore.gui.wizard_dialog import WizardDialog
from camcore.gui.relief_dialog import ReliefDialog

spec = json.loads(sys.argv[1])

def settle():
    for _ in range(30):
        app.processEvents()

def viewer_rect(w):
    """Where the embedded viewer would be (below the caption row of the Viewer tab)."""
    pane = w.viewer
    top = pane.label.geometry().bottom() + 6
    tl = pane.mapTo(w, QPoint(0, top))
    return QRect(tl.x() + 1, tl.y(), pane.width() - 2, pane.height() - top - 1)

for shot in spec:
    if shot.get("relief"):
        d = ReliefDialog(Path(shot["relief"]), Path(shot["out"]))
        for key, value in shot.get("answers", {}).items():   # typed or picked as a user would
            w = d.widget(key)
            if hasattr(w, "setCurrentText"):
                w.setCurrentText(value); w.textActivated.emit(value)
            else:
                w.setText(value); w.textEdited.emit(value)
        d.resize(1150, 640)
        d.show(); settle()
        d.grab().save(shot["png"])
        continue
    if shot.get("wizard"):
        d = WizardDialog(Path(shot["wizard"]), Path(shot["out"]))
        d.resize(760, d.sizeHint().height())
        d.show(); settle()
        d.grab().save(shot["png"])
        continue
    w = MainWindow(shot["job"])
    w.resize(1400, 900)
    w.show()
    w.left.setCurrentIndex(shot.get("left", 0))
    if "op" in shot:
        w.ops_panel.list.setCurrentRow(shot["op"])
    if "shape" in shot:
        w.shapes_panel.list.setCurrentRow(shot["shape"])
    for key, value in shot.get("set", {}).items():          # op form parameter, typed as a user would
        (e,) = [f for p, f in w.ops_panel.scroll.widget().fields.items() if p.name == key]
        e.setText(value); e.textEdited.emit(value)
    if "report" in shot:
        w.report.setPlainText(Path(shot["report"]).read_text())
        w.right.setCurrentWidget(w.report)
    if "viewer" in shot:
        w.viewer.label.setText(shot["caption"])
        w.viewer.open_btn.setEnabled(True)
    settle()
    pix = w.grab()
    if "viewer" in shot:
        r = viewer_rect(w)
        print(json.dumps({"png": shot["png"], "x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}), flush=True)
    pix.save(shot["png"])
    w.project.dirty = w.tools_panel.dirty = False
    w.close()
# every picture is saved: skip Qt's teardown, which can crash after several windows and dialogs
sys.stdout.flush()
os._exit(0)
'''


def app_pictures(tmp: Path):
    """Grabs of the app (offscreen); the embedded viewer is pasted in from Chrome."""
    sign = str(EX / "sign_job.json")
    pv = "Preview (dry run only, not for the machine): "
    shots = [
        {"name": "app_overview", "job": sign, "op": 1, "viewer": "sign", "actions": "shot.zoom(1.5)",
         "caption": pv + str(view("sign"))},
        {"name": "app_report", "job": sign, "op": 0, "report": str(OUT / "sign" / "check.log")},
        {"name": "app_shapes", "job": sign, "left": 1, "shape": 2},
        {"name": "app_job", "job": sign, "left": 2},
        {"name": "app_tools", "job": sign, "left": 3},
        {"name": "app_problem", "job": sign, "op": 0, "set": {"depth": ""}},
        {"name": "app_pawn", "job": str(EX / "pawn_job.json"), "op": 1, "viewer": "pawn", "actions": "shot.zoom(1.3)",
         "caption": "Exported (sim passed): " + str(view("pawn"))},
        {"name": "app_relief", "relief": str(EX / "relief" / "waves.png"), "out": str(OUT / "relief_dialog"),
         "answers": {"mode": "3d", "width": "100", "height": "60", "depth": "8", "stock_thickness": "12", "frame": "5"}},
        {"name": "app_wizard", "wizard": str(EX / "pawn.stl"), "out": str(OUT / "wizard")},
    ]
    for s in shots:
        s["png"] = str(tmp / (s["name"] + ".png"))
    r = subprocess.run([sys.executable, "-c", APP_SCRIPT, json.dumps(shots)], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr)
    from PIL import Image
    pastes = {json.loads(line)["png"]: json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")}
    out = {}
    for s in shots:
        if s["png"] in pastes:
            p = pastes[s["png"]]
            vp = tmp / (s["name"] + "_viewer.png")
            viewer_shot(view(s["viewer"]), vp, s.get("actions", ""), p["w"], p["h"])
            im = Image.open(s["png"]).convert("RGB")
            im.paste(Image.open(vp).convert("RGB"), (p["x"], p["y"]))
            im.save(s["png"])
        out[s["name"]] = Path(s["png"])
    return out

# ---------------------------------------------------------------------------------------------
# the LinuxCNC sim GUI (AXIS + the vismach machine model) in Docker

SIM_RUN = r'''
import linuxcnc, time, sys
c, s = linuxcnc.command(), linuxcnc.stat()
def wait(cond, t=60):
    end = time.time() + t
    while time.time() < end:
        s.poll()
        if cond():
            return
        time.sleep(0.2)
    sys.exit("timeout")
c.state(linuxcnc.STATE_ESTOP_RESET); c.state(linuxcnc.STATE_ON); c.wait_complete()
c.teleop_enable(0); c.wait_complete()
c.home(-1); wait(lambda: all(s.homed[i] for i in range(s.joints)))
time.sleep(1)
c.mode(linuxcnc.MODE_AUTO); c.wait_complete()
c.auto(linuxcnc.AUTO_RUN, 0)
time.sleep(float(sys.argv[1]))
c.auto(linuxcnc.AUTO_PAUSE)
'''

# the image has no screenshot tool, but python-xlib
X_GRAB = r'''
from Xlib import X, display
root = display.Display(":1").screen().root
g = root.get_geometry()
open("/tmp/shot.raw", "wb").write(root.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xffffffff).data)
print(g.width, g.height)
'''


def sim_picture(name: str, png: Path, seconds: float, image: str = "camcore-sim"):
    d = OUT / name
    ngc = next(d.glob("*.ngc")).name
    cname = f"camcore-shot-{os.getpid()}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", cname, "-v", f"{d}:/work", image,
                    "gui", f"/work/{ngc}"], check=True, capture_output=True)
    try:
        def ex(*a, **k):
            return subprocess.run(["docker", "exec", cname, *a], capture_output=True, text=True, **k)
        for _ in range(120):
            if ex("sh", "-c", "pgrep -f camcore_vismach && halcmd getp joint.0.pos-fb").returncode == 0:
                break
            time.sleep(1)
        time.sleep(8)                                           # AXIS and vismach draw
        r = ex("python3", "-c", SIM_RUN, str(seconds), timeout=300)
        if r.returncode:
            sys.exit(f"sim run failed: {r.stdout}{r.stderr}")
        time.sleep(3)
        r = ex("python3", "-c", X_GRAB, check=True)
        w, h = map(int, r.stdout.split())
        raw = png.with_suffix(".raw")
        subprocess.run(["docker", "cp", f"{cname}:/tmp/shot.raw", str(raw)], check=True, capture_output=True)
        from PIL import Image
        im = Image.frombytes("RGB", (w, h), raw.read_bytes(), "raw", "BGRX")
        im.crop(im.getbbox()).save(png)                         # the empty black screen around the windows
        raw.unlink()
    finally:
        subprocess.run(["docker", "stop", cname], capture_output=True)


def sim_pictures():
    return {
        "sim_sign": lambda p: sim_picture("sign", p, 25),
        "sim_pawn": lambda p: sim_picture("pawn", p, 40),
    }

# ---------------------------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-sim", action="store_true", help="no LinuxCNC sim at all (no Docker): no sim pictures")
    ap.add_argument("--reuse", action="store_true", help="keep check output already in out/tutorials")
    ap.add_argument("--only", default="", help="only pictures whose name starts with this")
    args = ap.parse_args(argv)
    run_checks(not args.no_sim, args.reuse)
    IMG.mkdir(exist_ok=True)
    jobs = dict(viewer_pictures())
    if not args.no_sim:
        jobs.update(sim_pictures())
    with tempfile.TemporaryDirectory() as tmp:
        if "app_".startswith(args.only) or args.only.startswith("app_"):
            for name, src in app_pictures(Path(tmp)).items():
                if name.startswith(args.only):
                    shutil.copy(src, IMG / f"{name}.png")
                    shrink(IMG / f"{name}.png")
                    print("wrote", IMG / f"{name}.png")
        for name, make in jobs.items():
            if name.startswith(args.only):
                make(IMG / f"{name}.png")
                shrink(IMG / f"{name}.png")
                print("wrote", IMG / f"{name}.png", flush=True)


if __name__ == "__main__":
    main()
