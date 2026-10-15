#!/bin/sh
# check FILE  : headless run through LinuxCNC; exits non-zero on any LinuxCNC error
# gui [FILE]  : AXIS GUI in your browser at http://localhost:6080/vnc.html
set -e
MODE=${1:-check}; FILE=${2:-/work/program.ngc}
[ -f /work/tool.tbl ] && cp /work/tool.tbl /home/cnc/sim/tool.tbl
[ -f /work/machine.ini ] && cp /work/machine.ini /home/cnc/sim/camcore_sim.ini

case "$MODE" in
  check)
    sed -i 's/^DISPLAY = .*/DISPLAY = linuxcncrsh/' camcore_sim.ini
    linuxcnc camcore_sim.ini > /tmp/linuxcnc.log 2>&1 &
    exec python3 /usr/local/bin/run_check.py "$FILE"
    ;;
  gui)
    Xvfb :1 -screen 0 1600x1000x24 & export DISPLAY=:1
    x11vnc -display :1 -forever -nopw -quiet &
    websockify --web /usr/share/novnc 6080 localhost:5900 &
    sed -i "s|^OPEN_FILE = .*|OPEN_FILE = $FILE|" camcore_sim.ini
    exec linuxcnc camcore_sim.ini
    ;;
  *) echo "usage: check FILE | gui [FILE]"; exit 2 ;;
esac
