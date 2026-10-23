//! Drop-cutter and waterline on triangle meshes, for camcore's 3-axis 3D operations
//! (camcore/surface.py). Replaces OpenCAMLib.
//!
//! Cutters. Every cutter is a solid of revolution about the vertical tool axis,
//! infinitely long (the shank as wide as the cutter): material anywhere above the
//! profile blocks it. Its profile f(r), the height of the cutter surface above its
//! lowest point at radial distance r <= R, belongs to one of two families, each a base
//! shape grown by a ball of radius L >= 0 (its exact offset):
//!   disc of radius a:          flat (L = 0), ball (a = 0), bull nose (a = R - rc, L = rc)
//!   cone of radius a, slope k: V-bit (L = 0, k = 1 / tan(half angle)), grown V-bit (L > 0)
//! f is convex and non-decreasing in r for all of them.
//!
//! Drop. With the tool axis at (x, y), the lowest point of the cutter can come down to
//!     z = max over model points p within R of the axis of  p.z - f(|p - axis|).
//! On one triangle the function p.z - f(|p - axis|) is concave (a plane minus a convex
//! function of the distance), so its maximum over the triangle (inside the disc) lies
//! either where it is stationary inside the face, or on an edge, or at a vertex:
//! - vertex: v.z - f(d), exact;
//! - face: the contact radius r* where f'(r*) equals the facet's slope, towards
//!   its uphill side; counted when that point lies on the facet, exact;
//! - edge: along the edge the function is concave in the parameter t, so its maximum is
//!   where its derivative changes sign: found by bisection to the last bit.
//! Vertical facets have no face term (their edges and vertices cover them).
//!
//! Waterline. The cutter at tip height z is blocked at (x, y) where the drop height
//! there is above z. The loops are the boundary of that region: the drop height on a
//! grid `sampling` apart, contour cells as in marching squares (a saddle decided by the
//! cell centre), and each contour point found by bisection along its grid edge, so it
//! lies on the true boundary to within 1e-10 mm. camcore.surface checks every point.

use numpy::{IntoPyArray, PyArray1, PyReadonlyArray2};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

const INF: f64 = f64::INFINITY;
/// mm: model points this far beyond the cutter radius still count as touching it (at the
/// profile's edge height), so an exact tangency (a pass one radius beside a wall) is not
/// lost to rounding. On the safe side: the cutter only ever stays higher.
const REACH: f64 = 1e-9;

// --- cutters -----------------------------------------------------------------------

#[derive(Clone, Copy, Debug)]
struct Cutter {
    cone: bool,
    a: f64, // base radius (disc or cone)
    l: f64, // offset (ball radius grown around the base)
    k: f64, // cone slope: height per radius
    r: f64, // outer radius a + l
    q: f64, // sqrt(1 + k^2)
    r1: f64, // grown cone: end of the ball tip
    r2: f64, // grown cone: end of the straight flank
}

impl Cutter {
    fn new(cone: bool, a: f64, l: f64, k: f64) -> PyResult<Cutter> {
        if !(a >= 0.0 && l >= 0.0 && a + l > 0.0 && (!cone || k > 0.0)) || !(a + l).is_finite() {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "bad cutter: cone={cone} a={a} l={l} k={k}"
            )));
        }
        let q = (1.0 + k * k).sqrt();
        let r1 = if cone { l * k / q } else { 0.0 };
        Ok(Cutter { cone, a, l, k, r: a + l, q, r1, r2: a + r1 })
    }

    /// Height of the cutter surface above its lowest point at radial distance d.
    #[inline]
    fn f(&self, d: f64) -> f64 {
        let d = d.clamp(0.0, self.r);
        let (a, l) = (self.a, self.l);
        if !self.cone {
            if d <= a {
                0.0
            } else {
                let e = d - a;
                l - (l * l - e * e).max(0.0).sqrt()
            }
        } else if l == 0.0 {
            self.k * d
        } else if d <= self.r1 {
            l - (l * l - d * d).max(0.0).sqrt()
        } else if d <= self.r2 {
            self.k * d + l - l * self.q
        } else {
            let e = d - a;
            self.k * a + l - (l * l - e * e).max(0.0).sqrt()
        }
    }

    /// f'(d) (infinite where the profile turns vertical).
    #[inline]
    fn fp(&self, d: f64) -> f64 {
        let (a, l) = (self.a, self.l);
        let arc = |e: f64| {
            let den = l * l - e * e;
            if den <= 0.0 {
                INF
            } else {
                e / den.sqrt()
            }
        };
        if !self.cone {
            if d <= a {
                0.0
            } else {
                arc(d - a)
            }
        } else if l == 0.0 {
            self.k
        } else if d <= self.r1 {
            arc(d)
        } else if d <= self.r2 {
            self.k
        } else {
            arc(d - a)
        }
    }

    /// The radius where the profile's slope equals s (a plane of slope s touches there).
    #[inline]
    fn contact_radius(&self, s: f64) -> f64 {
        let (a, l) = (self.a, self.l);
        let ball = |s: f64| l * s / (1.0 + s * s).sqrt();
        if s <= 0.0 {
            0.0
        } else if !self.cone {
            if l == 0.0 {
                a
            } else {
                a + ball(s)
            }
        } else if l == 0.0 {
            if s <= self.k {
                0.0
            } else {
                a
            }
        } else if s <= self.k {
            ball(s)
        } else {
            a + ball(s)
        }
    }
}

// --- the mesh -------------------------------------------------------------------------

struct Face {
    a: [f64; 3],
    b: [f64; 3],
    c: [f64; 3],
    area2: f64, // twice the signed XY area
    gx: f64,    // plane: z = a.z + gx (x - a.x) + gy (y - a.y)
    gy: f64,
    zmax: f64,
    bbox: [f64; 4],
}

struct Edge {
    p0: [f64; 3],
    p1: [f64; 3],
    zmax: f64,
    bbox: [f64; 4],
}

/// Items binned by the cell of their bounding box's low corner; a query widens its
/// range by the largest binned extent. Items larger than that are always tested.
struct Bins {
    x0: f64,
    y0: f64,
    c: f64,
    nx: usize,
    ny: usize,
    margin: f64,
    start: Vec<u32>,
    items: Vec<u32>,
    large: Vec<u32>,
}

impl Bins {
    fn build(boxes: &[[f64; 4]], c: f64, extent: [f64; 4], large_ext: f64) -> Bins {
        let [x0, y0, x1, y1] = extent;
        let nx = (((x1 - x0) / c).floor() as usize + 1).max(1);
        let ny = (((y1 - y0) / c).floor() as usize + 1).max(1);
        let cell = |b: &[f64; 4]| {
            let i = (((b[0] - x0) / c).floor().max(0.0) as usize).min(nx - 1);
            let j = (((b[1] - y0) / c).floor().max(0.0) as usize).min(ny - 1);
            j * nx + i
        };
        let mut count = vec![0u32; nx * ny + 1];
        let mut large = Vec::new();
        let mut margin: f64 = 0.0;
        for (k, b) in boxes.iter().enumerate() {
            let ext = (b[2] - b[0]).max(b[3] - b[1]);
            if ext > large_ext {
                large.push(k as u32);
            } else {
                margin = margin.max(ext);
                count[cell(b) + 1] += 1;
            }
        }
        for k in 1..count.len() {
            count[k] += count[k - 1];
        }
        let start = count.clone();
        let mut fill = count;
        let mut items = vec![0u32; start[nx * ny] as usize];
        for (k, b) in boxes.iter().enumerate() {
            let ext = (b[2] - b[0]).max(b[3] - b[1]);
            if ext <= large_ext {
                let c = cell(b);
                items[fill[c] as usize] = k as u32;
                fill[c] += 1;
            }
        }
        Bins { x0, y0, c, nx, ny, margin, start, items, large }
    }

    /// Every item whose box can meet [xlo, xhi] x [ylo, yhi] (and some that cannot).
    #[inline]
    fn query(&self, xlo: f64, ylo: f64, xhi: f64, yhi: f64, mut f: impl FnMut(usize)) {
        for &k in &self.large {
            f(k as usize);
        }
        let ci = |v: f64, o: f64, n: usize| (((v - o) / self.c).floor().max(0.0) as usize).min(n - 1);
        if xhi < self.x0 || yhi < self.y0 {
            return;
        }
        let (i0, i1) = (ci(xlo - self.margin, self.x0, self.nx), ci(xhi, self.x0, self.nx));
        let (j0, j1) = (ci(ylo - self.margin, self.y0, self.ny), ci(yhi, self.y0, self.ny));
        for j in j0..=j1 {
            let row = j * self.nx;
            for k in self.start[row + i0]..self.start[row + i1 + 1] {
                f(self.items[k as usize] as usize);
            }
        }
    }
}

#[inline]
fn overlaps(b: &[f64; 4], x: f64, y: f64, r: f64) -> bool {
    b[0] <= x + r && b[2] >= x - r && b[1] <= y + r && b[3] >= y - r
}

/// Highest value of p.z - f(|p - axis|) along an edge (None: the edge is out of reach
/// or vertical; its end points are vertices and tested as such).
#[inline]
fn edge_max(c: &Cutter, p0: &[f64; 3], p1: &[f64; 3], x: f64, y: f64) -> Option<f64> {
    let (ex, ey, ez) = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]);
    let alpha = ex * ex + ey * ey;
    if alpha < 1e-24 {
        return None;
    }
    let (ux, uy) = (p0[0] - x, p0[1] - y);
    let b = ux * ex + uy * ey;
    let c0 = ux * ux + uy * uy;
    // reach: the line within r of the axis; a line up to REACH beyond it (a tangency
    // lost to rounding) touches at its nearest point
    let disc = b * b - alpha * (c0 - c.r * c.r);
    if disc < -alpha * 2.0 * c.r * REACH {
        return None;
    }
    let sq = disc.max(0.0).sqrt();
    let (tlo, thi) = (((-b - sq) / alpha).max(0.0), ((-b + sq) / alpha).min(1.0));
    if tlo > thi {
        return None;
    }
    let g = |t: f64| {
        let (dx, dy) = (ux + t * ex, uy + t * ey);
        p0[2] + t * ez - c.f((dx * dx + dy * dy).sqrt())
    };
    // g' = ez - f'(d) d', with d' = (u . e) / d; g is concave, so g' falls along the edge
    let gp = |t: f64| {
        let (dx, dy) = (ux + t * ex, uy + t * ey);
        let d = (dx * dx + dy * dy).sqrt();
        if d < 1e-300 {
            return ez;
        }
        let dd = (dx * ex + dy * ey) / d;
        if dd == 0.0 {
            ez
        } else {
            ez - c.fp(d) * dd
        }
    };
    let mut best = g(tlo).max(g(thi));
    if thi > tlo && gp(tlo) > 0.0 && gp(thi) < 0.0 {
        let (mut lo, mut hi) = (tlo, thi);
        for _ in 0..80 {
            let m = 0.5 * (lo + hi);
            if m <= lo || m >= hi {
                break;
            }
            if gp(m) > 0.0 {
                lo = m;
            } else {
                hi = m;
            }
        }
        best = best.max(g(lo)).max(g(hi));
    }
    Some(best)
}

#[pyclass(frozen)]
struct Mesh {
    verts: Vec<[f64; 3]>,
    faces: Vec<Face>,
    edges: Vec<Edge>,
    vbins: Bins,
    fbins: Bins,
    ebins: Bins,
    extent: [f64; 4],
    vertical: usize,
    grids: Mutex<Vec<(Vec<u64>, Arc<Vec<f64>>)>>, // waterline drop-height grids, newest last
}

impl Mesh {
    fn build(verts: Vec<[f64; 3]>, tris: Vec<[usize; 3]>) -> PyResult<Mesh> {
        if verts.is_empty() || tris.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err("empty mesh"));
        }
        let mut extent = [INF, INF, -INF, -INF];
        for v in &verts {
            if !(v[0].is_finite() && v[1].is_finite() && v[2].is_finite()) {
                return Err(pyo3::exceptions::PyValueError::new_err("mesh vertex is not finite"));
            }
            extent = [extent[0].min(v[0]), extent[1].min(v[1]), extent[2].max(v[0]), extent[3].max(v[1])];
        }
        let bbox = |ps: &[&[f64; 3]]| {
            let mut b = [INF, INF, -INF, -INF];
            for p in ps {
                b = [b[0].min(p[0]), b[1].min(p[1]), b[2].max(p[0]), b[3].max(p[1])];
            }
            b
        };
        let mut faces = Vec::new();
        let mut vertical = 0;
        let mut edge_set: Vec<(usize, usize)> = Vec::with_capacity(tris.len() * 3);
        for t in &tris {
            if t.iter().any(|&i| i >= verts.len()) {
                return Err(pyo3::exceptions::PyValueError::new_err("face index out of range"));
            }
            for (i, j) in [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])] {
                if verts[i] != verts[j] {
                    edge_set.push((i.min(j), i.max(j)));
                }
            }
            let (a, b, c) = (verts[t[0]], verts[t[1]], verts[t[2]]);
            let (bx, by, bz) = (b[0] - a[0], b[1] - a[1], b[2] - a[2]);
            let (cx, cy, cz) = (c[0] - a[0], c[1] - a[1], c[2] - a[2]);
            let area2 = bx * cy - by * cx;
            if area2.abs() < 2e-12 {
                vertical += 1; // no face term: its edges and vertices cover it
                continue;
            }
            faces.push(Face {
                a,
                b,
                c,
                area2,
                gx: (bz * cy - cz * by) / area2,
                gy: (bx * cz - cx * bz) / area2,
                zmax: a[2].max(b[2]).max(c[2]),
                bbox: bbox(&[&a, &b, &c]),
            });
        }
        edge_set.sort_unstable();
        edge_set.dedup();
        let edges: Vec<Edge> = edge_set
            .iter()
            .map(|&(i, j)| Edge { p0: verts[i], p1: verts[j], zmax: verts[i][2].max(verts[j][2]), bbox: bbox(&[&verts[i], &verts[j]]) })
            .collect();
        // cell size: four times the 90th percentile item size, at most 2048 cells across
        let mut sizes: Vec<f64> = edges.iter().map(|e| (e.bbox[2] - e.bbox[0]).max(e.bbox[3] - e.bbox[1])).collect();
        sizes.sort_unstable_by(|p, q| p.partial_cmp(q).unwrap());
        let p90 = if sizes.is_empty() { 1.0 } else { sizes[(sizes.len() * 9) / 10] };
        let span = (extent[2] - extent[0]).max(extent[3] - extent[1]).max(1e-6);
        let cell = (4.0 * p90).max(span / 2048.0).max(1e-6);
        let large = 4.0 * cell;
        let vboxes: Vec<[f64; 4]> = verts.iter().map(|v| [v[0], v[1], v[0], v[1]]).collect();
        let fboxes: Vec<[f64; 4]> = faces.iter().map(|f| f.bbox).collect();
        let eboxes: Vec<[f64; 4]> = edges.iter().map(|e| e.bbox).collect();
        Ok(Mesh {
            vbins: Bins::build(&vboxes, cell, extent, large),
            fbins: Bins::build(&fboxes, cell, extent, large),
            ebins: Bins::build(&eboxes, cell, extent, large),
            verts,
            faces,
            edges,
            extent,
            vertical,
            grids: Mutex::new(Vec::new()),
        })
    }

    /// The lowest point of the cutter with its axis at (x, y): the drop height, or
    /// `floor` where nothing higher is in reach. With `above`, stops as soon as the
    /// answer is known to be above `floor` (returns a value > floor then).
    fn drop_one(&self, c: &Cutter, x: f64, y: f64, floor: f64, above: bool) -> f64 {
        let r = c.r + REACH;
        let mut best = floor;
        let mut done = false;
        self.vbins.query(x - r, y - r, x + r, y + r, |i| {
            if done {
                return;
            }
            let p = &self.verts[i];
            if p[2] <= best {
                return;
            }
            let (dx, dy) = (p[0] - x, p[1] - y);
            let d2 = dx * dx + dy * dy;
            if d2 <= r * r {
                let v = p[2] - c.f(d2.sqrt());
                if v > best {
                    best = v;
                    done = above;
                }
            }
        });
        if done {
            return best;
        }
        self.fbins.query(x - r, y - r, x + r, y + r, |i| {
            if done {
                return;
            }
            let f = &self.faces[i];
            if f.zmax <= best || !overlaps(&f.bbox, x, y, r) {
                return;
            }
            let s = f.gx.hypot(f.gy);
            let rc = c.contact_radius(s);
            let (qx, qy) = if s > 0.0 { (x + rc * f.gx / s, y + rc * f.gy / s) } else { (x, y) };
            // inside the facet (XY, edges included)
            let w0 = (f.b[0] - f.a[0]) * (qy - f.a[1]) - (f.b[1] - f.a[1]) * (qx - f.a[0]);
            let w1 = (f.c[0] - f.b[0]) * (qy - f.b[1]) - (f.c[1] - f.b[1]) * (qx - f.b[0]);
            let w2 = (f.a[0] - f.c[0]) * (qy - f.c[1]) - (f.a[1] - f.c[1]) * (qx - f.c[0]);
            let eps = -1e-12 * f.area2.abs();
            let sg = f.area2.signum();
            if w0 * sg >= eps && w1 * sg >= eps && w2 * sg >= eps {
                let v = f.a[2] + f.gx * (qx - f.a[0]) + f.gy * (qy - f.a[1]) - c.f(rc);
                if v > best {
                    best = v;
                    done = above;
                }
            }
        });
        if done {
            return best;
        }
        self.ebins.query(x - r, y - r, x + r, y + r, |i| {
            if done {
                return;
            }
            let e = &self.edges[i];
            if e.zmax <= best || !overlaps(&e.bbox, x, y, r) {
                return;
            }
            // nearest XY distance from the axis to the edge: a bound before the real work
            let (ex, ey) = (e.p1[0] - e.p0[0], e.p1[1] - e.p0[1]);
            let (ux, uy) = (x - e.p0[0], y - e.p0[1]);
            let l2 = ex * ex + ey * ey;
            let t = if l2 > 0.0 { ((ux * ex + uy * ey) / l2).clamp(0.0, 1.0) } else { 0.0 };
            let dmin = (ux - t * ex).hypot(uy - t * ey);
            if dmin > r || e.zmax - c.f(dmin) <= best {
                return;
            }
            if let Some(v) = edge_max(c, &e.p0, &e.p1, x, y) {
                if v > best {
                    best = v;
                    done = above;
                }
            }
        });
        best
    }

    fn grid(&self, c: &Cutter, key: Vec<u64>, gx0: f64, gy0: f64, s: f64, nx: usize, ny: usize) -> Arc<Vec<f64>> {
        if let Some((_, g)) = self.grids.lock().unwrap().iter().find(|(k, _)| *k == key) {
            return g.clone();
        }
        let h: Vec<f64> = (0..nx * ny)
            .into_par_iter()
            .map(|n| self.drop_one(c, gx0 + (n % nx) as f64 * s, gy0 + (n / nx) as f64 * s, -INF, false))
            .collect();
        let h = Arc::new(h);
        let mut grids = self.grids.lock().unwrap();
        grids.push((key, h.clone()));
        if grids.len() > 4 {
            grids.remove(0);
        }
        h
    }

    fn waterline_loops(&self, c: &Cutter, z: f64, s: f64) -> Vec<Vec<(f64, f64)>> {
        let m = c.r + 2.0 * s;
        let gx0 = ((self.extent[0] - m) / s).floor() * s;
        let gy0 = ((self.extent[1] - m) / s).floor() * s;
        let nx = ((self.extent[2] + m - gx0) / s).ceil() as usize + 1;
        let ny = ((self.extent[3] + m - gy0) / s).ceil() as usize + 1;
        let key = vec![c.cone as u64, c.a.to_bits(), c.l.to_bits(), c.k.to_bits(), s.to_bits()];
        let h = self.grid(c, key, gx0, gy0, s, nx, ny);
        let at = |i: usize, j: usize| (gx0 + i as f64 * s, gy0 + j as f64 * s);
        let blocked = |i: usize, j: usize| h[j * nx + i] > z;
        let hcount = (nx - 1) * ny;
        // grid edges where the state changes: horizontal (i, j)-(i+1, j) as j * (nx - 1) + i,
        // vertical (i, j)-(i, j+1) as hcount + j * nx + i
        let mut cross: Vec<usize> = Vec::new();
        for j in 0..ny {
            for i in 0..nx {
                if i + 1 < nx && blocked(i, j) != blocked(i + 1, j) {
                    cross.push(j * (nx - 1) + i);
                }
                if j + 1 < ny && blocked(i, j) != blocked(i, j + 1) {
                    cross.push(hcount + j * nx + i);
                }
            }
        }
        let ends = |id: usize| {
            if id < hcount {
                let (j, i) = (id / (nx - 1), id % (nx - 1));
                ((i, j), (i + 1, j))
            } else {
                let k = id - hcount;
                let (j, i) = (k / nx, k % nx);
                ((i, j), (i, j + 1))
            }
        };
        // each crossing by bisection between its blocked and its free end
        let points: Vec<(f64, f64)> = cross
            .par_iter()
            .map(|&id| {
                let (p, q) = ends(id);
                let (inside, outside) = if blocked(p.0, p.1) { (at(p.0, p.1), at(q.0, q.1)) } else { (at(q.0, q.1), at(p.0, p.1)) };
                let (mut lo, mut hi) = (0.0f64, 1.0f64); // fraction from inside to outside
                for _ in 0..48 {
                    if (hi - lo) * s < 1e-10 {
                        break;
                    }
                    let t = 0.5 * (lo + hi);
                    let (x, y) = (inside.0 + (outside.0 - inside.0) * t, inside.1 + (outside.1 - inside.1) * t);
                    if self.drop_one(c, x, y, z, true) > z {
                        lo = t;
                    } else {
                        hi = t;
                    }
                }
                let t = 0.5 * (lo + hi);
                (inside.0 + (outside.0 - inside.0) * t, inside.1 + (outside.1 - inside.1) * t)
            })
            .collect();
        let index: HashMap<usize, usize> = cross.iter().enumerate().map(|(n, &id)| (id, n)).collect();
        // marching squares: link the crossings of each cell
        let mut link: Vec<Vec<usize>> = vec![Vec::new(); cross.len()];
        let mut pair = |p: usize, q: usize| {
            let (a, b) = (index[&p], index[&q]);
            link[a].push(b);
            link[b].push(a);
        };
        for j in 0..ny - 1 {
            for i in 0..nx - 1 {
                let b = [blocked(i, j), blocked(i + 1, j), blocked(i + 1, j + 1), blocked(i, j + 1)];
                let e = [j * (nx - 1) + i, hcount + j * nx + i + 1, (j + 1) * (nx - 1) + i, hcount + j * nx + i];
                let changed = [b[0] != b[1], b[1] != b[2], b[3] != b[2], b[0] != b[3]];
                let n = changed.iter().filter(|&&v| v).count();
                if n == 2 {
                    let v: Vec<usize> = (0..4).filter(|&k| changed[k]).map(|k| e[k]).collect();
                    pair(v[0], v[1]);
                } else if n == 4 {
                    let (cx, cy) = (gx0 + (i as f64 + 0.5) * s, gy0 + (j as f64 + 0.5) * s);
                    let centre = self.drop_one(c, cx, cy, z, true) > z;
                    if centre == b[0] {
                        pair(e[0], e[1]); // corners 1 and 3 are cut off
                        pair(e[2], e[3]);
                    } else {
                        pair(e[0], e[3]); // corners 0 and 2 are cut off
                        pair(e[1], e[2]);
                    }
                }
            }
        }
        // walk the loops: every crossing lies on an inner grid edge (the outer nodes are
        // free), so the two cells beside it link it once each
        let mut seen = vec![false; cross.len()];
        let mut loops = Vec::new();
        for start in 0..cross.len() {
            if seen[start] {
                continue;
            }
            let mut lp: Vec<(f64, f64)> = Vec::new();
            let (mut prev, mut cur) = (usize::MAX, start);
            while !seen[cur] {
                seen[cur] = true;
                let p = points[cur];
                if lp.last().map_or(true, |&(x, y): &(f64, f64)| (x - p.0).hypot(y - p.1) > 1e-12) {
                    lp.push(p);
                }
                let next = match link[cur].as_slice() {
                    [a, b] => if *a != prev { *a } else { *b },
                    [a] => *a,
                    _ => break,
                };
                prev = cur;
                cur = next;
            }
            if lp.len() > 1 {
                let (f, l) = (lp[0], lp[lp.len() - 1]);
                if (f.0 - l.0).hypot(f.1 - l.1) <= 1e-12 {
                    lp.pop();
                }
            }
            loops.push(lp);
        }
        loops
    }
}

fn cutter_from(t: (bool, f64, f64, f64)) -> PyResult<Cutter> {
    Cutter::new(t.0, t.1, t.2, t.3)
}

#[pymethods]
impl Mesh {
    /// A mesh from vertices (n x 3) and triangles (m x 3 vertex indices).
    #[new]
    fn py_new(vertices: PyReadonlyArray2<f64>, faces: PyReadonlyArray2<i64>) -> PyResult<Mesh> {
        let v = vertices.as_array();
        let f = faces.as_array();
        if v.ncols() != 3 || f.ncols() != 3 {
            return Err(pyo3::exceptions::PyValueError::new_err("vertices and faces must have 3 columns"));
        }
        let verts = v.rows().into_iter().map(|r| [r[0], r[1], r[2]]).collect();
        let mut tris = Vec::with_capacity(f.nrows());
        for r in f.rows() {
            if r.iter().any(|&i| i < 0) {
                return Err(pyo3::exceptions::PyValueError::new_err("negative face index"));
            }
            tris.push([r[0] as usize, r[1] as usize, r[2] as usize]);
        }
        Mesh::build(verts, tris)
    }

    /// Facets left out of the face test because they have no XY area.
    #[getter]
    fn vertical(&self) -> usize {
        self.vertical
    }

    /// Drop height of `cutter` (cone, a, l, k) at each (x, y) row of `xy`, never below `floor`.
    fn drop<'py>(&self, py: Python<'py>, cutter: (bool, f64, f64, f64), xy: PyReadonlyArray2<f64>, floor: f64)
                 -> PyResult<Bound<'py, PyArray1<f64>>> {
        let c = cutter_from(cutter)?;
        let a = xy.as_array();
        if a.ncols() != 2 {
            return Err(pyo3::exceptions::PyValueError::new_err("xy must be n x 2"));
        }
        let pts: Vec<(f64, f64)> = a.rows().into_iter().map(|r| (r[0], r[1])).collect();
        let z: Vec<f64> = py.detach(|| pts.par_iter().map(|&(x, y)| self.drop_one(&c, x, y, floor, false)).collect());
        Ok(z.into_pyarray(py))
    }

    /// Closed loops of axis positions where `cutter` with its lowest point at `z` just
    /// touches the mesh, from a grid `sampling` apart (see the module docs).
    fn waterline(&self, py: Python<'_>, cutter: (bool, f64, f64, f64), z: f64, sampling: f64)
                 -> PyResult<Vec<Vec<(f64, f64)>>> {
        let c = cutter_from(cutter)?;
        if !(sampling > 0.0) {
            return Err(pyo3::exceptions::PyValueError::new_err("sampling must be > 0"));
        }
        let span = (self.extent[2] - self.extent[0] + 2.0 * c.r).max(self.extent[3] - self.extent[1] + 2.0 * c.r);
        let cells = ((self.extent[2] - self.extent[0]) / sampling + 2.0 * c.r / sampling + 8.0)
            * ((self.extent[3] - self.extent[1]) / sampling + 2.0 * c.r / sampling + 8.0);
        if span / sampling > 200_000.0 || cells > 6e7 {
            return Err(pyo3::exceptions::PyValueError::new_err("waterline grid too fine for the model size"));
        }
        Ok(py.detach(|| self.waterline_loops(&c, z, sampling)))
    }
}

#[pymodule]
fn camcore_surface_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Mesh>()?;
    m.add("VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
