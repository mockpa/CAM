"""Lamella geometry (relief plan phase 3): strips of a height field as part outlines."""
import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from camcore.relief import lamella as lam
from camcore.relief.heightfield import HeightField, prepare, sample_relief
from camcore.relief.lamella import LamellaSettings


def ramp_x(w=100.0, h=60.0, n=201):
    """h = X / W: dark at the left edge, white at the right."""
    return HeightField(np.tile(np.linspace(0, 1, n, dtype=np.float32), (61, 1)), w, h)


def ramp_back(w=100.0, h=60.0):
    """h = Y / H: dark at the front, white at the back (row 0)."""
    return HeightField(np.tile(np.linspace(1, 0, 61, dtype=np.float32)[:, None], (1, 101)), w, h)


@pytest.mark.parametrize("span, t, gap, n, off", [
    (100, 18, 0, 5, 5.0),          # 5 x 18 = 90, 5 mm each side
    (100, 18, 2, 5, 1.0),          # 5 x 18 + 4 x 2 = 98
    (100, 20, 0, 5, 0.0),          # exact fit
    (100, 18, 10, 3, 13.0),        # 3 x 18 + 2 x 10 = 74
    (10, 18, 0, 0, 0.0),           # nothing fits
])
def test_layout_centres_the_strips(span, t, gap, n, off):
    assert lam.layout(span, t, gap) == (n, pytest.approx(off))


def test_vertical_strips_average_across_their_thickness():
    st = LamellaSettings(direction="v", thickness=18, gap=2, base=10, relief=40, step=2)
    parts = lam.lamellas(ramp_x(), st)
    assert [p.label for p in parts] == ["V01", "V02", "V03", "V04", "V05"]
    assert [p.offset for p in parts] == pytest.approx([1, 21, 41, 61, 81])
    for p in parts:
        assert p.length == 60 and len(p.s) == 31
        # h = X / 100 is linear, so the average over the strip is its centre
        assert p.profile == pytest.approx(np.full(31, 10 + 40 * (p.offset + 9) / 100), abs=1e-5)


def test_horizontal_strips_run_from_the_back():
    st = LamellaSettings(direction="h", thickness=20, gap=0, base=10, relief=40, step=5)
    parts = lam.lamellas(ramp_back(), st)
    assert [p.label for p in parts] == ["H01", "H02", "H03"]
    assert [p.offset for p in parts] == [0, 20, 40]
    for p, centre_y in zip(parts, (50, 30, 10)):            # back row first
        assert p.length == 100 and len(p.s) == 21
        assert p.profile == pytest.approx(np.full(21, 10 + 40 * centre_y / 60), abs=1e-5)


def test_profile_along_the_strip_and_centre_line_sampling():
    F = ramp_back()                                           # vertical strips: rises towards the back
    (p,) = lam.lamellas(F, LamellaSettings(thickness=100, base=5, relief=60, step=1.5))
    assert p.s[0] == 0 and p.s[-1] == 60 and np.allclose(np.diff(p.s), 60 / 40)
    assert p.profile == pytest.approx(5 + 60 * p.s / 60, abs=1e-5)
    # a bump only 2 mm wide across the strip: averaged over 18 mm, or read on the centre line
    z = np.zeros((61, 101), np.float32)
    z[:, 49:52] = 1                                           # X49..51
    F = HeightField(z, 100, 60)
    st = dict(thickness=18, base=10, relief=10, step=10)
    avg = lam.lamellas(F, LamellaSettings(average=True, **st))[2]
    mid = lam.lamellas(F, LamellaSettings(average=False, **st))[2]
    assert avg.offset == 41 and mid.profile == pytest.approx(np.full(7, 20.0))        # centre X50
    assert avg.profile == pytest.approx(np.full(7, 10 + 10 * 1 / 5), abs=1e-5)        # 1 of 5 samples


def test_outline_is_a_valid_closed_part_within_tolerance():
    F = prepare(sample_relief(240, 160), 120, 80, blur=1)
    st = LamellaSettings(thickness=12, base=15, relief=30, step=0.5, tolerance=0.02)
    parts = lam.lamellas(F, st)
    assert len(parts) == 10
    for p in parts:
        o = p.outline
        assert o.is_valid and o.exterior.is_ccw and len(o.interiors) == 0
        assert o.bounds == pytest.approx((0, 0, p.length, p.height), abs=0.02)
        coords = np.array(o.exterior.coords)
        assert np.all(coords[:, 1] >= 0) and coords[0].tolist() == [0, 0] and coords[1].tolist() == [p.length, 0]
        top = o.exterior.difference(Polygon([(0, -1), (p.length, -1), (p.length, 0.001), (0, 0.001)]))
        d = [top.distance(Point(s, z)) for s, z in zip(p.s, p.profile)]
        assert max(d) <= 0.02 + 1e-9                        # every sample within the tolerance
        assert len(coords) < len(p.s)                       # and fewer points than samples


def test_cut_outline_fills_valleys_narrower_than_the_cutter():
    # a 4 mm wide, 10 mm deep slot and a 10 mm wide one in a 20 mm high part
    s = np.linspace(0, 60, 601)
    prof = np.full_like(s, 20.0)
    prof[(s > 10) & (s < 14)] = 10
    prof[(s > 30) & (s < 40)] = 10
    part = lam.outline(s, prof, 0.001)
    cut = lam.cut_outline(part, 3.0)                        # 6 mm cutter
    assert cut.contains(part)
    assert cut.contains(Point(12, 15))                       # narrow slot: filled
    assert not cut.contains(Point(35, 11))                   # wide slot: open down to its corners' radius
    assert cut.contains(Point(30.5, 10.5))                   # ... which stay round (3 mm)
    assert lam.cut_outline(part, 0) is part


@pytest.mark.parametrize("kw", [dict(direction="d"), dict(thickness=0), dict(gap=-1), dict(base=0),
                                dict(relief=-1), dict(step=0), dict(tolerance=0)])
def test_bad_settings_are_refused(kw):
    with pytest.raises(ValueError):
        lam.lamellas(ramp_x(), LamellaSettings(**kw))


def test_too_narrow_artwork_has_no_strips():
    assert lam.lamellas(ramp_x(w=10), LamellaSettings(thickness=18)) == []
