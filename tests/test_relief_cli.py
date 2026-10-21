"""camcore relief (relief plan phase 6): questions, flags, outputs and the check gate."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from camcore.cli import main as camcore_main
from camcore.relief import cli
from camcore.relief.cli import QUESTIONS, main

EX = Path(__file__).parent.parent / "examples"
PNG = EX / "relief" / "waves.png"


def scripted(answers):
    """input_fn answering prompts in order; fails if the CLI asks more than given."""
    it = iter(answers)
    asked = []

    def fn(prompt):
        asked.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise AssertionError(f"unexpected question: {prompt}") from None
    fn.asked = asked
    return fn


def test_questions_are_well_formed():
    keys = [q.key for q in QUESTIONS]
    assert len(keys) == len(set(keys)) and keys[0] == "mode"
    for q in QUESTIONS:
        assert set(q.modes) <= set(cli.MODES) and q.flag is not None
        assert q.gate in (None, "adjust")
    assert {q.key for q in QUESTIONS if q.gate} == {"rotate", "mirror", "fit", "blur", "black", "white", "gamma",
                                                     "invert"}


def test_pins_with_flags(tmp_path):
    assert main([str(PNG), "-o", str(tmp_path), "--yes", "--mode", "pins", "--shape", "round", "--size", "20",
                 "--levels", "4", "--width", "200"]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["waves_cut_list.csv", "waves_height.png", "waves_pin_plan.dxf", "waves_pin_plan.svg",
                     "waves_positions.csv"]
    rows = list(csv.reader(open(tmp_path / "waves_cut_list.csv")))
    assert 2 <= len(rows) - 2 <= 4 and int(rows[-1][2]) == 9 * 6            # 200 x 133 mm: 9 x 6 pins
    assert Image.open(tmp_path / "waves_height.png").size == (240, 160)    # 200 x 133 mm: hardly cropped


def test_lamellas_write_boards_paperwork_and_checked_programs(tmp_path):
    rc = main([str(PNG), "-o", str(tmp_path), "--yes", "--mode", "lamellas", "--width", "120", "--thickness", "12",
               "--base", "8", "--relief", "15", "--board-width", "200", "--board-height", "150", "--interlock", "y",
               "--name", "lam", "--no-sim", "--no-open"])
    assert rc == 0
    jobs = sorted(tmp_path.glob("lam_sheet*_job.json"))
    assert len(jobs) == 2
    for j in jobs:
        assert j.with_name(j.name.replace("_job.json", ".ngc")).exists()
        job = json.loads(j.read_text())
        assert job["relief"]["sheet"]["interlock"] and job["relief"]["source"]["width"] == 120
        assert job["tool_library"] == str((EX / "tools.json").resolve()) or job["tool_library"].endswith("tools.json")
    for f in ("lam_parts.csv", "lam_assembly.svg", "lam_board1.svg", "lam_board2.dxf", "lam_height.png"):
        assert (tmp_path / f).exists()


def test_3d_writes_the_job_and_stl(tmp_path):
    rc = main([str(PNG), "-o", str(tmp_path), "--yes", "--mode", "3d", "--width", "30", "--depth", "5",
               "--frame", "2", "--thickness", "8", "--finish-dir", "xy", "--no-sim", "--no-open"])
    assert rc == 0
    job = json.loads((tmp_path / "waves_job.json").read_text())
    assert job["stock"]["z"] == [-8.0, 0.0] and job["stock"]["x"] == [0.0, 34.0]
    assert [o["id"] for o in job["operations"]] == ["rough", "finish", "finish_y"]
    assert (tmp_path / "waves.stl").exists() and (tmp_path / "waves.ngc").exists()


def test_typed_answers_are_checked_and_asked_again(tmp_path, capsys):
    fn = scripted(["pins",            # design
                   "y",               # adjust the image
                   "45", "90",        # turn: 45 refused, then 90
                   "n",               # mirror
                   "100", "",         # width, height = the suggestion (turned: 100 x 150)
                   "stretch", "0",    # fit, smoothing
                   "20", "10", "80",  # black 20, white 10 refused (below black), then 80
                   "", "y",           # gamma, invert
                   "square", "25", "", "", "", "0", ""])  # shape, size, gap, base, relief, levels, kerf
    assert main([str(PNG), "-o", str(tmp_path), "--no-sim"], input_fn=fn) == 0
    out = capsys.readouterr().out
    assert "'45' is not one of 0, 90, 180, 270" in out and "must be above the black point (20 %)" in out
    assert "artwork 100 x 150 mm" in out
    rows = list(csv.reader(open(tmp_path / "waves_positions.csv")))
    assert len(rows) - 1 == 3 * 5                                       # 25 + 2 mm pins on 100 x 150
    assert Image.open(tmp_path / "waves_height.png").size == (160, 240)  # turned, not cropped (stretch)


def test_stl_input_asks_the_view(tmp_path):
    fn = scripted(["pins", "y", "n", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
    assert main([str(EX / "relief.stl"), "-o", str(tmp_path), "--no-sim"], input_fn=fn) == 0
    assert fn.asked[1].startswith("Look at the STL from") and fn.asked[2].startswith("Adjust the image")
    job_free = sorted(p.name for p in tmp_path.iterdir())
    assert "relief_cut_list.csv" in job_free


def test_a_failing_check_leaves_no_program(tmp_path):
    m = json.loads((EX.parent / "machine" / "printnc_provisional.json").read_text())
    m["travel"] = {"x": [0, 40], "y": [0, 40], "z": [-150, 0]}           # the relief is 34 x 24, the tool reaches out
    m.pop("sim", None)
    machine = tmp_path / "small.json"
    machine.write_text(json.dumps(m))
    rc = main([str(PNG), "-o", str(tmp_path), "--yes", "--mode", "3d", "--width", "30", "--depth", "5",
               "--frame", "2", "--thickness", "8", "--machine", str(machine), "--no-sim", "--no-open"])
    assert rc == 1
    assert (tmp_path / "waves_job.json").exists() and not (tmp_path / "waves.ngc").exists()
    assert "outside machine travel" in (tmp_path / "waves_report.txt").read_text()


@pytest.mark.parametrize("argv, msg", [
    (["--mode", "lamellas", "--board-width", "2000"], "too large: the machine reaches 1015"),
    (["--mode", "lamellas", "--width", "120", "--board-width", "80", "--board-height", "80"], "do(es) not fit"),
    (["--mode", "3d", "--depth", "25"], "no flat/bull tool"),                   # em6 flutes are 22 mm
    (["--mode", "3d", "--depth", "5", "--thickness", "5.5"], "1 mm more than the depth"),
    (["--mode", "nothing"], "'nothing' is not one of"),
])
def test_bad_designs_end_with_a_message(tmp_path, capsys, argv, msg):
    assert main([str(PNG), "-o", str(tmp_path), "--yes", "--no-sim", "--no-open", *argv]) == 2
    assert msg in capsys.readouterr().err


def test_camcore_relief_subcommand(tmp_path):
    with pytest.raises(SystemExit) as e:
        camcore_main(["relief", str(PNG), "-o", str(tmp_path), "--yes", "--mode", "pins", "--width", "100"])
    assert e.value.code == 0 and (tmp_path / "waves_pin_plan.svg").exists()
