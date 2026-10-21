"""camcore-gui (guide step 10), driven offscreen with pytest-qt."""
import json
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ["QT_QPA_PLATFORM"] == "offscreen":     # no GL offscreen: WebGL would bring Chromium down
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
try:                                      # must be loaded before the QApplication exists
    import PySide6.QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QComboBox  # noqa: E402

import camcore.check  # noqa: E402
from camcore.gui.main_window import MainWindow  # noqa: E402
from camcore.gui.wizard_dialog import WizardDialog  # noqa: E402
from camcore.wizard import main as wizard_main  # noqa: E402

EX = Path(__file__).parent.parent / "examples"


@pytest.fixture
def ex(tmp_path):
    d = tmp_path / "ex"
    shutil.copytree(EX, d, ignore=shutil.ignore_patterns("*.ngc", "*_view.html", "*.png"))
    return d


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setenv("CAMCORE_GUI_NO_WEBENGINE", "1")
    made = []

    def make(path=None):
        w = MainWindow(path)
        made.append(w)
        return w
    yield make
    for w in made:                         # close without the unsaved-changes questions
        w.runner.wait(300)
        w.project.dirty = w.tools_panel.dirty = False
        w.close()


def type_into(qtbot, w, text):
    """Replace a field's text as a user would (so textEdited fires)."""
    w.selectAll()
    qtbot.keyClick(w, Qt.Key_Backspace)
    if text:
        qtbot.keyClicks(w, text)


def run_check(qtbot, w, start):
    start()
    qtbot.waitUntil(lambda: w.last_result is not None and not w.runner.busy, timeout=300_000)
    return w.last_result


def test_edit_an_operation_and_save(qtbot, window, ex):
    w = window(ex / "sign_job.json")
    assert w.ops_panel.list.count() == 4
    w.ops_panel.list.setCurrentRow(1)
    form = w.ops_panel.form
    fields = {p.name: f for p, f in form.fields.items()}
    assert fields["step"].text() == "0.15"
    assert fields["plunge"].text() == "" and fields["plunge"].placeholderText() == "300"
    assert fields["safe_z"].placeholderText() == "5.0"                 # job defaults
    assert fields["safe_z"].toolTip() == "from the job defaults"
    type_into(qtbot, fields["feed"], "950")
    type_into(qtbot, fields["top_z"], "")                               # empty: back to inherited
    assert w.project.job["operations"][1]["feed"] == 950
    assert "top_z" not in w.project.job["operations"][1]
    assert w.windowTitle().startswith("*sign")
    w.save()
    saved = json.loads((ex / "sign_job.json").read_text())
    assert saved["operations"][1]["feed"] == 950 and "top_z" not in saved["operations"][1]
    assert not w.windowTitle().startswith("*")


def test_hints_follow_the_tool_and_material(qtbot, window, ex):
    w = window(ex / "indexed_job.json")                                  # material: hardwood
    form = w.ops_panel.form
    fields = {p.name: f for p, f in form.fields.items()}
    assert fields["feed"].placeholderText() == "1500"                    # em3 hardwood
    assert fields["feed"].toolTip() == "from the tool's hardwood data"
    tool = next(c for c in form.findChildren(QComboBox) if c.currentText() == "em3")
    tool.setCurrentText("em6")
    tool.textActivated.emit("em6")
    assert w.project.job["operations"][0]["tool"] == "em6"
    assert fields["feed"].placeholderText() == "2500"                    # em6 hardwood
    assert w.ops_panel.list.item(0).text() == "slots: pocket slot with em6"
    type_into(qtbot, fields["depth"], "")
    assert "depth is required" in form.warning.text()


def test_3d_ops_take_the_box_stock_from_the_job(qtbot, window, ex):
    w = window(ex / "relief_job.json")
    for row in range(3):
        w.ops_panel.list.setCurrentRow(row)
        form = w.ops_panel.form
        assert form.warning.text() == "", row
    w.ops_panel.list.setCurrentRow(0)
    fields = {p.name: f for p, f in w.ops_panel.form.fields.items()}
    assert fields["stock"].placeholderText() == "from the box stock"
    assert fields["feed"].placeholderText() == "2500"                    # em6 hardwood


def test_preview_is_a_dry_run_outside_the_output_folder(qtbot, window, ex):
    w = window(ex / "sign_job.json")
    ok, view, exported = run_check(qtbot, w, w.preview)
    assert ok and not exported
    assert Path(view).parent == w.preview_dir and Path(view).exists()
    assert "PASSED WITHOUT SIM" in w.report.toPlainText()
    assert not (ex / "out").exists()
    assert w.viewer.path == view and w.viewer.open_btn.isEnabled()


def test_preview_saves_first(qtbot, window, ex):
    w = window(ex / "cutout_job.json")
    w.ops_panel.list.setCurrentRow(0)
    w.project.set_op_value(w.project.job["operations"][0], "feed", 777)
    ok, *_ = run_check(qtbot, w, w.preview)
    assert ok and json.loads((ex / "cutout_job.json").read_text())["operations"][0]["feed"] == 777
    assert "F777" in (w.preview_dir / "cutout.ngc").read_text()


def test_export_writes_the_program_only_if_the_sim_passes(qtbot, window, ex, monkeypatch):
    w = window(ex / "cutout_job.json")
    monkeypatch.setattr(camcore.check, "run_sim", lambda *a: (False, "LinuxCNC: joint 0 out of range"))
    ok, view, exported = run_check(qtbot, w, w.export)
    assert not ok and not view
    assert not (ex / "out" / "cutout.ngc").exists() and (ex / "out" / "cutout_report.txt").exists()
    assert "joint 0 out of range" in w.report.toPlainText()
    assert w.statusBar().currentMessage().startswith("FAILED")

    monkeypatch.setattr(camcore.check, "run_sim", lambda *a: (True, "ran to the end"))
    w.last_result = None
    ok, view, exported = run_check(qtbot, w, w.export)
    assert ok and exported and (ex / "out" / "cutout.ngc").exists()
    assert Path(view) == ex / "out" / "cutout_view.html"
    assert w.viewer.label.text().startswith("Exported (sim passed)")


def test_build_a_job_in_the_app(qtbot, window, ex):
    w = window()
    w.project.save(ex / "plate_job.json")
    w._set_project(w.project)
    w.shapes_panel.add_rect()
    w.ops_panel.add("profile")
    fields = {p.name: f for p, f in w.ops_panel.form.fields.items()}
    assert "depth is required" in w.ops_panel.form.warning.text()
    type_into(qtbot, fields["depth"], "3")
    type_into(qtbot, fields["step_down"], "1.5")
    side = next(f for p, f in w.ops_panel.form.fields.items() if p.name == "side")
    assert [side.itemText(i) for i in range(side.count())] == ["", "outside", "inside", "on"]
    assert w.ops_panel.form.warning.text() == ""
    assert w.ops_panel.list.item(0).text() == "profile: profile rect with em3"
    ok, *_ = run_check(qtbot, w, w.preview)
    assert ok, w.report.toPlainText()
    job = json.loads((ex / "plate_job.json").read_text())
    assert job["operations"] == [{"id": "profile", "op": "profile", "tool": "em3", "shape": "rect",
                                  "depth": 3, "step_down": 1.5}]


def test_shape_json_editor(qtbot, window, ex):
    w = window(ex / "sign_job.json")
    sp = w.shapes_panel
    assert sp.current() == ("shapes", "panel")
    sp.editor.setPlainText('{"type": "rect", "x": 0, "y": 0, "w": 150, "h": 90}')
    assert w.project.job["shapes"]["panel"]["w"] == 150
    sp.editor.setPlainText('{"type": "rect", "x": 0,')                  # broken: kept, not applied
    assert "panel" in w.project.job["shapes"] and w.project.job["shapes"]["panel"]["w"] == 150
    assert sp.error.text()
    name = w.project.add_model_file(ex / "pawn.stl")
    sp.set_project(w.project, ("models", name))
    assert json.loads(sp.editor.toPlainText()) == {"file": "pawn.stl"}


def test_tool_library_editor(qtbot, window, ex):
    w = window(ex / "sign_job.json")
    te = w.tools_panel
    names = te.names()
    assert names[:2] == ["em3", "em6"]
    col = 1 + [f.name for f in te.fields].index("diameter")
    te.tools.item(0, col).setText("3.0")
    assert te.data["em3"]["diameter"] == 3.0 and te.dirty
    te.tools.setCurrentCell(0, 0)
    mats = [te.cutting.item(r, 0).text() for r in range(te.cutting.rowCount())]
    assert "hardwood" in mats
    feed_col = 1 + te.cut_keys.index("feed")
    te.cutting.item(mats.index("hardwood"), feed_col).setText("1400")
    assert te.data["em3"]["cutting"]["hardwood"]["feed"] == 1400
    assert te.save()
    lib = json.loads((ex / "tools.json").read_text())
    assert lib["em3"]["diameter"] == 3.0 and lib["em3"]["cutting"]["hardwood"]["feed"] == 1400

    num_col = 1 + [f.name for f in te.fields].index("number")
    te.tools.item(1, num_col).setText("1")                               # em6 takes em3's number
    assert not te.save() and "duplicate tool numbers" in te.status.text()
    assert json.loads((ex / "tools.json").read_text()) == lib            # nothing written
    te.revert()
    assert not te.dirty and te.data["em6"]["number"] == 2


def test_wizard_dialog_writes_the_same_job_as_the_cli(qtbot, tmp_path):
    cli_out, gui_out = tmp_path / "a" / "out", tmp_path / "b" / "out"
    assert wizard_main([str(EX / "pawn.stl"), "-o", str(cli_out), "--yes", "--no-sim", "--no-open"],
                       input_fn=None) == 0
    d = WizardDialog(EX / "pawn.stl", gui_out)
    qtbot.addWidget(d)
    assert d.refresh()
    assert "largest radius 13.00 mm" in d.placed.text()
    d.accept()
    assert json.loads(d.job_path.read_text()) == json.loads((cli_out / "pawn_job.json").read_text())


def test_wizard_dialog_checks_answers_like_the_cli(qtbot, tmp_path):
    d = WizardDialog(EX / "pawn.stl", tmp_path)
    qtbot.addWidget(d)
    dia = d.widget("stock_diameter")
    assert dia.placeholderText() == "30"
    type_into(qtbot, dia, "20")
    msg = next(r[2] for r in d.rows if r[0].key == "stock_diameter")
    assert msg.text() == "20 is too small: the model needs 26.00 mm"
    assert not d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
    assert not d.widget("floor").isEnabled()                            # waits for a valid diameter
    type_into(qtbot, dia, "32")
    assert d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
    assert d.widget("floor").placeholderText() == "4"                   # 16 - 12 mm flutes: follows the diameter
    mat = d.widget("material")
    mat.setCurrentText("softwood")
    mat.textActivated.emit("softwood")
    assert d.session.a["material"] == "softwood"
    d.accept()
    job = json.loads(d.job_path.read_text())
    assert job["material"] == "softwood" and job["stock"]["diameter"] == 32.0


def test_wizard_from_the_main_window_opens_and_checks_the_job(qtbot, window, tmp_path):
    w = window()
    d = WizardDialog(EX / "pawn.stl", tmp_path / "out")
    d.sim.setChecked(False)
    d.exec = lambda: (d.accept(), 1)[1]
    ok, view, _ = run_check(qtbot, w, lambda: w.run_wizard(d))
    assert ok, w.report.toPlainText()
    assert w.project.path == (tmp_path / "out" / "pawn_job.json").resolve()
    assert [o["op"] for o in w.project.job["operations"]] == ["rotary_rough", "rotary_finish"]
    assert Path(view) == tmp_path / "out" / "pawn_view.html"


def test_embedded_viewer_loads_the_program(qtbot, ex, monkeypatch):
    monkeypatch.delenv("CAMCORE_GUI_NO_WEBENGINE", raising=False)
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    w = MainWindow(ex / "cutout_job.json")
    try:
        web = w.viewer.web
        assert web is not None
        loads = []                         # (ok, url): the blank page set on open finishes too
        web.loadFinished.connect(lambda ok: loads.append((ok, web.url().toLocalFile())))
        run_check(qtbot, w, w.preview)
        qtbot.waitUntil(lambda: any(u.endswith("cutout_view.html") for _, u in loads), timeout=120_000)
        assert (True, str(w.preview_dir / "cutout_view.html")) in loads
    finally:
        w.close()


# --- relief dialog (relief plan phase 7) ---------------------------------------------------------

from camcore.gui.relief_dialog import ReliefDialog  # noqa: E402
from camcore.relief.cli import main as relief_main  # noqa: E402

PNG = EX / "relief" / "waves.png"
SMALL = {"width": "120", "thickness": "12", "base": "8", "relief": "15", "board_width": "200", "board_height": "150"}


def fill(qtbot, d, answers):
    for key, text in answers.items():
        w = d.widget(key)
        if isinstance(w, QComboBox):
            w.setCurrentText(text)
            w.textActivated.emit(text)
        else:
            type_into(qtbot, w, text)


def test_relief_dialog_writes_the_same_files_as_the_cli(qtbot, tmp_path):
    cli_out, gui_out = tmp_path / "a" / "out", tmp_path / "b" / "out"
    assert relief_main([str(PNG), "-o", str(cli_out), "--yes", "--no-sim", "--no-open", "--width", "120",
                        "--thickness", "12", "--base", "8", "--relief", "15", "--board-width", "200",
                        "--board-height", "150", "--interlock", "y"], input_fn=None) == 0
    d = ReliefDialog(PNG, gui_out)
    qtbot.addWidget(d)
    fill(qtbot, d, {**SMALL, "interlock": "y"})
    assert d.refresh()
    d.accept()
    assert [p.name for p in d.jobs] == ["waves_sheet1_job.json", "waves_sheet2_job.json"]
    for p in d.jobs:
        assert json.loads(p.read_text()) == json.loads((cli_out / p.name).read_text())
    assert (gui_out / "waves_parts.csv").read_text() == (cli_out / "waves_parts.csv").read_text()


def test_relief_dialog_shows_what_applies_and_previews(qtbot, tmp_path):
    d = ReliefDialog(PNG, tmp_path)
    qtbot.addWidget(d)
    assert d.visible("thickness") and d.visible("interlock") and not d.visible("shape")
    assert not d.visible("invert") and not d.visible("stl_view")          # behind "adjust", not an STL
    assert d.widget("height").placeholderText() == "400"                  # 600 wide, the image's 3:2
    before = d.image.copy()
    assert not before.isNull() and before.width() == 480
    fill(qtbot, d, {"mode": "pins"})
    assert d.visible("shape") and d.visible("levels") and not d.visible("thickness")
    fill(qtbot, d, {"adjust": "y"})
    assert d.visible("invert") and d.visible("rotate")
    fill(qtbot, d, {"invert": "y"})
    assert d.image != before                                              # the preview follows
    fill(qtbot, d, {"rotate": "90"})
    assert d.widget("height").placeholderText() == "900"                  # turned: 2:3


def test_relief_dialog_checks_answers_like_the_cli(qtbot, tmp_path):
    d = ReliefDialog(PNG, tmp_path)
    qtbot.addWidget(d)
    fill(qtbot, d, {"adjust": "y", "black": "30", "white": "20"})
    assert d.message("white") == "20 is too small: it must be above the black point (30 %)"
    assert not d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
    assert not d.widget("gamma").isEnabled()                              # waits for a valid white point
    fill(qtbot, d, {"white": "90", "board_width": "2000"})
    assert "the machine reaches 1015 mm" in d.message("board_width")
    fill(qtbot, d, {**SMALL, "board_width": "80", "board_height": "80"})  # valid answers, impossible design
    d.accept()
    assert d.jobs is None and "do(es) not fit" in d.error.text() and not d.error.isHidden()


def test_relief_from_the_main_window_checks_every_board(qtbot, window, tmp_path):
    w = window()
    d = ReliefDialog(PNG, tmp_path / "out")
    qtbot.addWidget(d)                  # deleted on the GUI thread: a dialog left to the garbage collector
    fill(qtbot, d, SMALL)               # can be freed on the check's worker thread, which crashes Qt
    d.sim.setChecked(False)
    d.exec = lambda: (d.accept(), 1)[1]
    ok, view, _ = run_check(qtbot, w, lambda: w.run_relief(d))
    assert ok, w.report.toPlainText()
    assert w.project.path == (tmp_path / "out" / "waves_sheet1_job.json").resolve()
    assert (tmp_path / "out" / "waves_sheet1.ngc").exists() and (tmp_path / "out" / "waves_sheet2.ngc").exists()
    assert Path(view) == tmp_path / "out" / "waves_sheet2_view.html"


def test_relief_pins_from_the_main_window(qtbot, window, tmp_path, monkeypatch):
    w = window()
    told = []
    monkeypatch.setattr("camcore.gui.main_window.QMessageBox.information", lambda *a: told.append(a[2]))
    d = ReliefDialog(PNG, tmp_path / "out")
    qtbot.addWidget(d)
    fill(qtbot, d, {"mode": "pins"})
    d.exec = lambda: (d.accept(), 1)[1]
    assert w.run_relief(d) and "nothing to machine" in told[0]
    assert (tmp_path / "out" / "waves_pin_plan.svg").exists()


def test_check_runner_never_frees_qt_objects_on_its_thread(qtbot, ex, tmp_path):
    # Qt widgets left in reference cycles are garbage the cyclic collector will find; if it
    # runs on the check's worker thread it frees them there and Qt crashes (this brought the
    # relief GUI tests down). The runner collects on the GUI thread first and keeps automatic
    # collection off until the check is done.
    import gc

    from PySide6.QtWidgets import QLabel, QWidget

    from camcore.gui.runner import CheckRunner

    for _ in range(50):
        w = QWidget()
        w.label = QLabel(w)
        w.cycle = w                         # only the cyclic collector can free it
        del w
    runner = CheckRunner()
    with qtbot.waitSignal(runner.finished, timeout=120_000) as blocker:
        runner.start(ex / "cutout_job.json", tmp_path, sim=False)
        assert not gc.isenabled()
    assert blocker.args[0] is True
    assert gc.isenabled()
