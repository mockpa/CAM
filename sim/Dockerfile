# LinuxCNC 2.9.4 simulator (XYZA + simulated tool changer) for dry-running camcore programs.
# Uses Debian's own linuxcnc-uspace package; no real-time kernel or hardware needed.
# trixie has the 2.9.4 release; bookworm only has a 2.9.0~pre1 git snapshot.
FROM debian:trixie

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        linuxcnc-uspace python3 xvfb x11vnc novnc websockify xauth procps \
    && rm -rf /var/lib/apt/lists/*

# rtapi_app is setuid root, but Docker drops CAP_IPC_OWNER, so it can't open the
# HAL shared memory that user cnc creates. The sim doesn't need realtime anyway.
RUN chmod u-s /usr/bin/rtapi_app && useradd -m cnc
COPY config/ /home/cnc/sim/
COPY run_check.py entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run_check.py && chown -R cnc /home/cnc/sim
USER cnc
WORKDIR /home/cnc/sim
EXPOSE 6080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["check", "/work/program.ngc"]
