"""3D relief jobs (relief plan phase 2): height field to an STL block and a surface job."""
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from camcore.job import load_job, run_job
from camcore.relief import heightfield as hf
from camcore.relief import relief3d as r3
from camcore.relief.relief3d import Relief3D, ReliefError
from camcore.tools import Tool
from camcore.verify import Machine, machine_from_library, run
from camcore.post import job_post
from camcore.check import DEFAULT_MACHINE, job_library
from test_surface3d import feed_points, worst_depth

EX = Path(__file__).parent.parent / "examples"
LIB = json.loads((EX / "tools.json").read_text())
REL = EX / "relief"


def field(w=30, h=20, blur=1.0):
    return hf.prepare(hf.sample_relief(72, 48), w, h, blur=blur)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def test_axis_nodes_double_the_frame_edge():
    xs, on = r3.axis_nodes(10, 2, 0.5)
    assert xs[0] == 0 and xs[-1] == 14
    edge = np.nonzero(np.isclose(xs, 2))[0]
    assert len(edge) == 2 and not on[edge[0]] and on[edge[1]]
    assert np.all(on[(xs > 2) & (xs < 12)]) and not np.any(on[(xs < 2) | (xs > 12)])
    xs, on = r3.axis_nodes(10, 0, 0.3)
    assert len(xs) == 35 and on.all() and np.allclose(np.diff(xs), 10 / 34)


def test_surface_heights_frame_and_relief():
    F = field()
    xs, ys, S = r3.surface_grid(F, depth=5, frame=2, cell=0.5)
    assert (xs[-1], ys[-1]) == (34, 24)
    assert np.all(S[:, xs < 2] == 0) and np.all(S[ys > 22, :] == 0)       # frame at the stock top
    on = (xs >= 2) & (xs <= 32)
    j = 10
    inner = np.nonzero(on)[0][1:-1]
    assert np.allclose(S[j, inner], -5 * (1 - F.sample(xs[inner] - 2, ys[j] - 2)))
    assert S.min() >= -5 and S.min() < -4.5                                  # black is 5 deep


def test_block_is_a_closed_solid_and_drops_back_to_the_grid():
    F = field()
    xs, ys, S = r3.surface_grid(F, depth=5, frame=0, cell=0.5)
    m = r3.block_mesh(xs, ys, S, -8)
    assert m.is_volume
    assert np.allclose(m.bounds, [[0, 0, -8], [30, 20, S.max()]])
    d = hf.drop(m.triangles, 0.5)                    # row 0 at the back: flip to Y ascending
    assert np.abs(d.z[::-1] - S).max() < 1e-9
    framed = r3.block_mesh(*r3.surface_grid(F, 5, 2, 0.5), -8)
    assert framed.is_volume and framed.volume == pytest.approx(34 * 24 * 8 - (30 * 20 * 8 - m.volume))


# --------------------------------------------------------------------------
# Design checks
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kw, msg", [
    (dict(depth=8, thickness=8.5), "thicker than"),
    (dict(depth=25, thickness=30), "flutes are only 22"),                 # em6
    (dict(depth=8, thickness=12, rough=False), "turn roughing on"),        # ball3: 1 mm step down
    (dict(depth=8, thickness=12, rough_tool="v60"), "needs an end mill"),
    (dict(depth=8, thickness=12, finish_dir="z"), "finish_dir"),
    (dict(depth=8, thickness=12, finish_tool="nope"), "not in the tool library"),
])
def test_unsafe_designs_are_refused(kw, msg):
    with pytest.raises(ReliefError, match=msg):
        r3.check_design(Relief3D(**kw), LIB, "hardwood")


def test_finishing_alone_only_within_one_step_down():
    notes = r3.check_design(Relief3D(depth=0.8, thickness=5, rough=False), LIB, "hardwood")
    assert notes == ["no roughing: ball3 cuts the whole 0.8 mm in one pass"]
    with pytest.raises(ReliefError, match="step down \\?"):                 # no cutting data: refuse
        r3.check_design(Relief3D(depth=0.8, thickness=5, rough=False), LIB, None)


def test_job_layout(tmp_path):
    F = field()
    job = r3.build_job(F, Relief3D(depth=5, thickness=8, frame=2, finish_dir="xy", finish_stepover=0.4),
                       tmp_path, "r", EX / "tools.json", source={"file": "x.png"})
    assert (tmp_path / "r.stl").exists() and job["models"] == {"r": {"file": "r.stl"}}
    assert job["stock"] == {"type": "box", "x": [0.0, 34.0], "y": [0.0, 24.0], "z": [-8.0, 0.0]}
    rough, fx, fy = job["operations"]
    assert rough == {"id": "rough", "op": "surface_rough", "model": "r", "tool": "em6",
                     "stock_to_leave": 0.5, "floor": -4.5}
    assert fx["angle"] == 0 and fy["angle"] == 90 and fx["after"] == ["rough"] and fy["after"] == ["finish"]
    assert fx["boundary"] == [0.5, 0.5, 33.5, 23.5]                          # frame edge - ball radius
    assert fx["stepover"] == 0.4 and fx["floor"] == -5.0
    assert job["relief"]["source"] == {"file": "x.png"} and job["relief"]["frame"] == 2
    job = r3.build_job(F, Relief3D(depth=0.8, thickness=5), tmp_path, "r", EX / "tools.json")
    assert [o["id"] for o in job["operations"]] == ["rough", "finish"]
    assert job["operations"][1]["boundary"] == [0.0, 0.0, 30.0, 20.0]
    job = r3.build_job(F, Relief3D(depth=0.8, thickness=5, rough=False), tmp_path, "r", EX / "tools.json")
    (fin,) = job["operations"]
    assert "after" not in fin and job["relief"]["notes"]


# --------------------------------------------------------------------------
# Machining
# --------------------------------------------------------------------------

def model_points(mesh, frame, depth, cell=0.1):
    """Dense exact points of the relief surface plus the frame walls."""
    d = hf.drop(mesh.triangles, cell)
    ny, nx = d.z.shape
    X, Y = np.meshgrid(d.x0 + np.arange(nx) * cell, d.y1 - np.arange(ny) * cell)
    pts = [np.column_stack([X.ravel(), Y.ravel(), d.z.ravel()])]
    top = pts[0]
    (x0, y0, _), (x1, y1, _) = mesh.bounds
    edge = top[(np.isclose(top[:, 0], x0 + frame) | np.isclose(top[:, 0], x1 - frame)
                | np.isclose(top[:, 1], y0 + frame) | np.isclose(top[:, 1], y1 - frame))]
    for z in np.arange(-depth, 0, 0.1):
        pts.append(np.column_stack([edge[:, :2], np.full(len(edge), z)]))
    return np.vstack(pts)


def test_small_relief_machines_clean(tmp_path):
    d = Relief3D(depth=5, thickness=8, frame=2)
    path = r3.write_job(field(), d, tmp_path, "small", EX / "tools.json")
    job, base = load_job(path)
    paths = run_job(job, base)
    gcode = job_post(job).emit(paths)
    machine = machine_from_library(Machine.load(DEFAULT_MACHINE), job_library(job, base))
    issues, _, _ = run(gcode, machine, job["stock"])
    assert [str(i) for i in issues] == []
    mesh = trimesh.load(tmp_path / "small.stl")
    pts = model_points(mesh, d.frame, d.depth)
    rough, finish = paths
    em6 = Tool.from_dict({"name": "em6", **LIB["em6"]})
    ball3 = Tool.from_dict({"name": "ball3", **LIB["ball3"]})
    assert worst_depth(rough, pts, em6) <= -0.49                       # 0.5 mm left everywhere
    assert worst_depth(finish, pts, ball3) <= 0.0055                   # no gouge (tolerance 0.005)
    assert feed_points(rough)[:, 2].min() >= -4.5 - 1e-9              # never below depth - leave


# --------------------------------------------------------------------------
# Example
# --------------------------------------------------------------------------

def test_example_regenerates(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_example", REL / "make_example.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = mod.main(tmp_path)
    new, old = json.loads(path.read_text()), json.loads((REL / "waves3d_job.json").read_text())
    new.pop("tool_library"), old.pop("tool_library")           # relative to the output folder
    assert new == old
    assert (tmp_path / "waves.png").read_bytes() == (REL / "waves.png").read_bytes()
    assert (tmp_path / "waves3d.stl").read_bytes() == (REL / "waves3d.stl").read_bytes()
