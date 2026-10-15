import json
from pathlib import Path

import pytest

from camcore.job import load_job, run_job
from camcore.verify import Machine, read_tool_table, run

TOOLS = {n: {"diameter": 6, "kind": "flat", "flute_length": 20, "center_cutting": True} for n in (1, 2, 7, 99)}


def errors(body, **mkw):
    m = Machine(**{"tools": dict(TOOLS), "atc_pockets": 6, "probe_tool": 99, **mkw})
    issues, _, _ = run("G21 G90\n" + body + "M5\nM2\n", m, None)
    return [i.message for i in issues if i.level == "error"]


# --- machine checks ---------------------------------------------------------

def test_atc_pockets_and_probe():
    assert errors("T1 M6\nG43 H1\nM3 S10000\n") == []
    assert errors("T7 M6\nG43 H7\n") == ["T7 has no ATC pocket (pockets 1..6, probe T99)"]
    assert errors("T99 M6\nG43 H99\nG0 X10\n") == []                          # loading the probe is fine
    assert errors("T99 M6\nG43 H99\nM3 S1000\n") == ["spindle started with the touch probe T99 loaded"]
    assert errors("T7 M6\nG43 H7\n", atc_pockets=None) == []                 # no ATC configured: no pocket check


def test_real_tool_table(tmp_path):
    (tmp_path / "tool.tbl").write_text("T1 P1 Z0 D3.175 ;em3\nT2  P2 Z12.5 D6 ; em6\n; T5 commented out\nT99 P99 D6 ;probe\n")
    assert read_tool_table(tmp_path / "tool.tbl") == {1, 2, 99}
    (tmp_path / "m.json").write_text(json.dumps({"tool_table": "tool.tbl", "atc_pockets": 6, "probe_tool": 99}))
    m = Machine.load(tmp_path / "m.json")
    assert m.table_numbers == {1, 2, 99}
    issues, _, _ = run("T5 M6\nG43 H5\n", m, None)
    assert [i.message for i in issues] == ["T5 is not in the controller's tool.tbl"]


def test_sim_uses_the_real_tool_table(tmp_path, monkeypatch):
    import subprocess
    from camcore import check
    (tmp_path / "tool.tbl").write_text("T1 P1 D3.175 ;real\n")
    (tmp_path / "sim.ini").write_text("[EMC]\n")
    mdata = {"work_offset": {"x": 0, "y": 0, "z": 0}, "sim": {"ini": "sim.ini"}, "tool_table": "tool.tbl"}
    seen = {}

    def fake_run(cmd, **kw):
        work = Path(cmd[cmd.index("-v") + 1].split(":")[0])
        seen["tbl"] = (work / "tool.tbl").read_text()
        return subprocess.CompletedProcess(cmd, 0, "OK", "")
    monkeypatch.setattr(check.subprocess, "run", fake_run)
    monkeypatch.setattr(check.shutil, "which", lambda name: "/usr/bin/docker")
    ok, _ = check.run_sim("M2\n", mdata, tmp_path / "m.json", {"x": {"number": 4, "diameter": 1}}, "img")
    assert ok and seen["tbl"] == "T1 P1 D3.175 ;real\n"


# --- jobs: materials and ordering --------------------------------------------

LIB = {
    "em3": {"number": 1, "diameter": 3, "center_cutting": True,
            "cutting": {"aluminium": {"rpm": 16000, "feed": 600, "plunge": 150, "step_down": 0.5, "ramp_angle": 2},
                        "mdf": {"rpm": 18000, "feed": 2000, "plunge": 500, "step_down": 2}}},
    "em6": {"number": 2, "diameter": 6,
            "cutting": {"aluminium": {"rpm": 14000, "feed": 900, "step_down": 1}}},
    "v90": {"number": 3, "diameter": 12, "kind": "vbit", "v_angle": 90, "center_cutting": True,
            "cutting": {"aluminium": {"rpm": 18000, "feed": 500}}},
}


def job(tmp_path, ops, **extra):
    (tmp_path / "lib.json").write_text(json.dumps(LIB))
    j = {"tool_library": "lib.json", "defaults": {"safe_z": 5, "feed": 999, "ramp_angle": 4},
         "shapes": {"s": {"type": "rect", "x": 0, "y": 0, "w": 40, "h": 30},
                    "t": {"type": "rect", "x": 50, "y": 0, "w": 20, "h": 20}},
         "operations": ops, **extra}
    (tmp_path / "j.json").write_text(json.dumps(j))
    return run_job(*load_job(tmp_path / "j.json"))


def feeds(tp):
    return {m.feed for m in tp.moves if m.feed is not None}


def test_material_feeds_with_precedence(tmp_path):
    ops = [{"op": "profile", "shape": "s", "tool": "em3", "depth": 1},
           {"op": "profile", "shape": "s", "tool": "em3", "depth": 1, "feed": 450, "rpm": 12000},
           {"op": "drill", "tool": "em3", "points": [[5, 5]], "depth": 2}]
    a, b, d = job(tmp_path, ops, material="aluminium")
    assert a.spindle_rpm == 16000 and feeds(a) == {600, 150}               # material beats job defaults
    assert len({round(m.z, 6) for m in a.moves if m.z is not None and m.z < 0 and m.kind == "feed"}) > 1  # 0.5 steps
    assert b.spindle_rpm == 12000 and 450 in feeds(b) and 600 not in feeds(b)   # the op beats the material
    assert feeds(d) == {150}                                               # drill takes only what it accepts
    a, *_ = job(tmp_path, ops[:1], material="mdf")
    assert a.spindle_rpm == 18000 and feeds(a) == {2000, 500}
    a, = job(tmp_path, [{"op": "profile", "shape": "s", "tool": "em3", "depth": 1, "step_down": 1}])
    assert feeds(a) == {999, 300}                                          # no material: job defaults
    with pytest.raises(ValueError, match="no cutting data for material 'mdf'"):
        job(tmp_path, [{"op": "profile", "shape": "s", "tool": "em6", "depth": 1}], material="mdf")
    with pytest.raises(TypeError):                                         # typos in the op itself still fail
        job(tmp_path, [{"op": "drill", "tool": "em3", "points": [[5, 5]], "depth": 2, "feed": 100}])


def names(paths):
    return [(p.name, p.tool.name) for p in paths]


def test_order_respects_after_and_minimizes_changes(tmp_path):
    ops = [{"op": "profile", "id": "rough", "shape": "s", "tool": "em6", "depth": 1, "step_down": 1, "name": "rough"},
           {"op": "pocket", "id": "pocket", "shape": "t", "tool": "em3", "depth": 1, "step_down": 1, "name": "pocket"},
           {"op": "profile", "id": "finish", "shape": "s", "tool": "em3", "depth": 1, "step_down": 1,
            "after": ["rough"], "name": "finish"},
           {"op": "profile", "id": "outline", "shape": "t", "tool": "em6", "depth": 1, "step_down": 1,
            "after": ["finish"], "name": "outline"}]
    as_written = job(tmp_path, ops, material="aluminium")
    assert names(as_written) == [("rough", "em6"), ("pocket", "em3"), ("finish", "em3"), ("outline", "em6")]
    ops2 = [ops[2], ops[0], ops[1], ops[3]]                                # finish listed first, but it is after rough
    assert [p.name for p in job(tmp_path, ops2, material="aluminium")] == ["rough", "finish", "pocket", "outline"]
    ops3 = [ops[1], ops[0], {**ops[3], "after": []}, ops[2]]
    grouped = job(tmp_path, ops3, material="aluminium", tool_order="minimize_changes")
    assert names(grouped) == [("pocket", "em3"), ("rough", "em6"), ("outline", "em6"), ("finish", "em3")]
    with pytest.raises(ValueError, match="cycle"):
        job(tmp_path, [{**ops[0], "after": ["finish"]}, ops[2]], material="aluminium")
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        job(tmp_path, [{**ops[0], "after": ["nope"]}], material="aluminium")
    with pytest.raises(ValueError, match="duplicate"):
        job(tmp_path, [ops[0], ops[0]], material="aluminium")


def test_vcarve_clearing_stays_before_its_vbit_pass(tmp_path):
    ops = [{"op": "vcarve", "shape": "s", "tool": "v90", "max_depth": 1, "clear_tool": "em3", "step": 0.5},
           {"op": "profile", "shape": "t", "tool": "v90", "depth": 0.5, "step_down": 0.5, "name": "mark"},
           {"op": "profile", "shape": "t", "tool": "em3", "depth": 1, "step_down": 1, "name": "cut"}]
    paths = job(tmp_path, ops, material="aluminium", tool_order="minimize_changes")
    order = [p.name for p in paths]
    assert order.index("vcarve-clear") < order.index("vcarve")
    assert names(paths) == [("vcarve-clear", "em3"), ("cut", "em3"), ("vcarve", "v90"), ("mark", "v90")]
    assert paths[0].spindle_rpm == 16000 and feeds(paths[0]) >= {600}         # clearing uses em3's aluminium data
