# camcore tutorials

Step-by-step walkthroughs of every workflow, with screenshots. Each one uses an example from
`examples/`, so you can follow along without your own files. The [user manual](../MANUAL.md) is the
reference: every key, every option.

**Start here:** the [setup](../MANUAL.md#2-setup), then tutorials 1 and 2.

| # | Tutorial | You learn |
|---|---|---|
| 1 | [Your first job](01-first-job.md) | `camcore check`, the 3D viewer, changing a job, the final check with the simulator |
| 2 | [The desktop app](02-desktop-app.md) | editing operations, shapes, job settings and tools; Preview and Export |
| 3 | [Your own drawing: pocket and cut out](03-own-drawings-and-cutouts.md) | DXF/SVG import, layers, profiles with lead-ins and tabs, notes |
| 4 | [V-carving](04-vcarve.md) | flat-bottomed V-carves, offset vs medial |
| 5 | [Adaptive pocket clearing](05-adaptive-pocket.md) | deep pockets in aluminium at constant load |
| 6 | [Inlays](06-inlay.md) | pocket and plug jobs, the fit check, glue-up |
| 7 | [3D carving from an STL](07-3d-from-stl.md) | roughing, raster and waterline finishing |
| 8 | [Wrapping around a cylinder](08-wrapped-rotary.md) | a flat job on the A axis; rotary setup |
| 9 | [Indexed 3+1](09-indexed.md) | the same work on several faces of a bar |
| 10 | [Turned parts from an STL](10-rotary-from-stl.md) | the rotary wizard, on the command line and in the app |
| 11 | [Relief designs from a picture](11-relief-designs.md) | lamellas, 3D reliefs and pins with `camcore relief` |
| 12 | [The simulator and the machine](12-simulator-and-machine.md) | AXIS with the machine model; G54, tools and proving a program |
| 13 | [When a check fails](13-when-a-check-fails.md) | reading the report, viewing the failing program, common errors |

All commands are run from the `camcore` folder in a terminal set up as in
[tutorial 1, step 1](01-first-job.md#step-1-open-a-terminal-in-camcore).

## Updating the screenshots

The pictures in `img/` are made by a script from the examples, so they can be redone when the app or
the viewer changes:

```bash
python docs/tutorials/screenshots.py              # all of them (about 5 minutes)
python docs/tutorials/screenshots.py --no-sim     # without Docker: no simulator pictures
python docs/tutorials/screenshots.py --reuse --only app_   # only the app pictures, reusing earlier checks
```

It needs Google Chrome or Chromium (for the viewer and the drawings), PySide6 (the app, grabbed
offscreen) and Docker with the `camcore-sim` image (the simulator pictures). Its working files go to
`out/tutorials/`.
