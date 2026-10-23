//! Compiled inner loop of `camcore.verify.Stock`: the box heightmap and the rotary
//! radius map, cut and probed by the exact tool bodies.
//!
//! This is a line-by-line port of the Python reference in `camcore/verify.py`
//! (`_cells`, `_profile`, `_ray_cells`, `_ray_interval`, `surface`,
//! `interference`, `cut` and the step loop of `run.move`). It must stay that: the
//! Python code is the specification, `tests/test_verify_kernel.py` replays
//! programs through both and compares every issue and every stock cell. Same
//! operations in the same order, so the results are the same floating point
//! numbers (Python `%` and `round` are reproduced, see `py_mod` / `round_ties_even`).
//!
//! verify is the independent check of the ops and posts: nothing here may be
//! shared with code that makes toolpaths.

use numpy::ndarray::{ArrayView2, ArrayViewMut2};
use numpy::{PyReadonlyArray2, PyReadwriteArray2};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::f64::consts::PI;

const DEG_TO_RAD: f64 = PI / 180.0; // math.radians / np.radians
const RAD_TO_DEG: f64 = 180.0 / PI; // math.degrees
const EPS: f64 = 1e-12;
const INF: f64 = f64::INFINITY;
/// A sweep step touching more cells than this splits its rows over the CPU cores
/// (fine grids: the inlay fit, the V-carve groove tests).
const PARALLEL_CELLS: usize = 16384;

#[allow(dead_code)]
const FLAT: i32 = 0; // and any other kind: a flat end
const BALL: i32 = 1;
const BULL: i32 = 2;
const VBIT: i32 = 3;

/// A tool as `Stock._tool_key` passes it: (kind, radius, corner radius, V angle).
#[derive(Clone, Copy)]
struct Tool {
    kind: i32,
    r: f64,
    rc: f64,
    v_angle: f64,
}

impl Tool {
    fn new(t: (i32, f64, f64, f64)) -> Tool {
        Tool { kind: t.0, r: t.1, rc: t.2, v_angle: t.3 }
    }
    /// math.tan(math.radians(v_angle) / 2)
    fn tan_half(&self) -> f64 {
        (self.v_angle * DEG_TO_RAD / 2.0).tan()
    }
}

/// The grid as `Stock._grid` passes it: (rotary, x0, y0, res, a_res).
#[derive(Clone, Copy)]
struct Grid {
    rotary: bool,
    x0: f64,
    y0: f64,
    res: f64,
    a_res: f64,
}

impl Grid {
    fn new(g: (bool, f64, f64, f64, f64)) -> Grid {
        Grid { rotary: g.0, x0: g.1, y0: g.2, res: g.3, a_res: g.4 }
    }
}

/// The uncut stock as `Stock._surf` passes it: (kind 0 box / 1 cylinder / 2 bar,
/// top or radius, y0, y1, z0, z1).
#[derive(Clone, Copy)]
struct Surf {
    kind: i32,
    top: f64,
    y0: f64,
    y1: f64,
    z0: f64,
    z1: f64,
}

// numpy's maximum/minimum propagate NaN, f64::max/min do not.
#[inline]
fn nmax(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a >= b {
        a
    } else {
        b
    }
}
#[inline]
fn nmin(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a <= b {
        a
    } else {
        b
    }
}
/// np.clip(v, 0, None)
#[inline]
fn clip0(v: f64) -> f64 {
    nmax(v, 0.0)
}

/// Python's float `%` (and np.remainder): the result has the divisor's sign.
#[inline]
fn py_mod(a: f64, b: f64) -> f64 {
    let m = a % b;
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m + b
        } else {
            m
        }
    } else {
        0.0f64.copysign(b)
    }
}

// --- box stock ---------------------------------------------------------------

/// Stock._profile: height of the cutter surface above its tip at radial distance d.
#[inline]
fn profile(t: &Tool, tan_half: f64, d: f64) -> f64 {
    match t.kind {
        VBIT => d / tan_half,
        BALL => t.r - clip0(t.r * t.r - d * d).sqrt(),
        BULL => {
            let e = clip0(d - (t.r - t.rc));
            t.rc - clip0(t.rc * t.rc - e * e).sqrt()
        }
        _ => 0.0,
    }
}

/// Stock._cells: index ranges of the cells within distance r, or None.
#[inline]
fn box_range(shape: (usize, usize), g: &Grid, x: f64, y: f64, r: f64) -> Option<(usize, usize, usize, usize)> {
    let i0 = ((x - r - g.x0) / g.res).floor() as i64;
    let i1 = ((x + r - g.x0) / g.res).ceil() as i64;
    let (i0, i1) = (i0.max(0), i1.min(shape.0 as i64 - 1));
    if i0 > i1 {
        return None;
    }
    let j0 = ((y - r - g.y0) / g.res).floor() as i64;
    let j1 = ((y + r - g.y0) / g.res).ceil() as i64;
    let (j0, j1) = (j0.max(0), j1.min(shape.1 as i64 - 1));
    if j0 > j1 {
        return None;
    }
    Some((i0 as usize, i1 as usize, j0 as usize, j1 as usize))
}

/// The columns of row offset dx that can lie within r of the tool axis, inside j0..=j1:
/// the chord's ends with one cell to spare (each cell is still tested exactly), or
/// None when the row is out of reach (hypot(dx, dy) >= |dx|).
#[inline]
fn box_row(g: &Grid, y: f64, r: f64, dx: f64, j0: usize, j1: usize) -> Option<(usize, usize)> {
    if dx.abs() > r {
        return None;
    }
    let half = (r * r - dx * dx).max(0.0).sqrt();
    let lo = (((y - half - g.y0) / g.res).floor() as i64 - 1).max(j0 as i64);
    let hi = (((y + half - g.y0) / g.res).ceil() as i64 + 1).min(j1 as i64);
    if lo > hi {
        None
    } else {
        Some((lo as usize, hi as usize))
    }
}

fn box_interference(h: &ArrayView2<f64>, g: &Grid, x: f64, y: f64, z: f64, t: &Tool) -> f64 {
    let Some((i0, i1, j0, j1)) = box_range(h.dim(), g, x, y, t.r) else {
        return -INF;
    };
    let th = t.tan_half();
    let mut best = -INF;
    let mut any = false;
    for i in i0..=i1 {
        let dx = (g.x0 + i as f64 * g.res) - x;
        let Some((ja, jb)) = box_row(g, y, t.r, dx, j0, j1) else { continue };
        for j in ja..=jb {
            let dy = (g.y0 + j as f64 * g.res) - y;
            let d = dx.hypot(dy);
            if d <= t.r {
                let v = h[[i, j]] - (z + profile(t, th, d));
                best = if any { nmax(best, v) } else { v };
                any = true;
            }
        }
    }
    best
}

fn box_cut(h: &mut ArrayViewMut2<f64>, g: &Grid, x: f64, y: f64, z: f64, t: &Tool) {
    let Some((i0, i1, j0, j1)) = box_range(h.dim(), g, x, y, t.r) else {
        return;
    };
    let th = t.tan_half();
    for i in i0..=i1 {
        let dx = (g.x0 + i as f64 * g.res) - x;
        let Some((ja, jb)) = box_row(g, y, t.r, dx, j0, j1) else { continue };
        for j in ja..=jb {
            let dy = (g.y0 + j as f64 * g.res) - y;
            let d = dx.hypot(dy);
            if d <= t.r {
                let cell = &mut h[[i, j]];
                *cell = nmin(*cell, z + profile(t, th, d));
            }
        }
    }
}

/// Row i of a box sweep step: cells more than 0.01 into the tool are cut at once (the
/// step cuts); cells the cut would lower by 0.01 or less go to `band` unless `hit`.
#[inline]
#[allow(clippy::too_many_arguments)]
fn box_step_row(row: &mut [f64], i: usize, g: &Grid, x: f64, y: f64, z: f64, t: &Tool, th: f64,
                j0: usize, j1: usize, mut hit: bool, band: &mut Vec<(usize, usize, f64)>) -> bool {
    let dx = (g.x0 + i as f64 * g.res) - x;
    let Some((ja, jb)) = box_row(g, y, t.r, dx, j0, j1) else { return hit };
    for j in ja..=jb {
        let dy = (g.y0 + j as f64 * g.res) - y;
        let d = dx.hypot(dy);
        if d <= t.r {
            let surf = z + profile(t, th, d);
            let cell = &mut row[j];
            let diff = *cell - surf;
            if diff > 0.01 {
                hit = true;
                *cell = surf;
            } else if diff > 0.0 {
                if hit {
                    *cell = surf;
                } else {
                    band.push((i, j, surf));
                }
            }
        }
    }
    hit
}

/// Rows i0..=i1 of a C-ordered grid (nx x ny) as one mutable slice per row.
fn rows_of<'a>(h: &'a mut ArrayViewMut2<'_, f64>, i0: usize, i1: usize) -> Option<&'a mut [f64]> {
    let ny = h.dim().1;
    h.as_slice_mut().map(|s| &mut s[i0 * ny..(i1 + 1) * ny])
}

/// Run `row_fn` over rows i0..=i1, in parallel when the step is big; returns whether
/// any row hit and the band cells of rows that did not know of a hit yet.
fn step_rows<F>(h: &mut ArrayViewMut2<f64>, i0: usize, i1: usize, cells: usize, band: &mut Vec<(usize, usize, f64)>,
                row_fn: F) -> bool
where
    F: Fn(&mut [f64], usize, bool, &mut Vec<(usize, usize, f64)>) -> bool + Sync,
{
    let ny = h.dim().1;
    band.clear();
    if cells > PARALLEL_CELLS {
        if let Some(rows) = rows_of(h, i0, i1) {
            let (hit, pending) = rows
                .par_chunks_mut(ny)
                .enumerate()
                .fold(|| (false, Vec::new()), |(hit, mut b), (k, row)| {
                    let hit = row_fn(row, i0 + k, hit, &mut b);
                    (hit, b)
                })
                .reduce(|| (false, Vec::new()), |(ha, mut ba), (hb, bb)| {
                    ba.extend(bb);
                    (ha || hb, ba)
                });
            *band = pending;
            return hit;
        }
    }
    let mut hit = false;
    for i in i0..=i1 {
        let mut row = h.row_mut(i);
        hit = match row.as_slice_mut() {
            Some(r) => row_fn(r, i, hit, band),
            None => {
                let mut tmp = row.to_vec();
                let hh = row_fn(&mut tmp, i, hit, band);
                row.assign(&numpy::ndarray::ArrayView1::from(&tmp[..]));
                hh
            }
        };
    }
    hit
}

/// One step of a sweep on box stock in one pass: the same as `if interference > 0.01:
/// cut`. A cell more than 0.01 into the tool means the step cuts, so it is cut at
/// once; cells the cut would lower by 0.01 or less wait in `band` until a cell decides.
fn box_step(h: &mut ArrayViewMut2<f64>, g: &Grid, x: f64, y: f64, z: f64, t: &Tool,
            band: &mut Vec<(usize, usize, f64)>) -> bool {
    let Some((i0, i1, j0, j1)) = box_range(h.dim(), g, x, y, t.r) else {
        return false;
    };
    let th = t.tan_half();
    let cells = (i1 - i0 + 1) * (j1 - j0 + 1);
    let hit = step_rows(h, i0, i1, cells, band, |row, i, hit, b| box_step_row(row, i, g, x, y, z, t, th, j0, j1, hit, b));
    if hit {
        for &(i, j, surf) in band.iter() {
            h[[i, j]] = surf;
        }
    }
    hit
}

/// The same for rotary stock (cut: a ray whose outer end lies in the body is cut back
/// to where it enters). With columns listed twice it falls back to the two passes.
fn ray_step(h: &mut ArrayViewMut2<f64>, g: &Grid, x: f64, a: f64, z: f64, t: &Tool, y: f64,
            band: &mut Vec<(usize, usize, f64)>) -> bool {
    let Some((i0, i1, rays)) = ray_cells(h.dim(), g, x, a, z, t.r, y) else {
        return false;
    };
    if rays.len() > h.dim().1 {
        let hit = ray_interference(&h.view(), g, x, a, z, t, y) > 0.01;
        if hit {
            ray_cut(h, g, x, a, z, t, y);
        }
        return hit;
    }
    let cells = (i1 - i0 + 1) * rays.len();
    let hit = step_rows(h, i0, i1, cells, band, |row, i, mut hit, b| {
        let dx = (g.x0 + i as f64 * g.res) - x;
        for &(j, phi) in &rays {
            let (lo, hi) = ray_interval(dx, phi, z, t, y);
            let cell = &mut row[j];
            let sub = *cell;
            let lowers = lo < sub && hi >= sub - 1e-9;
            if lo <= hi && nmin(sub, hi) - lo > 0.01 {
                hit = true;
                if lowers {
                    *cell = lo;
                }
            } else if lowers {
                if hit {
                    *cell = lo;
                } else {
                    b.push((i, j, lo));
                }
            }
        }
        hit
    });
    if hit {
        for &(i, j, lo) in band.iter() {
            h[[i, j]] = lo;
        }
    }
    hit
}

// --- rotary stock --------------------------------------------------------------

/// Stock._ray_cells: the X range and the rays (column, angle phi from the vertical).
fn ray_cells(shape: (usize, usize), g: &Grid, x: f64, a: f64, z: f64, r: f64, y: f64)
             -> Option<(usize, usize, Vec<(usize, f64)>)> {
    let i0 = (((x - r - g.x0) / g.res).floor() as i64).max(0);
    let i1 = (((x + r - g.x0) / g.res).ceil() as i64).min(shape.0 as i64 - 1);
    if i0 > i1 {
        return None;
    }
    let (lo_deg, hi_deg) = if z > 0.0 {
        (-((y + r).atan2(z) * RAD_TO_DEG), -((y - r).atan2(z) * RAD_TO_DEG))
    } else {
        (-90.0, 90.0)
    };
    let (mid, half) = ((lo_deg + hi_deg) / 2.0, (hi_deg - lo_deg) / 2.0);
    let span = (half / g.a_res).ceil() as i64 + 1;
    let jc = (py_mod(a + mid, 360.0) / g.a_res).round_ties_even() as i64;
    let na = shape.1 as i64;
    let rays = (jc - span..=jc + span)
        .map(|k| {
            let j = k.rem_euclid(na);
            let phi = (py_mod(j as f64 * g.a_res - a + 180.0, 360.0) - 180.0) * DEG_TO_RAD;
            (j as usize, phi)
        })
        .collect();
    Some((i0 as usize, i1 as usize, rays))
}

/// lin(a, b) of Stock._ray_interval: {rho : a * rho >= b}
#[inline]
fn lin(a: f64, b: f64) -> (f64, f64) {
    let q = b / if a.abs() > EPS { a } else { 1.0 };
    let flat = if b <= 0.0 { -INF } else { INF };
    (
        if a > EPS { q } else if a < -EPS { -INF } else { flat },
        if a > EPS { INF } else if a < -EPS { q } else { -flat },
    )
}

#[inline]
fn meet2(p: (f64, f64), q: (f64, f64)) -> (f64, f64) {
    (nmax(p.0, q.0), nmin(p.1, q.1))
}

#[inline]
fn meet3(p: (f64, f64), q: (f64, f64), s: (f64, f64)) -> (f64, f64) {
    // np.maximum.reduce([p, q, s]) folds left to right
    (nmax(nmax(p.0, q.0), s.0), nmin(nmin(p.1, q.1), s.1))
}

/// hull of two intervals: the pieces of a convex set along the ray
#[inline]
fn hull2(p: (f64, f64), q: (f64, f64)) -> (f64, f64) {
    let lo = nmin(if p.0 <= p.1 { p.0 } else { INF }, if q.0 <= q.1 { q.0 } else { INF });
    let hi = nmax(if p.0 <= p.1 { p.1 } else { -INF }, if q.0 <= q.1 { q.1 } else { -INF });
    (lo, hi)
}

/// Stock._ray_interval for one ray: where it passes through the tool body.
fn ray_interval(dx: f64, phi: f64, z: f64, t: &Tool, y: f64) -> (f64, f64) {
    let r = t.r;
    let (sg, c) = (-phi.sin(), phi.cos());
    let w2 = r * r - dx * dx;
    let w = clip0(w2).sqrt();
    let base = meet3(lin(sg, y - w), lin(-sg, -(y + w)), (0.0, INF));
    let (lo, hi) = if t.kind == BALL {
        let upper = meet2(base, lin(c, z + r));
        let b = sg * y + c * (z + r);
        let disc = b * b - (y * y + (z + r) * (z + r) - w2);
        let sq = clip0(disc).sqrt();
        let ok = disc >= 0.0;
        hull2(upper, meet2((if ok { b - sq } else { INF }, if ok { b + sq } else { -INF }), base))
    } else if t.kind == VBIT {
        let kv = 1.0 / t.tan_half();
        let above = meet2(base, lin(c, z));
        // cone: (rho c - z)^2 >= kv^2 (dx^2 + (rho sg - y)^2), on the side rho c >= z
        let qa = c * c - kv * kv * sg * sg;
        let qb = -2.0 * z * c + 2.0 * kv * kv * sg * y;
        let qc = z * z - kv * kv * (dx * dx + y * y);
        let disc = qb * qb - 4.0 * qa * qc;
        let sq = clip0(disc).sqrt();
        let quad = qa.abs() > EPS;
        let den = 2.0 * if quad { qa } else { 1.0 };
        let (ra, rb) = ((-qb - sq) / den, (-qb + sq) / den);
        let (r1, r2) = (nmin(ra, rb), nmax(ra, rb));
        let (lin_lo, lin_hi) = lin(qb, -qc);
        let real = disc >= 0.0;
        let p1 = (
            if quad { if qa > 0.0 { -INF } else if real { r1 } else { INF } } else { lin_lo },
            if quad {
                if qa > 0.0 { if real { r1 } else { INF } } else if real { r2 } else { -INF }
            } else {
                lin_hi
            },
        );
        let two = quad && qa > 0.0 && real;
        let p2 = (if two { r2 } else { INF }, if two { INF } else { -INF });
        hull2(meet2(above, p1), meet2(above, p2))
    } else {
        meet2(base, lin(c, z))
    };
    let miss = w2 < 0.0 || c <= EPS || !(lo <= hi);
    if miss {
        (INF, -INF)
    } else {
        (nmax(lo, 0.0), hi)
    }
}

fn ray_interference(h: &ArrayView2<f64>, g: &Grid, x: f64, a: f64, z: f64, t: &Tool, y: f64) -> f64 {
    let Some((i0, i1, rays)) = ray_cells(h.dim(), g, x, a, z, t.r, y) else {
        return -INF;
    };
    let mut best = -INF;
    let mut any = false;
    for i in i0..=i1 {
        let dx = (g.x0 + i as f64 * g.res) - x;
        for &(j, phi) in &rays {
            let (lo, hi) = ray_interval(dx, phi, z, t, y);
            if lo <= hi {
                let v = nmin(h[[i, j]], hi) - lo;
                best = if any { nmax(best, v) } else { v };
                any = true;
            }
        }
    }
    best
}

fn ray_cut(h: &mut ArrayViewMut2<f64>, g: &Grid, x: f64, a: f64, z: f64, t: &Tool, y: f64) {
    let Some((i0, i1, rays)) = ray_cells(h.dim(), g, x, a, z, t.r, y) else {
        return;
    };
    // numpy evaluates the whole selection before it writes (h[idx] = where(..., sub)),
    // so a column listed twice (a tiny radius, rays round more than the full turn) is
    // computed from the old values and the last write wins
    let repeats = rays.len() > h.dim().1;
    let mut new = Vec::with_capacity(if repeats { (i1 - i0 + 1) * rays.len() } else { 0 });
    for i in i0..=i1 {
        let dx = (g.x0 + i as f64 * g.res) - x;
        for &(j, phi) in &rays {
            let (lo, hi) = ray_interval(dx, phi, z, t, y);
            let sub = h[[i, j]];
            let v = if lo < sub && hi >= sub - 1e-9 { lo } else { sub };
            if repeats {
                new.push((i, j, v));
            } else {
                h[[i, j]] = v;
            }
        }
    }
    for (i, j, v) in new {
        h[[i, j]] = v;
    }
}

// --- both ------------------------------------------------------------------------

fn interference(h: &ArrayView2<f64>, g: &Grid, x: f64, u: f64, z: f64, t: &Tool, y: f64) -> f64 {
    if g.rotary {
        ray_interference(h, g, x, u, z, t, y)
    } else {
        box_interference(h, g, x, u, z, t)
    }
}

fn cut(h: &mut ArrayViewMut2<f64>, g: &Grid, x: f64, u: f64, z: f64, t: &Tool, y: f64) {
    if g.rotary {
        ray_cut(h, g, x, u, z, t, y)
    } else {
        box_cut(h, g, x, u, z, t)
    }
}

/// Stock.surface: top of the uncut stock at machine Y = y with the part at A = a.
fn surface(s: &Surf, y: f64, a: f64) -> f64 {
    match s.kind {
        0 => s.top,
        1 => clip0(s.top * s.top - y * y).sqrt(),
        _ => {
            let (ca, sa) = ((a * DEG_TO_RAD).cos(), (a * DEG_TO_RAD).sin());
            let corners = [(s.y0, s.z0), (s.y1, s.z0), (s.y1, s.z1), (s.y0, s.z1)];
            let pts: Vec<(f64, f64)> = corners.iter().map(|&(py, pz)| (py * ca + pz * sa, -py * sa + pz * ca)).collect();
            let mut top = -INF;
            for k in 0..4 {
                let ((ya, za), (yb, zb)) = (pts[k], pts[(k + 1) % 4]);
                if ya.min(yb) - 1e-9 <= y && y <= ya.max(yb) + 1e-9 {
                    let vertical = (yb - ya).abs() < 1e-12;
                    let v1 = if vertical { za } else { za + (zb - za) * (y - ya) / (yb - ya) };
                    let v2 = if vertical { zb } else { -INF };
                    // Python's max(top, v1, v2): the first of equal values
                    if v1 > top {
                        top = v1;
                    }
                    if v2 > top {
                        top = v2;
                    }
                }
            }
            top
        }
    }
}

// --- Python API ----------------------------------------------------------------------

/// How far material pokes into the cutter body at (x, u, z) (<= 0: clear), as
/// Stock.interference. u is Y on box stock, A on rotary stock (y the tool's Y).
#[pyfunction]
fn interference_at(h: PyReadonlyArray2<f64>, grid: (bool, f64, f64, f64, f64), tool: (i32, f64, f64, f64),
                   x: f64, u: f64, z: f64, y: f64) -> f64 {
    interference(&h.as_array(), &Grid::new(grid), x, u, z, &Tool::new(tool), y)
}

/// Remove what the tool body at (x, u, z) occupies, as Stock.cut.
#[pyfunction]
fn cut_at(mut h: PyReadwriteArray2<f64>, grid: (bool, f64, f64, f64, f64), tool: (i32, f64, f64, f64),
          x: f64, u: f64, z: f64, y: f64) {
    cut(&mut h.as_array_mut(), &Grid::new(grid), x, u, z, &Tool::new(tool), y)
}

/// The stock part of one straight move, as the step loop in verify.run's `move`:
/// steps s = 1..steps at t = s / steps from `start` to `end` (x, y, z, a). Where
/// material reaches more than 0.01 mm into the tool it is cut, unless `stop`, which
/// ends the sweep at the first such step without cutting. `flute` not NaN: also look
/// for the first cutting step deeper below the uncut surface than flute + 1e-6.
/// Returns (first step in material or 0, first too deep step or 0, its depth).
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn sweep(mut h: PyReadwriteArray2<f64>, grid: (bool, f64, f64, f64, f64), surf: (i32, f64, f64, f64, f64, f64),
         tool: (i32, f64, f64, f64), start: (f64, f64, f64, f64), end: (f64, f64, f64, f64), steps: u64,
         stop: bool, flute: f64) -> (u64, u64, f64) {
    let (g, t) = (Grid::new(grid), Tool::new(tool));
    let s = Surf { kind: surf.0, top: surf.1, y0: surf.2, y1: surf.3, z0: surf.4, z1: surf.5 };
    let mut h = h.as_array_mut();
    let (mut hit, mut deep_step, mut deep) = (0u64, 0u64, 0.0f64);
    let mut band = Vec::new();
    for k in 1..=steps {
        let f = k as f64 / steps as f64;
        let px = start.0 + (end.0 - start.0) * f;
        let py = start.1 + (end.1 - start.1) * f;
        let pz = start.2 + (end.2 - start.2) * f;
        let pa = start.3 + (end.3 - start.3) * f;
        let u = if g.rotary { pa } else { py };
        if stop {
            if interference(&h.view(), &g, px, u, pz, &t, py) > 0.01 {
                hit = k;
                break;
            }
            continue;
        }
        let cuts = if g.rotary {
            ray_step(&mut h, &g, px, pa, pz, &t, py, &mut band)
        } else {
            box_step(&mut h, &g, px, py, pz, &t, &mut band)
        };
        if cuts {
            if hit == 0 {
                hit = k;
            }
            if !flute.is_nan() && deep_step == 0 {
                let d = surface(&s, py, pa) - pz;
                if d > flute + 1e-6 {
                    deep_step = k;
                    deep = d;
                }
            }
        }
    }
    (hit, deep_step, deep)
}

#[pymodule]
fn camcore_verify_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(interference_at, m)?)?;
    m.add_function(wrap_pyfunction!(cut_at, m)?)?;
    m.add_function(wrap_pyfunction!(sweep, m)?)?;
    m.add("VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
