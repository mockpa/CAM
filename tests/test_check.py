import json
import shutil
from pathlib import Path

from camcore.check import check, fast_sim_ini, sim_var

ROOT = Path(__file__).parent.parent
EX = ROOT / "examples"
PRINTNC = ROOT / "machine" / "printnc_provisional.json"


def copy_job(tmp_path, name="sign_job.json", **changes):
    for f in ("tools.json", "sign.svg"):
        shutil.copy(EX / f, tmp_path / f)
    job = json.loads((EX / name).read_text())
    job.update(changes)
    p = tmp_path / name
    p.write_text(json.dumps(job))
    return p


def test_passing_job_writes_program_viewer_and_report(tmp_path):
    out = tmp_path / "out"
    assert check(copy_job(tmp_path), out, sim=False, log=lambda s: None)
    assert (out / "sign.ngc").read_text() == (EX / "sign.ngc").read_text()
    assert (out / "sign_view.html").exists()
    assert "PASSED WITHOUT SIM" in (out / "sign_report.txt").read_text()


def test_failing_job_writes_only_a_report_and_removes_the_old_program(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "sign.ngc").write_text("old program")
    (out / "sign_view.html").write_text("old viewer")
    job = json.loads((EX / "sign_job.json").read_text())
    job["shapes"]["recess"]["x"] = 1000                 # pocket runs past X max
    job["stock"]["x"] = [0, 1200]                       # still on the stock: verify's travel check finds it
    assert not check(copy_job(tmp_path, shapes=job["shapes"], stock=job["stock"]), out, sim=False,
                     log=lambda s: None)
    assert sorted(f.name for f in out.iterdir()) == ["sign_report.txt"]
    assert "outside machine travel" in (out / "sign_report.txt").read_text()


def test_job_without_stock_is_refused(tmp_path):
    p = copy_job(tmp_path)
    job = json.loads(p.read_text())
    del job["stock"]
    p.write_text(json.dumps(job))
    assert not check(p, tmp_path / "out", sim=False, log=lambda s: None)
    assert "no \"stock\"" in (tmp_path / "out" / "sign_report.txt").read_text()


def test_sim_g54_comes_from_the_machine_json():
    m = json.loads(PRINTNC.read_text())
    assert sim_var(m["work_offset"]) == (PRINTNC.parent / "printnc_sim.var").read_text()


def test_check_sim_raises_only_the_speed_limits():
    """The check's sim runs with speed and acceleration limits raised (LinuxCNC only
    slows down to meet them); soft limits, homing and everything else stay as they are."""
    ini = (ROOT / "machine" / "printnc_sim.ini").read_text()
    fast = fast_sim_ini(ini)
    old, new = ini.splitlines(), fast.splitlines()
    assert len(old) == len(new)
    changed = {(a, b) for a, b in zip(old, new) if a != b}
    assert changed == {("MAX_LINEAR_VELOCITY = 283", "MAX_LINEAR_VELOCITY = 30000"),
                       ("MAX_VELOCITY = 283", "MAX_VELOCITY = 30000"),
                       ("MAX_ACCELERATION = 2000", "MAX_ACCELERATION = 1000000"),
                       ("MAX_ACCELERATION = 360000", "MAX_ACCELERATION = 1000000")}
    # 2 x MAX_LINEAR_VELOCITY ([DISPLAY], [TRAJ]), 6 linear axes/joints x 2 keys, A's 2 accelerations
    assert sum(a != b for a, b in zip(old, new)) == 2 + 12 + 2
    assert "MAX_VELOCITY = 36000" in new and "MAX_ANGULAR_VELOCITY = 36000" in new   # already above
    assert "MAX_FEED_OVERRIDE = 50.0" in new and "MAX_LIMIT = 1035" in new
    # only in speed sections, comments and other keys untouched
    assert fast_sim_ini("[FOO]\nMAX_VELOCITY = 5\n[JOINT_0]\nMAX_VELOCITY = 5 ; x\r\n") == \
        "[FOO]\nMAX_VELOCITY = 5\n[JOINT_0]\nMAX_VELOCITY = 30000 ; x\r\n"
