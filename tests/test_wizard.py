"""camcore rotary-wizard (guide step 8C)."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from camcore.job import load_job, run_job
from camcore.stl import load_stl, place
from camcore.verify import Machine, machine_from_library, run
from camcore.wizard import WizardError, finish_stepover, layout, main, model_size, suggest_floor

ROOT = Path(__file__).parent.parent
EX = ROOT / "examples"
QUIET = ["--no-sim", "--no-open"]


def scripted(answers):
    """An input() that returns the answers in order and records the prompts."""
    it = iter(answers)
    prompts = []

    def fn(prompt):
        prompts.append(prompt)
        return next(it)
    fn.prompts = prompts
    return fn


def verify_job(job_path):
    job, base = load_job(job_path)
    from camcore.check import DEFAULT_MACHINE, job_library
    from camcore.post import job_post
    gcode = job_post(job).emit(run_job(job, base))
    machine = machine_from_library(Machine.load(DEFAULT_MACHINE), job_library(job, base))
    issues, motions, _ = run(gcode, machine, job["stock"])
    return issues, motions


def test_place_along_y_and_z_and_flip():
    box = trimesh.creation.box((10, 20, 40))           # extents X 10, Y 20, Z 40
    box.apply_translation((5, 10, 20))                  # corner at the origin
    ext = lambda m: tuple(np.round(m.bounds[1] - m.bounds[0], 6))
    assert ext(place(box, along="y")) == (20, 10, 40)
    assert ext(place(box, along="z")) == (40, 20, 10)
    cone = trimesh.creation.cone(radius=5, height=30)    # apex at +Z
    standing = place(cone, along="z", x_start=0)
    tip = standing.vertices[standing.vertices[:, 0].argmax()]
    assert np.allclose(tip, (30, 0, 0))                 # apex at the far (free) end
    flipped = place(cone, along="z", flip=True, x_start=0)
    tip = flipped.vertices[flipped.vertices[:, 0].argmin()]
    assert np.allclose(tip, (0, 0, 0))                  # apex now at X0, the chuck end
    with pytest.raises(ValueError):
        place(box, along="w")


def test_model_size_is_the_exact_vertex_radius():
    length, r = model_size(place(load_stl(EX / "pawn.stl"), center_yz=True, x_start=0))
    assert length == pytest.approx(47.8785, abs=1e-3)
    assert r == pytest.approx(13.0, abs=1e-5)


def test_stock_layout_keeps_tools_off_the_chuck_jaws():
    # pawn: 47.88 long -> stock ends 2 mm past it at X50; 10 held + 3 tool radius + 2 clearance
    assert layout(47.88, 65, 10, 3) == (-15.0, 50.0)
    with pytest.raises(WizardError, match="at least 65 mm"):
        layout(47.88, 64.9, 10, 3)


def test_finish_stepover_from_scallop_height():
    # ball R 1.5, scallop 0.02: s = 2 sqrt(2 R h - h^2) = 0.488 -> 0.48
    assert finish_stepover({"diameter": 3, "kind": "ball"}, {"scallop": 0.02}) == 0.48
    assert finish_stepover({"diameter": 6, "kind": "flat"}, {"flat_stepover_ratio": 0.15}) == 0.9
    assert finish_stepover({"diameter": 3, "kind": "ball"}, {"scallop": 5}) == 1.5   # capped at D/2


def test_floor_default_keeps_the_cut_within_the_flutes():
    assert suggest_floor(30, 12) == 3.0     # 10 % of the stock
    assert suggest_floor(31, 12) == 3.5     # 15.5 - 12: deeper would pass the flutes
    assert suggest_floor(8, 12) == 1.0


def test_defaults_on_the_pawn_pass_check(tmp_path):
    out = tmp_path / "out"
    assert main([str(EX / "pawn.stl"), "-o", str(out), "--yes", *QUIET], input_fn=None) == 0
    job = json.loads((out / "pawn_job.json").read_text())
    assert job["stock"] == {"type": "cylinder", "diameter": 30.0, "x": [-15.0, 50.0]}
    assert job["models"]["pawn"]["center_yz"] and job["models"]["pawn"]["x_start"] == 0.0
    rough, finish = job["operations"]
    assert (rough["tool"], rough["floor"], rough["stock_to_leave"], rough["x_range"]) == ("em6", 3.0, 0.5, [0.0, 51.0])
    assert (finish["tool"], finish["strategy"], finish["stepover"], finish["x_range"]) == \
        ("ball3", "helical", 0.48, [0.0, 49.5])
    assert finish["after"] == ["rough"]
    assert (out / "pawn.ngc").exists() and (out / "pawn_view.html").exists()
    report = (out / "pawn_report.txt").read_text()
    assert "0 errors, 0 warnings" in report and "PASSED WITHOUT SIM" in report


def test_interactive_reasks_until_valid(tmp_path):
    out = tmp_path / "out"
    fn = scripted(["", "", "90",                 # axis, flip, rotate
                   "plywood", "softwood",        # unknown material, then a valid one
                   "", "",                       # tools
                   "20", "200", "32",            # stock too thin, larger than the A swing, ok
                   "", "40", "",                 # hold, stock too short, default length
                   "50", "", "fine", "x_raster"])
    assert main([str(EX / "pawn.stl"), "-o", str(out), *QUIET], input_fn=fn) == 0
    job = json.loads((out / "pawn_job.json").read_text())
    assert job["material"] == "softwood"
    assert job["stock"]["diameter"] == 32.0
    assert job["models"]["pawn"]["rotate_x"] == 90.0
    assert job["operations"][1]["strategy"] == "x_raster"
    assert job["wizard"]["preset"] == "fine"
    assert len(fn.prompts) == 17


def test_interactive_messages(tmp_path, capsys):
    out = tmp_path / "out"
    fn = scripted(["", "", "", "", "", "", "20", "200", "", "", "40", "", "50", "", "", ""])
    assert main([str(EX / "pawn.stl"), "-o", str(out), *QUIET], input_fn=fn) == 0
    text = capsys.readouterr().out
    assert "20 is too small: the model needs 26.00 mm" in text
    assert "200 is too large: the A axis swings at most 150 mm" in text
    assert "stock of 40 mm is too short" in text
    assert "50 is too large for the stock" in text


def test_bad_flag_is_an_error_not_a_prompt(tmp_path, capsys):
    rc = main([str(EX / "pawn.stl"), "-o", str(tmp_path), "--yes", "--diameter", "20", *QUIET], input_fn=None)
    assert rc == 2
    assert "the model needs 26.00 mm" in capsys.readouterr().err
    assert not (tmp_path / "pawn_job.json").exists()


def test_standing_model_is_laid_on_the_axis(tmp_path):
    """An STL exported standing up (long axis Z, off-centre): the wizard picks Z,
    centres it and the job verifies clean against the placed model."""
    pawn = load_stl(EX / "pawn.stl")
    standing = pawn.copy()                               # long axis X -> Z
    standing.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, (0, 1, 0)))
    standing.apply_translation((40, -25, 7))
    ext = standing.bounds[1] - standing.bounds[0]
    assert ext.argmax() == 2
    stl = tmp_path / "standing.stl"
    standing.export(stl)
    out = tmp_path / "out"
    assert main([str(stl), "-o", str(out), "--yes", "--preset", "coarse", *QUIET], input_fn=None) == 0
    job = json.loads((out / "standing_job.json").read_text())
    assert job["models"]["standing"]["along"] == "z"
    assert job["stock"]["diameter"] == 30.0
    issues, motions = verify_job(out / "standing_job.json")
    assert motions and not [i for i in issues if i.level == "error"]
