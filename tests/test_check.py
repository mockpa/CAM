import json
import shutil
from pathlib import Path

from camcore.check import check, sim_var

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
