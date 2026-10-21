"""Relief designs (lamellas, 3D relief, pins) as a front end that writes ordinary jobs.

Nothing here makes toolpaths or G-code: like `wizard.py`, the relief code turns a
design into job JSON plus geometry and hands it to `check.check`. See
docs/relief_plan.md.
"""
