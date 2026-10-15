#!/usr/bin/env python3
"""Vismach model of the machine for the AXIS GUI of the sim.

Draws the scene `camcore check` writes next to each program (NAME_scene.json,
made by camcore/body.py `scene()` from the machine JSON's "body"), so what you
see here is exactly what camcore-verify checked the program against: the
fixed obstacles (spoilboard, ATC rack, tool setter, the rotary unit on rotary
jobs), the gantry beam, the spindle with the current tool, and the stock.

    camcore_vismach.py /work/NAME_scene.json
    camcore_vismach.py --setup /work/NAME_scene.json DIR   (before LinuxCNC starts)

--setup writes the INI, G54 parameter file and tool table the program was
checked with (embedded in the scene by camcore check) into DIR as
camcore_sim.ini, sim.var and tool.tbl, so the GUI runs what the check ran.

Positions come straight from LinuxCNC's pins (hal.get_value), so it needs no
extra HAL nets: joint.N.pos-fb is the tool tip in machine coordinates (the
sim's tool table has no length offsets) and iocontrol.0.tool-number the tool
in the spindle, whose gauge length puts the spindle nose above the tip.
Vismach only draws; collisions are found by camcore-verify.
"""
import json
import sys

COLORS = {"fixed": (0.55, 0.55, 0.6, 1), "gantry": (0.35, 0.35, 0.4, 1),
          "spindle": (0.8, 0.8, 0.85, 1), "tool": (0.95, 0.75, 0.2, 1), "stock": (0.75, 0.55, 0.35, 1)}


class Pins:
    """What HalTranslate / HalRotate read with comp[var]: LinuxCNC's own pins."""

    def __init__(self, scene, get_value):
        self.get = get_value
        self.tools = {int(k): v for k, v in scene["tools"].items()}
        self.default = {"length": scene["default_tool_length"], "diameter": 3.0}

    def tool(self):
        return self.tools.get(int(self.get("iocontrol.0.tool-number")), self.default)

    def __getitem__(self, var):
        if var == "nose":
            return self.get("joint.2.pos-fb") + self.tool()["length"]
        return self.get({"x": "joint.0.pos-fb", "y": "joint.1.pos-fb", "z": "joint.2.pos-fb",
                         "a": "joint.3.pos-fb"}[var])


def solid(v, p):
    """A vismach primitive for a scene part (box, or the cylinder inscribed in it)."""
    (x0, y0, z0), (x1, y1, z1) = p["lo"], p["hi"]
    if p["kind"] == "box":
        return v.Box(x0, y0, z0, x1, y1, z1)
    if p["kind"] == "cyl_z":
        r = (x1 - x0) / 2
        return v.Translate([v.CylinderZ(z0, r, z1, r)], (x0 + x1) / 2, (y0 + y1) / 2, 0)
    r = (y1 - y0) / 2
    return v.Translate([v.CylinderX(x0, r, x1, r)], 0, (y0 + y1) / 2, (z0 + z1) / 2)


def build(scene, v, pins):
    """(model, tool capture, work capture) for vismach.main. `v` is the vismach module."""

    class ToolBody(v.CylinderZ):
        """The cutter in the spindle: a cylinder of its diameter and gauge length."""

        def coords(self):
            t = pins.tool()
            r = t["diameter"] / 2
            return [0, r, t["length"], r]

    def group(frame):
        parts = [solid(v, p) for p in scene["parts"] if p["frame"] == frame]
        return v.Color(COLORS[frame], parts) if parts else None

    fixed, gantry, spindle, stock = (group(f) for f in ("fixed", "gantry", "spindle", "stock"))
    tool_capture, work_capture = v.Capture(), v.Capture()
    tip = v.Color(COLORS["tool"], [ToolBody(0, 0, 0, 0), tool_capture])
    head = [v.HalTranslate([spindle], pins, "nose", 0, 0, 1)] if spindle else []
    head.append(v.HalTranslate([tip], pins, "z", 0, 0, 1))
    carriage = v.HalTranslate([v.HalTranslate(head, pins, "y", 0, 1, 0)], pins, "x", 1, 0, 0)
    model = [m for m in (fixed, carriage, work_capture) if m is not None]
    if gantry:
        model.append(v.HalTranslate([gantry], pins, "y", 0, 1, 0))
    if stock:
        axis = scene.get("rotary_axis")
        if axis:       # the part turns by -A about the rotary axis (ISO: +A turns it the other way)
            ay, az = axis
            stock = v.Translate([v.HalRotate([v.Translate([stock], 0, -ay, -az)], pins, "a", -1, 1, 0, 0)],
                                0, ay, az)
        model.append(stock)
    return v.Collection(model), tool_capture, work_capture


def setup(scene_path, directory):
    sim = json.loads(open(scene_path).read()).get("sim")
    if not sim:
        return
    for name, key in (("camcore_sim.ini", "ini"), ("sim.var", "var"), ("tool.tbl", "tool_tbl")):
        with open(f"{directory}/{name}", "w") as f:
            f.write(sim[key])


def main(argv):
    if argv[1] == "--setup":
        return setup(argv[2], argv[3])
    import tkinter

    import hal
    import vismach

    scene = json.loads(open(argv[1]).read())
    model, tool, work = build(scene, vismach, Pins(scene, hal.get_value))
    tk = tkinter.Tk

    def placed(*args, **kw):
        """The sim's X display has no window manager: put the window right of AXIS."""
        app = tk(*args, **kw)
        app.title("camcore machine model")
        app.geometry("850x980+745+10")
        return app

    tkinter.Tk = placed
    vismach.main(model, tool, work, size=1500, lat=-60, lon=25)


if __name__ == "__main__":
    main(sys.argv)
