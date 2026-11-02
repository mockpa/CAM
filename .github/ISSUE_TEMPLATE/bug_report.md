---
name: Bug report
about: A wrong toolpath, a failed check, a crash
labels: bug
---

**What happened, and what did you expect?**


**Files to reproduce** (attach them, or paste them in code blocks)
- the job JSON (and the SVG / DXF / STL / image it uses)
- the machine JSON, if not one from `machine/` or `examples/`
- the tool library, if not `examples/tools.json`

**`camcore check` output**
```
paste the full output of: camcore check YOUR_JOB.json
```

**Version**
camcore commit (`git rev-parse --short HEAD`), Python version, OS. Rust kernels installed? (yes/no)

> If a program already ran on a machine: what was cut, and did the preview show the problem?
