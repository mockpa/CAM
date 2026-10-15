#!/bin/sh
# check FILE         : headless run through LinuxCNC; exits non-zero on any LinuxCNC error
# gui [FILE [SCENE]] : AXIS GUI in your browser at http://localhost:6080/vnc.html, plus the
#                      vismach machine model if SCENE (default FILE's NAME_scene.json from
#                      camcore check) exists
set -e
MODE=${1:-check}; FILE=${2:-/work/program.ngc}; SCENE=${3:-${FILE%.ngc}_scene.json}
[ -f /work/tool.tbl ] && cp /work/tool.tbl /home/cnc/sim/tool.tbl
[ -f /work/machine.ini ] && cp /work/machine.ini /home/cnc/sim/camcore_sim.ini
[ -f /work/machine.var ] && cp /work/machine.var /home/cnc/sim/sim.var

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
    [ -f "$SCENE" ] && python3 /usr/local/bin/camcore_vismach.py --setup "$SCENE" /home/cnc/sim
    sed -i "s|^OPEN_FILE = .*|OPEN_FILE = $FILE|" camcore_sim.ini
    if [ ! -f "$SCENE" ]; then exec linuxcnc camcore_sim.ini; fi
    linuxcnc camcore_sim.ini & LCNC=$!
    until halcmd getp joint.0.pos-fb >/dev/null 2>&1; do sleep 0.5; done
    python3 /usr/local/bin/camcore_vismach.py "$SCENE" &
    wait $LCNC
    ;;
  *) echo "usage: check FILE | gui [FILE [SCENE]]"; exit 2 ;;
esac
