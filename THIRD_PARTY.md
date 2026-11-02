# Third-party components

camcore is licensed under GPL-3.0-or-later (see [LICENSE](LICENSE)). It uses the components below,
unmodified. Only three.js is shipped inside this repository; the others are installed by pip, conda or
cargo.

| Component | License | How camcore uses it |
|---|---|---|
| [Shapely](https://github.com/shapely/shapely) | BSD-3-Clause | runtime dependency (2D geometry) |
| [NumPy](https://numpy.org) | BSD-3-Clause | runtime dependency |
| [SciPy](https://scipy.org) | BSD-3-Clause | runtime dependency (Voronoi for medial-axis V-carve) |
| [ezdxf](https://github.com/mozman/ezdxf) | MIT | runtime dependency (DXF import and export) |
| [trimesh](https://github.com/mikedh/trimesh) | MIT | runtime dependency (STL handling) |
| [Pillow](https://python-pillow.org) | MIT-CMU (HPND) | runtime dependency (images for reliefs) |
| [PySide6](https://doc.qt.io/qtforpython/) | LGPL-3.0 | optional, desktop app only (`pip install .[gui]`) |
| [three.js](https://threejs.org) r128 | MIT | vendored in `camcore/vendor/` (license in `camcore/vendor/three.LICENSE`) |
| [PyO3](https://pyo3.rs) | MIT or Apache-2.0 | Rust kernels in `rust/` |
| [Rayon](https://github.com/rayon-rs/rayon) | MIT or Apache-2.0 | Rust kernels in `rust/` |
| [rust-numpy](https://github.com/PyO3/rust-numpy) | BSD-2-Clause | Rust kernels in `rust/` |
| [OpenCAMLib](https://github.com/aewallin/opencamlib) | LGPL-2.1 | tests only: `tests/test_surface_kernel.py` compares against it |
| [LinuxCNC](https://linuxcnc.org) | GPL-2.0-or-later | only inside the simulator Docker image in `sim/` (Debian package) |

The generated `*_view.html` viewer files embed three.js and keep its `@license` header; do not strip it
(`tests/test_license.py` checks this).

`camcore/relief/` is derived from Reliefwerk, the author's earlier relief tool.
