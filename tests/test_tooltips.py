"""Every operation parameter, job key and tool field the app shows has a tooltip."""
from camcore.project import CUTTING_KEYS, OP_KINDS, STRUCT_KEYS, op_params, tool_fields
from camcore.tooltips import CUTTING_TIPS, JOB_TIPS, OP_TIPS, PARAM_TIPS, STRUCT_TIPS, TOOL_TIPS, param_tip


def test_every_op_parameter_has_a_tip():
    missing = [f"{k}.{p.name}" for k in OP_KINDS for p in op_params(k) if not param_tip(k, p.name)]
    assert not missing, f"add these to camcore/tooltips.py PARAM_TIPS: {missing}"
    assert set(OP_TIPS) == set(OP_KINDS)


def test_op_specific_tips_name_real_parameters():
    for key in PARAM_TIPS:
        if "." in key:
            kind, name = key.split(".")
            assert name in {p.name for p in op_params(kind)}, key


def test_op_specific_tip_wins():
    assert param_tip("pocket", "method").startswith("offset: loops")
    assert param_tip("vcarve", "method").startswith("offset: inset contours")
    assert param_tip("drill", "depth") == PARAM_TIPS["depth"]


def test_structure_job_tool_and_cutting_tips():
    assert set(STRUCT_KEYS) - {"op", "fit_with"} <= set(STRUCT_TIPS)
    for key in ("post", "material", "tool_library", "machine", "tool_order", "stock", "defaults", "rotary",
                "index", "index_clearance", "post_options"):
        assert JOB_TIPS[key]
    assert {f.name for f in tool_fields()} | {"name"} <= set(TOOL_TIPS)
    assert set(CUTTING_KEYS) | {"material"} <= set(CUTTING_TIPS)
