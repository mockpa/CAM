# Contributing to camcore

Thanks for helping. camcore makes programs for real machines, so changes are held to a few firm rules.

## Setup
```bash
git clone https://github.com/mockpa/CAM.git && cd CAM
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
# needs a Rust toolchain: the 3-axis 3D operations require surface_kernel; verify_kernel is optional
# (verify falls back to pure Python without it, just slower)
pip install ./rust/verify_kernel ./rust/surface_kernel
```
OpenCAMLib (only for `tests/test_surface_kernel.py`) comes from conda-forge: see `environment.yml`.
The LinuxCNC simulator is a Docker image; see `sim/README.md`.

## Rules
- `pytest -q` must pass. Add tests with every feature or fix.
- Every change that alters generated programs keeps `camcore check` green on the examples, with the sim
  (see `CLAUDE.md` for the example programs that are compared byte for byte and how to regenerate them).
- Changes to posts, rotary math or `verify` need a test that pins the exact G-code or numbers.
- Never weaken a `verify` check to make a test pass. If a check is wrong, fix it and add a test that shows why.
- New source files start with `# SPDX-License-Identifier: GPL-3.0-or-later` (`//` in Rust).

`CLAUDE.md` describes the architecture and the boundaries between its parts.

## License of contributions
camcore is licensed under GPL-3.0-or-later. By contributing you agree that your contribution is licensed
under the same terms (inbound = outbound).
