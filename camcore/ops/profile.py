"""Profile (contour) cutting: inside, outside or on the line."""
from __future__ import annotations

from shapely.geometry import LinearRing, Polygon
from shapely.geometry.base import BaseGeometry

from ..tools import Tool
from ..toolpath import Toolpath
from .common import offset as offset_geom
from .common import polygons, rings, step_depths
from .contour import RingParam, Tabs, cut_contour, plan_lead
from .entry import approach, check_entry


MIN_SLUG = 1.0     # mm: waste narrower than this inside a contour is not worth holding with tabs


def profile(shape: BaseGeometry, tool: Tool, depth: float, step_down: float,
            side: str = "outside", feed: float = 1000, plunge: float = 300,
            safe_z: float = 5.0, climb: bool = True, top_z: float = 0.0,
            entry: str = "ramp", ramp_angle: float = 3.0, tabs: dict | None = None,
            lead_radius: float | None = None, name: str = "profile") -> Toolpath:
    """Cut along the shape's boundary. Each contour goes through all depths without
    retracting: ramping down along the contour (or plunging, centre-cutting tools
    only). `tabs` = {"count" or "spacing", "width", "height", optional "ramp_angle"}
    leaves holding tabs: passes below the tab top rise over them and ramp back down.
    `lead_radius` adds lead-in/lead-out arcs in the waste (see contour.Lead); a
    contour where no lead circle fits without touching the part gets none."""
    check_entry(entry, tool)
    if lead_radius is not None and (side == "on" or lead_radius <= 0):
        raise ValueError("lead_radius needs side 'outside' or 'inside' and a value > 0")
    dist = {"outside": tool.radius, "inside": -tool.radius, "on": 0.0}[side]
    path_geom = offset_geom(shape, dist) if dist else shape
    tp = Toolpath(name, tool)
    contours = [r for p in polygons(path_geom) for r in rings(p, climb, wall_outside=side == "inside")]
    levels = step_depths(depth, step_down, top_z)
    if side == "outside":       # tool centre must stay a tool radius away from the part
        keep_clear = lambda path: path.distance(shape) >= tool.radius - 1e-6          # noqa: E731
    else:                       # inside: tool centre must stay inside the inset shape
        inset = offset_geom(shape, -tool.radius + 1e-6)
        keep_clear = lambda path: path.within(inset.buffer(1e-9))                       # noqa: E731
    for ring in contours:
        where = f"contour at ({ring[0][0]:.1f}, {ring[0][1]:.1f})"
        tab = Tabs(RingParam(ring).length, tabs, tool.diameter, top_z - depth, top_z) if tabs else None
        # Waste is on the left (climb) / right of travel; if that is the inside of this ring
        # and the tool mills (nearly) all of it away, there is no slug to hold.
        if tab and LinearRing(ring).is_ccw == climb and Polygon(ring).buffer(-tool.radius - MIN_SLUG / 2).is_empty:
            tp.notes.append(f"{name}: {where} is milled out completely, no tabs needed")
            tab = None
        if tab and len(tab.centres) < tab.requested:
            tp.notes.append(f"{name}: {where} is too short for {tab.requested} tabs, "
                            f"{len(tab.centres) or 'no'} tab(s) used")
        if tab and not tab.centres:
            tab = None
        lead = plan_lead(ring, lead_radius, climb, keep_clear, tool.radius) if lead_radius else None
        if lead_radius and lead is None:
            tp.notes.append(f"{name}: {where} has no room for a lead arc, ramping in along it")
        elif lead and lead.r < lead_radius:
            tp.notes.append(f"{name}: {where} lead radius reduced to {lead.r:g} mm to stay off the part")
        start = lead.at(lead.phi_s) if lead else ring[0]
        approach(tp, start[0], start[1], top_z, top_z, safe_z, plunge)
        cut_contour(tp, ring, levels, top_z, entry, ramp_angle, feed, plunge, tab, lead)
        tp.rapid(z=safe_z)
    return tp
