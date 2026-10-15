"""The desktop app's project model (no Qt needed)."""
import json
import shutil
from pathlib import Path

import pytest

from camcore.job import load_job, run_job
from camcore.project import (Project, describe_op, dumps, effective, op_params, parse_value, rebase,
                             save_library, unknown_keys)

EX = Path(__file__).parent.parent / "examples"


def copy_examples(tmp_path):
    d = tmp_path / "ex"
    shutil.copytree(EX, d, ignore=shutil.ignore_patterns("*.ngc", "*_view.html", "*.png"))
    return d


def test_every_example_round_trips_unchanged(tmp_path):
    for f in sorted(EX.glob("*_job.json")):
        job = json.loads(f.read_text())
        out = tmp_path / f.name
        out.write_text(dumps(job))
        assert json.loads(out.read_text()) == job, f.name


def test_dumps_keeps_short_values_on_one_line():
    text = dumps({"stock": {"type": "box", "x": [0, 160]}, "operations": [{"op": "drill", "points": [[8, 8]]}]})
    assert '"stock": {"type": "box", "x": [0, 160]}' in text
    assert all(len(line) <= 100 for line in dumps(json.loads((EX / "sign_job.json").read_text())).splitlines())


def test_op_params_follow_the_op_signature():
    names = [p.name for p in op_params("pocket")]
    assert names[:3] == ["depth", "step_down", "stepover_ratio"] and names[-1] == "rpm"
    assert "shape" not in names and "tool" not in names
    depth = next(p for p in op_params("pocket") if p.name == "depth")
    assert depth.required
    with pytest.raises(ValueError):
        op_params("explode")


def test_effective_shows_where_each_value_comes_from():
    job, base = load_job(EX / "indexed_job.json")         # material hardwood, defaults safe_z 5
    p = Project.load(EX / "indexed_job.json")
    eff = effective(job, p.library(), job["operations"][0])
    assert eff["depth"] == (3, "op")
    assert eff["feed"] == (1500, "cutting")                # em3's hardwood data
    assert eff["safe_z"] == (5.0, "defaults")
    assert "climb" not in eff                              # left at the op's default
    job["material"] = "unobtainium"
    with pytest.raises(ValueError, match="no cutting data"):
        effective(job, p.library(), job["operations"][0])


def test_parse_value():
    assert parse_value("1.5") == 1.5 and parse_value("true") is True and parse_value("[1, 2]") == [1, 2]
    assert parse_value("outside") == "outside" and parse_value("  ") is None


def test_save_as_elsewhere_rebases_relative_paths(tmp_path):
    d = copy_examples(tmp_path)
    p = Project.load(d / "sign_job.json")
    target = tmp_path / "jobs" / "deeper" / "sign2_job.json"
    target.parent.mkdir(parents=True)
    p.save(target)
    job = json.loads(target.read_text())
    assert job["tool_library"] == "../../ex/tools.json"
    assert job["shapes"]["art"]["file"] == "../../ex/sign.svg"
    original, _ = load_job(d / "sign_job.json")
    moved, base = load_job(target)
    assert [tp.moves for tp in run_job(moved, base)] == [tp.moves for tp in run_job(original, d)]


def test_rebase_reaches_nested_shapes_and_models():
    job = {"tool_library": "t.json", "machine": "/abs/m.json",
           "shapes": {"u": {"type": "union", "shapes": [{"type": "svg", "file": "a.svg"}]},
                      "d": {"type": "difference", "a": {"type": "dxf", "file": "b.dxf"}, "b": {"type": "rect"}}},
           "models": {"m": {"file": "m.stl"}}}
    out = rebase(job, Path("/w/x"), Path("/w/y"))
    assert out["tool_library"] == "../x/t.json" and out["machine"] == "/abs/m.json"
    assert out["shapes"]["u"]["shapes"][0]["file"] == "../x/a.svg"
    assert out["shapes"]["d"]["a"]["file"] == "../x/b.dxf"
    assert out["models"]["m"]["file"] == "../x/m.stl"
    assert job["tool_library"] == "t.json"                  # the original is untouched


def test_build_a_job_from_scratch_and_run_it(tmp_path):
    d = copy_examples(tmp_path)
    p = Project()
    p.save(d / "new_job.json")
    p.job["tool_library"] = "tools.json"
    shape = p.add_shape({"type": "rect", "x": 10, "y": 10, "w": 40, "h": 30}, "recess")
    op = p.add_op("pocket")
    assert op["shape"] == shape and op["tool"] == "em3" and op["id"] == "pocket"
    p.set_op_value(op, "depth", 2.0)
    p.set_op_value(op, "step_down", 1.0)
    art = p.add_shape_file(d / "sign.svg")
    assert p.job["shapes"][art] == {"type": "svg", "file": "sign.svg"}
    v = p.add_op("vcarve")
    assert v["tool"] == "v60" and v["shape"] == "recess" and v["id"] == "vcarve"
    p.set_op_value(v, "shape", art)
    p.add_op("drill")["points"] = [[5, 5]]
    p.set_op_value(p.job["operations"][2], "depth", 3)
    assert [describe_op(o, i) for i, o in enumerate(p.job["operations"])][2] == "drill: drill 1 points with em3"
    assert p.move_op(p.move_op(2, -1), -1) == 0 and p.job["operations"][0]["op"] == "drill"
    assert p.move_op(0, -1) == 0                                           # already first
    p.save()
    job, base = load_job(d / "new_job.json")
    assert [tp.name for tp in run_job(job, base)] == ["drill", "pocket", "vcarve"]


def test_rename_follows_into_operations(tmp_path):
    p = Project.load(EX / "sign_job.json")
    assert p.rename("shapes", "art", "logo") == "logo"
    assert p.job["operations"][1]["shape"] == "logo" and "art" not in p.job["shapes"]
    assert list(p.job["shapes"]) == ["panel", "recess", "logo"]           # order kept
    assert p.rename("shapes", "logo", "panel") == "panel2"                 # never clobbers
    assert p.users("shapes", "panel2") == ["#2"] and p.users("shapes", "panel") == ["#4"]


def test_unknown_keys():
    assert unknown_keys({"op": "pocket", "depth": 1, "colour": "red"}) == ["colour"]
    assert unknown_keys({"op": "vcarve", "clear_tool": "em3", "clear": {}, "rpm": 1}) == []


def test_save_library_refuses_a_broken_library(tmp_path):
    f = tmp_path / "tools.json"
    f.write_text("{}")
    with pytest.raises(ValueError, match="duplicate tool numbers"):
        save_library(f, {"a": {"number": 1, "diameter": 3}, "b": {"number": 1, "diameter": 6}})
    with pytest.raises(TypeError):
        save_library(f, {"a": {"number": 1, "diameter": 3, "colour": "red"}})
    assert f.read_text() == "{}"
    save_library(f, {"a": {"number": 1, "diameter": 3}})
    assert json.loads(f.read_text()) == {"a": {"number": 1, "diameter": 3}}
