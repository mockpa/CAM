# CAM software: architecture and phased plan

Goal: a CAM application that generates toolpaths for **2.5D**, **3D**, **4-axis (rotary)** and **V-carve**, and outputs G-code for hobby/prosumer controllers.

Defaults chosen (change any of them and the plan adapts):

| Decision | Default | Why |
|---|---|---|
| Core language | **Python 3.11+** | Fast to iterate, best geometry ecosystem; hot loops can move to Rust later via PyO3 without changing the API. |
| First controller | **LinuxCNC** (Paul confirmed), GRBL also included | Full RS274NGC, real tool changes, native A axis for 4-axis work. |
| Platform | Desktop, cross-platform (Win/macOS/Linux) | Qt runs everywhere. |
| Units | Internally mm, convert at import/export | One canonical unit avoids a class of bugs. |

---

## 1. Architecture

```
            +-------------------------------------------------+
            |  UI (PySide6/Qt + VTK or pythreejs-free OpenGL) |
            +---------------------+---------------------------+
                                  | project / job model (JSON)
+------------+   +----------------v-----------------+   +--------------+
|  Import    |-->|  Core: geometry + operations     |-->|  Post-proc.  |--> .nc / .gcode
| DXF / SVG  |   |  2.5D | V-carve | 3D | 4-axis    |   |  GRBL, LCNC, |
| STL / STEP |   +----------------+-----------------+   |  Mach3 ...   |
+------------+                    | Toolpath (IR)       +--------------+
                                  v
                     +---------------------------+
                     | Simulation / verification |
                     | (heightmap or dexel stock)|
                     +---------------------------+
```

Layers, each a Python package with no upward dependencies:

1. **`geom`**: 2D polygons (Shapely/GEOS, Clipper2 for robust offsets), 3D meshes (trimesh + numpy), arcs/splines flattened to polylines with a chord tolerance.
2. **`tools`**: tool library (flat endmill, ball nose, bull nose, V-bit, engraving). Each tool knows its profile `r(z)`; this one function drives V-carve depth, 3D drop-cutter and simulation.
3. **`ops`**: operations take geometry + tool + params and emit a **Toolpath IR**: an ordered list of moves (`rapid`, `linear`, `arc`, with X/Y/Z/A and feed). Operations never write G-code.
4. **`post`**: post-processors turn IR into dialect-specific G-code (header/footer, arc support, line numbers, A-axis wrapping, decimal precision).
5. **`sim`**: material-removal simulation against stock. Heightmap (Z-buffer) for 3-axis; cylindrical heightmap (radius per angle x length) for 4-axis.
6. **`app`**: job model (JSON, versioned), CLI, later the Qt GUI.

The IR is the key contract: every mode produces the same IR, and simulation, post, time estimation and visualization consume only IR.

## 2. Toolpath algorithms per mode

### 2.5D (MVP)
- **Profile (inside/outside/on)**: offset contour by tool radius, multiple step-downs, optional tabs, climb vs conventional by contour orientation, lead-in/out arcs.
- **Pocket**: offset (contour-parallel) clearing: repeated inward offsets by stepover until empty, linked inner-to-outer. Later: zig-zag/raster, then adaptive/trochoidal clearing (constant engagement, big win for metal).
- **Drill**: peck / chip-break cycles (G81/G83) from points or circles.
- **Engrave / follow path**: on-line at fixed depth.
- Libraries: Shapely (GEOS) now; **pyclipper/Clipper2** if offset robustness or speed becomes an issue.

### V-carve (MVP)
- Depth at a point = distance to nearest boundary / tan(half V angle).
- **MVP method: offset-based.** Inset the shape in small increments `d`; each inset contour is cut at depth `d / tan(a/2)`. Simple, robust, and produces correct depth everywhere; resolution set by step size.
- **Phase 2: medial-axis method** (done, `"method": "medial"`, `scipy.spatial.Voronoi`). Compute the Voronoi/medial axis (OpenVoronoi, or `scipy.spatial.Voronoi` on densely sampled boundary, or Boost.Polygon Voronoi via bindings) and drive the bit along the axis with depth = clearance radius. Gives sharp corners and far fewer moves.
- **Flat-bottom V-carve**: cap depth at a max; clear the flat area with a pocket op using an endmill, V-bit only does the walls.
- **Inlays** (done, `"op": "inlay"`, see `docs/inlay_plan.md`): pocket = flat-bottom V-carve; plug = mirrored design, V-carved from `pocket_depth - glue_gap` (`vcarve` `start_depth`) with the flange cleared flat; fit check on verify's simulated stock (`camcore/inlay_fit.py`). Later: a `rough_tool` for wide designs (a big end mill first, the clear tool only for the rest).

### 3D (Phase 3)
- **Drop-cutter** is the core primitive: for each (x, y), drop the tool along Z until it touches the mesh (vertex, edge, facet tests per tool shape). Accelerate with a BVH/kd-tree over triangles.
- Strategies built on it: **raster/parallel finishing**, **waterline** (push-cutter at fixed Z; best for steep walls), **roughing** = Z-level slices of the mesh (trimesh section) fed into 2.5D pocketing with stock-to-leave.
- Library: **OpenCAMLib** (C++ with Python bindings, LGPL) implements drop-cutter and waterline for all standard cutters. Recommended to use it first rather than re-implementing; replace or extend later if needed.
- Performance: this is where Python runs out. Plan for numba or a Rust extension (PyO3) for drop-cutter if OpenCAMLib does not fit.

### 4-axis rotary (Phase 4)
- **Indexed (3+1)**: rotate A to fixed angles, run normal 3-axis ops per face. Cheap once 3D works.
- **Wrapped**: map a flat 2.5D/V-carve design onto a cylinder: X stays X, Y becomes A = Y / r * 180/pi. Reuses all 2.5D/V-carve code; only the post converts Y to A.
- **Continuous rotary 3D**: cylindrical drop-cutter: for each (x, A), cast the tool radially toward the axis and find contact with the mesh. Raster along X with A stepping (or helical spiral).
- Post must handle A-axis feed (inverse-time G93 or degree-based feed per controller) and angle wrap.

## 3. G-code post-processing
- Template per controller: header (units G21, G90, G17, spindle/coolant), tool change behavior, footer.
- Options: arc output (G2/G3) vs linearized, precision, max line length, line numbers, comments style, A-axis support, inverse-time feed.
- LinuxCNC first (done: T/M6, G43, G64 P, A axis), GRBL done; later Mach3/4 and a generic Fanuc-ish dialect. For rotary feeds LinuxCNC supports G93 inverse-time, to add in Phase 4.
- Arc fitting: fit G2/G3 to polyline runs within tolerance; reduces file size and smooths motion on GRBL.

## 4. Simulation and verification
- Heightmap stock (numpy 2D array of top Z) swept by tool profile along each move; render as a mesh. Fast enough in numpy for 2.5D/3D at hobby resolution.
- Rotary: cylindrical heightmap.
- Checks: rapid moves into stock (collision), plunges deeper than tool flute length, holder collision (later), out-of-bounds travel vs machine envelope.
- Time estimate from IR with acceleration-aware model (start simple: length / feed).

## 5. Must-haves from Paul

### Easy rotary, DeskProto-style
DeskProto is the benchmark: STL in, a few wizard steps, rotary G-code out. Our version:

1. **Rotary wizard** (GUI, also scriptable as a JSON job): pick STL or flat design, set stock diameter and length, place the part on the axis (auto-centre on the axis, rotate the model about X), choose roughing and finishing tools from the library, set accuracy (coarse/normal/fine), preview, export.
2. **Three rotary modes**, all exposed in the wizard:
   - **Wrapped** (done): flat 2.5D/V-carve designs wrapped around the stock, Y becomes A. Text and patterns on cylinders.
   - **Indexed 3+1**: lock A at N angles (e.g. 0/90/180/270 or 2 sides) and run normal 3-axis ops per face. Good for parts with flat faces.
   - **Continuous rotary from STL** (DeskProto's headline feature): cylindrical drop-cutter. For each X and A, find how far the tool can approach the axis before touching the mesh (`r(x, A)` = max over the tool footprint). Toolpath strategies: *A-raster* (spin A at fixed X, step X), *X-raster* (sweep X at fixed A, step A) and *helical* (continuous spiral, smoothest). Roughing = the same grid at a coarse step with stock-to-leave and Z step-down layers from the stock radius inwards.
3. **Feeds on the A axis**: G93 inverse-time on LinuxCNC (done), so the surface speed is correct at any radius.
4. **Machine setup**: rotary axis along X, Z0 on the rotary centreline (LinuxCNC convention), configurable A direction and max A speed.

Implementation: numpy-vectorised cylindrical heightmap of the mesh (ray-cast per (x, A) cell with trimesh/embree), then tool-shape dilation on that grid. This is far simpler than general 3D drop-cutter and can come before full 3-axis 3D.

### Automatic tool changer
- **Tool library** (done, JSON): name, tool number = ATC pocket, diameter, type, V angle, flute length. Later in the GUI: holder geometry and stick-out for collision checks, default feeds/speeds per material.
- **Multi-tool jobs** (done): every operation names its tool; the post emits `M5`, `G53 G0 Z0`, `Tn M6`, `G43 Hn` per change, and stages the next tool (`Tnext` right after `M6`) so a carousel can pre-select it.
- **Tool-change minimisation** (done, opt-in): `"tool_order": "minimize_changes"` groups operations by tool; later make this dependency-aware (keep clearing before V-carve, roughing before finishing).
- **To add**: optional tool-length probe after change (`G38.2` routine or a LinuxCNC remap subroutine call), and a check that every tool number exists in the LinuxCNC tool table (`.tbl` import).

### Virtual testing before the real machine
Every program goes through three layers before it touches the machine. Layers one and two are built and working; layer three is written but not yet run.

1. **camcore dry run** (done, `camcore-verify`): an independent replay of the G-code against a machine model (travel, work offset, tool table, A axis) and a stock model (block heightmap or cylinder radius map) with live material removal. It flags rapids into uncut material, cutting with the spindle stopped, travel-limit violations, unknown tools, missing G43, cuts deeper than the flute length, and G93 moves without F. Runs in seconds.
2. **Animated viewer** (done, `--view`): a self-contained HTML player of the program with a DRO readout, material removal, A-axis rotation, tool changes and clickable check results.
3. **Real LinuxCNC in a container** (written, untested, `sim/`): Debian's `linuxcnc-uspace` on a simulated XYZA machine with instant tool changes. `check` mode runs headless through the `linuxcnc` Python module at 5000 % feed override and fails on any LinuxCNC error (soft limits, unknown tool, interpreter errors). `gui` mode shows the AXIS GUI in a browser via noVNC.
4. **Next**: a vismach 3D model of Paul's actual machine (gantry, spindle, rotary chuck, ATC rack) inside the sim for collision checks against the machine itself, holder/shank collision in the camcore checker, and a `camcore check` command that runs layers one and three for every program as a gate before it gets copied to the machine.

## 6. Phases (re-ordered for rotary)

| Phase | Scope | Exit criterion |
|---|---|---|
| **0. Scaffold** (done) | Packages, IR, posts, tests, CLI | `pytest` green; CLI produces G-code from a JSON job |
| **1. MVP 2.5D + V-carve + ATC** (mostly done) | SVG import, profile, pocket, drill, V-carve, tool library, LinuxCNC ATC post, wrapped rotary, dry-run checker + animated viewer, LinuxCNC sim container | Both examples pass the dry run and the LinuxCNC sim, then cut on Paul's machine |
| **2. Rotary from STL** | STL import, cylindrical heightmap, rotary roughing + finishing (A-raster, helical), indexed 3+1 | Carve a rotary STL (e.g. a chess piece) |
| **3. GUI + simulation** | Qt app with the rotary wizard, embedded viewer, dry-run checks on every export, tool library editor; vismach model of the real machine | Load STL, run the wizard, preview, verify, export, all in the app |
| **4. 3-axis 3D** | Z-level roughing, raster + waterline finishing (OpenCAMLib) | Carve a relief from STL |
| **5. Polish** | Medial-axis V-carve, adaptive clearing, arcs (G2/G3) and arc fitting, tabs, ramps, DXF, bitmap reliefs, dependency-aware tool ordering | Feature parity with typical hobby CAM |

## 7. MVP scaffold (done, Phase 0 + most of Phase 1)

Located in `/mnt/project-files/cam/camcore/`. See its README for usage. Contains: tool library, Toolpath IR, profile/pocket/V-carve/drill ops, wrapped rotary, LinuxCNC (ATC, G93) and GRBL posts, CLI that takes a JSON job, and tests.

## 8. Open questions for Paul
1. Controller: LinuxCNC (answered). Assumed: rotary is A around X, Z0 on the axis centreline. What kind of ATC (carousel, rack) and does it need a tool-length probe after each change?
2. Platform: desktop GUI (which OS), or web-based?
3. Is there a GitHub repo to put this in, or should one be created?
4. Primary material/use: wood signs (V-carve heavy) or metal (adaptive clearing matters more)?
5. Commercial or personal/open-source? This affects library choices (OpenCAMLib is LGPL, CGAL is GPL/commercial).
