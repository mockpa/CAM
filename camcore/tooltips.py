"""Tooltip texts for the desktop app: what each job key, operation parameter and
tool field means. Plain data (no Qt), so tests can check that every parameter
of every operation has one. Units: mm and degrees, as everywhere.

PARAM_TIPS is keyed by parameter name; "op.param" overrides it for one
operation where the same name means something else there."""
from __future__ import annotations

OP_TIPS = {
    "pocket": "Clear the inside of a shape down to a flat floor",
    "profile": "Cut along a shape's outline: outside to cut a part free, inside for a hole, or on the line",
    "vcarve": "V-bit carving: the groove gets deeper where the shape is wider; with a clear tool, flat-bottomed",
    "inlay": "One part of a V-carved inlay: the pocket, or the mirrored plug that is glued into it",
    "drill": "Drill holes at a list of points, optionally pecking",
    "rotary_rough": "Rough an STL model out of round stock on the A axis, level by level",
    "rotary_finish": "Finish an STL model on the A axis (helical, A raster or X raster)",
    "surface_rough": "Rough an STL model out of a block (3 axes), level by level",
    "surface_finish": "Finish an STL model in a block (3 axes): raster or waterline",
}

STRUCT_TIPS = {
    "id": "A name for this operation, so others can wait for it with \"after\"",
    "after": "Ids of operations that must be cut before this one, e.g. [\"rough\"]",
    "tool": "The tool from the tool library (Tool library tab)",
    "shape": "The 2D shape to cut (Shapes & models tab)",
    "model": "The STL model to cut (Shapes & models tab)",
    "points": "Hole centres in work coordinates, [[x, y], ...]",
    "clear_tool": "A flat end mill that first clears the wide areas flat: the V-carve's floor at "
                  "max_depth, the inlay's pocket floor or plug flange (required for inlays). Without it "
                  "the middle of wide areas stays uncut",
    "fit_with": "Plug only: the pocket's job file. The check then replays both parts and tests that the "
                "plug fits the pocket",
    "clear": "Pocket parameters for the clear tool's pass, e.g. {\"stepover_ratio\": 0.45}",
    "index": "Cut this operation on several faces: A angles, e.g. 90, [0, 180] or {\"sides\": 4}",
    "face_z": "Height of each indexed face above the rotary axis (default: from the stock)",
}

PARAM_TIPS = {
    "depth": "How deep to cut, measured down from top_z (a positive number)",
    "step_down": "Most depth taken per pass; the cut is split into levels this far apart",
    "stepover_ratio": "Distance between neighbouring passes, as a fraction of the tool diameter "
                      "(0.4 = 40 % of the diameter)",
    "feed": "Cutting feed rate, mm/min",
    "plunge": "Feed rate for moving down into the material, mm/min",
    "safe_z": "Height for rapid moves between cuts. Must be above the stock and any clamps",
    "climb": "Climb milling (true, the usual choice on a CNC) or conventional (false)",
    "top_z": "Z where the material starts for this cut, e.g. -2 on the floor of a 2 mm pocket cut before",
    "entry": "How the tool gets down into the material: ramp along the path, helix (spiral) or plunge "
             "(straight down: centre-cutting tools only)",
    "ramp_angle": "Steepness of ramp and helix entries, degrees from horizontal",
    "helix_diameter": "Diameter of the helix entry (default: 0.8 x the tool diameter)",
    "method": "How the toolpath is computed",
    "name": "Name of this toolpath in the program and the report",
    "rpm": "Spindle speed, revolutions per minute",
    "side": "outside: cut around the shape (the part stays full size); inside: cut within it (a hole); "
            "on: the tool centre follows the line",
    "tabs": "Small bridges that hold the part to the sheet: {\"count\" or \"spacing\" or \"at\", "
            "\"width\", \"height\"}",
    "lead_radius": "Radius of an arc that leads the tool onto and off the wall, so no mark is left "
                   "where it starts",
    "step": "Distance between the offset contours of the V-carve (smaller = smoother, more moves)",
    "max_depth": "Deepest the V-bit goes, measured down from top_z. With a clear tool, wider areas get "
                 "a flat floor here",
    "tolerance": "How far the path may stray from the exact geometry",
    "start_depth": "The groove starts this far below top_z at the shape's edge (used for inlay plugs)",
    "pocket_depth": "Depth of the inlay pocket. Pocket and plug jobs must use the same value",
    "part": "pocket: the recess; plug: the mirrored piece that is glued into it",
    "glue_gap": "Space for glue between the plug's bottom and the pocket floor",
    "surface_gap": "How far the plug stands proud of the surface after glue-up (planed off later)",
    "plug_margin": "Board around the plug cleared flat (default: the whole plug stock)",
    "stock": "Set from the job's stock (Job tab)",
    "peck": "Depth per peck; the drill backs out between pecks to clear chips (empty: one pass)",
    "retract_z": "Height the drill backs out to between pecks",
    "stock_diameter": "Diameter of the round stock (default: from the job's cylinder stock)",
    "stepover": "Distance between neighbouring passes, mm (overrides stepover_ratio)",
    "stock_to_leave": "Material left on the model for the finishing pass",
    "x_range": "[start, end] along X to cut (default: the model's length)",
    "floor": "Lowest the tool tip goes",
    "resolution": "Grid spacing along X used to sample the model (smaller = more exact, slower)",
    "a_step": "Grid spacing around A, degrees (default: matching resolution at the stock radius)",
    "max_descent": "Steepest a non-centre-cutting tool may go down along the path, degrees",
    "strategy": "The pattern of the finishing passes",
    "stepover_deg": "Angle between neighbouring X raster passes, degrees (default: the stepover at the "
                    "model's largest radius)",
    "entry_clearance": "Height above the surface where the first pass starts feeding down",
    "boundary": "[x0, y0, x1, y1]: limits where the tool centre goes",
    "sampling": "Spacing of the model samples along each pass (smaller = more exact, slower)",
    "angle": "Direction of the raster passes, degrees from X",
    "z_range": "[bottom, top] of the waterline levels (default: the whole model)",

    "pocket.method": "offset: loops parallel to the wall; adaptive: constant tool load "
                     "(see stepover_ratio), for deep pockets in hard material",
    "pocket.stepover_ratio": "Distance between passes as a fraction of the tool diameter. With method "
                             "adaptive: the width of cut kept everywhere (0.1 to 0.25 is typical)",
    "pocket.depth": "Depth of the pocket floor below top_z (a positive number)",
    "vcarve.method": "offset: inset contours, step apart; medial: along the shape's centre lines, "
                     "fewer moves and sharp corners (needs entry plunge)",
    "vcarve.tolerance": "Medial method: how far the groove may stay short of the exact shape",
    "inlay.method": "medial (default, sharp corners; needs a centre-cutting bit) or offset",
    "inlay.tolerance": "Medial method: how far the groove may stay short of the exact shape",
    "inlay.stock": "Set from the job's box stock: the plug's flange is cleared to its edge",
    "surface_rough.stock": "Set from the job's box stock",
    "surface_rough.floor": "Lowest level roughed (default: the model's lowest point + stock_to_leave)",
    "surface_rough.boundary": "[x0, y0, x1, y1]: limits where the tool centre goes (default: the stock)",
    "surface_finish.floor": "Lowest the tool tip goes (default: the model's lowest point)",
    "surface_finish.boundary": "[x0, y0, x1, y1]: limits where the tool centre goes "
                               "(default: the model plus the tool radius)",
    "surface_finish.strategy": "raster: parallel passes, best on shallow areas; waterline: contours at "
                               "Z levels, best on steep walls",
    "surface_finish.step_down": "Waterline: distance between the Z levels (default: the stepover)",
    "rotary_finish.strategy": "helical: one continuous spiral (smoothest); a_raster: full turns of A, "
                              "stepping X; x_raster: sweeps along X, stepping A",
    "rotary_rough.floor": "Smallest radius left on the part, mm from the rotary axis (support)",
    "rotary_finish.floor": "Smallest radius left on the part, mm from the rotary axis (support)",
    "rotary_rough.stepover_ratio": "Distance between the turns of the helix, as a fraction of the tool "
                                   "diameter",
}

JOB_TIPS = {
    "post": "The controller the program is written for: linuxcnc (the PrintNC) or grbl (no A axis)",
    "material": "Picks each tool's feeds and speeds for this material from the tool library",
    "tool_library": "The tool library file, relative to the job",
    "machine": "The machine profile: travel, tool changer, body model (empty: the PrintNC profile)",
    "tool_order": "as written: operations in list order; minimize_changes: grouped by tool",
    "stock": "Required. The block or bar you clamp: box, cylinder or bar, in work coordinates",
    "defaults": "Values every operation inherits unless it sets its own, e.g. {\"safe_z\": 5}",
    "rotary": "Wrap all 2D operations around the A axis: {\"diameter\": D}",
    "index": "Indexed 3+1: cut every operation on several faces, e.g. {\"sides\": 4} or [0, 180]",
    "index_clearance": "Margin above the stock when A turns between faces (default 5)",
    "post_options": "Post settings, e.g. {\"arcs\": false} for G1 only, {\"arc_tolerance\": 0.005}",
}

TOOL_TIPS = {
    "name": "The name operations use for this tool",
    "diameter": "Cutting diameter, mm",
    "kind": "flat end mill, ball nose, bull nose (corner_radius) or V-bit (v_angle)",
    "v_angle": "V-bits: included angle of the V, degrees",
    "flute_length": "Cutting length; deeper cuts get a warning",
    "number": "ATC pocket = LinuxCNC tool number (1-6 on the PrintNC)",
    "center_cutting": "Can plunge straight down. Leave off unless the tool really can",
    "corner_radius": "Bull nose: radius of the rounded corner",
    "gauge_length": "Spindle nose to tool tip, for the machine body check",
}

CUTTING_TIPS = {
    "material": "Material name; the job's Material picks this row",
    "rpm": PARAM_TIPS["rpm"],
    "feed": PARAM_TIPS["feed"],
    "plunge": PARAM_TIPS["plunge"],
    "step_down": PARAM_TIPS["step_down"],
    "stepover_ratio": PARAM_TIPS["stepover_ratio"],
    "ramp_angle": PARAM_TIPS["ramp_angle"],
}


def param_tip(kind: str, name: str) -> str:
    """What parameter `name` of operation `kind` means ("" if unknown)."""
    return PARAM_TIPS.get(f"{kind}.{name}") or PARAM_TIPS.get(name, "")
