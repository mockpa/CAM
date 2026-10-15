#!/usr/bin/env python3
"""Run one program through the LinuxCNC simulator and fail on any error.

LinuxCNC itself enforces soft limits, tool table lookups, G-code syntax and
modal rules, so this catches what camcore's own checker might miss. Feed and
rapid overrides are raised so long programs finish quickly.
"""
import subprocess
import sys
import time

prog = sys.argv[1]
# Wait for LinuxCNC to come up. The linuxcnc module attaches to the tool table
# mmap once per process; if that happens before LinuxCNC is up, every later
# stat.poll() fails. So probe from child processes and import it only after.
PROBE = "import linuxcnc; linuxcnc.stat().poll()"
for _ in range(120):
    if subprocess.run([sys.executable, "-c", PROBE], capture_output=True).returncode == 0:
        break
    time.sleep(0.5)
else:
    sys.exit("LinuxCNC did not start, see /tmp/linuxcnc.log")

import linuxcnc  # noqa: E402

s = linuxcnc.stat()
c, e = linuxcnc.command(), linuxcnc.error_channel()


def wait(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s.poll()
        if cond():
            return True
        time.sleep(0.05)
    return False


def step(what, cond, timeout=30):
    if not wait(cond, timeout):
        sys.exit(f"LinuxCNC setup failed: {what}")


c.state(linuxcnc.STATE_ESTOP_RESET); c.wait_complete()
step("estop reset", lambda: s.task_state == linuxcnc.STATE_ESTOP_RESET)
c.state(linuxcnc.STATE_ON); c.wait_complete()
step("machine on", lambda: s.task_state == linuxcnc.STATE_ON)
c.mode(linuxcnc.MODE_MANUAL); c.wait_complete()
c.teleop_enable(False); c.wait_complete()
c.home(-1); c.wait_complete()
step("homing", lambda: all(s.homed[j] for j in range(s.joints)), 60)
c.mode(linuxcnc.MODE_AUTO); c.wait_complete()
c.program_open(prog); c.wait_complete()
c.feedrate(50.0)                             # 5000 %, capped by MAX_FEED_OVERRIDE
c.maxvel(1e9)
c.auto(linuxcnc.AUTO_RUN, 0)

errors, started, t0 = [], False, time.time()
while True:
    s.poll()
    err = e.poll()
    if err:
        kind, text = err
        if kind in (linuxcnc.NML_ERROR, linuxcnc.OPERATOR_ERROR):
            errors.append(text)
            print(f"LINUXCNC ERROR: {text}", flush=True)
            break
        print(f"linuxcnc: {text}", flush=True)
    if s.interp_state != linuxcnc.INTERP_IDLE:
        started = True
    if started and s.interp_state == linuxcnc.INTERP_IDLE:
        break
    if time.time() - t0 > 3600:
        errors.append("timed out after 1 h"); break
    time.sleep(0.02)

print(f"line {s.current_line}, {time.time() - t0:.0f} s simulated run, "
      f"{'FAILED' if errors else 'OK'}", flush=True)
c.state(linuxcnc.STATE_OFF)
sys.exit(1 if errors else 0)
