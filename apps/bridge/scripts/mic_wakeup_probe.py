#!/usr/bin/env python3
"""Does the mic multicast start flowing when the remote's wake-up combo is held?

    ssh c3po
    cd ~/c3po/apps/bridge
    SIM_MODE=real ROBOT_HOST=127.0.0.1 DDS_INTERFACE=eth0 \
        uv run python scripts/mic_wakeup_probe.py 60
    # then HOLD L1+L2 on the remote for ~10 s during the window

`mic_probe.py` already settled the idle half: verified join, 0 packets, robot at
rest (docs/ROBOT-HARDWARE.md §8.2). The open half needs the remote held, and a
run where nobody pressed anything looks EXACTLY like a run where the press
changed nothing — both are a column of zeros. That ambiguity is the reason this
script exists rather than a second run of mic_probe.py.

So it reads the buttons too. `rt/wirelesscontroller` is Go2-only on this robot;
the vendor-documented route to remote state on a G1 is the 40-byte
`LowState_.wireless_remote` blob (ROBOT-API.md §9.5), which costs no new
subscription. Decoded here, the run can distinguish:

    press seen + packets  -> GATED ON WAKE-UP, and listen() has a human prereq
    press seen + silence  -> not the gate; the feed is something else entirely
    no press seen         -> INCONCLUSIVE, and the script says so rather than
                             reporting a confident zero

WRITES NOTHING. It counts UDP bytes and reads a DDS field the bridge already
receives. It does not publish remote input: synthesising button presses into a
robot another team is using is not a probe, it is a command.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time

GROUP = "239.168.123.161"
PORT = 5555
IFACE_PREFIX = "192.168.123."
BYTES_PER_SEC = 16000 * 2

# ROBOT-API.md §9.5. The analog L2 axis is a float elsewhere in the blob and is
# NOT this bit — holding the trigger part-way moves the axis without setting it.
BTN_L1 = 0x0002
BTN_L2 = 0x0020
# THE DOCUMENTED HEADER IS WRONG ON THIS FIRMWARE. ROBOT-API.md §9.5 and
# Unitree's own remote_control_data page both give head = {0xFE, 0xEF}; the G1
# in front of us sends {0x55, 0x51}. Measured 2026-08-21 by dumping the raw blob
# from rt/lf/lowstate: idle reads 5551 0000..., and holding L2+R2 reads
# 5551 3000... — 0x0030 = 0x0010|0x0020 = exactly R2|L2 from the documented bit
# table. So the bit masks are right and only the magic bytes are not.
#
# Both are accepted: the vendor value may well be correct on other units, and
# rejecting frames on a magic number is how the first run of this probe reported
# "no controller present" while somebody was holding buttons down.
HEADS = ((0x55, 0x51), (0xFE, 0xEF))

BUTTON_NAMES = {
    0x0001: "R1", 0x0002: "L1", 0x0004: "start", 0x0008: "select",
    0x0010: "R2", 0x0020: "L2", 0x0040: "F1", 0x0080: "F2",
    0x0100: "A", 0x0200: "B", 0x0400: "X", 0x0800: "Y",
    0x1000: "up", 0x2000: "right", 0x4000: "down", 0x8000: "left",
}


def button_names(mask: int) -> str:
    names = [n for bit, n in BUTTON_NAMES.items() if mask & bit]
    return "+".join(names) if names else "-"

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

_buttons = {"mask": 0, "seen_l1l2": False, "any_press": False, "frames": 0, "head_ok": 0}
_lock = threading.Lock()


def decode_buttons(blob: bytes) -> int | None:
    """head[2] then the 16-bit key field, little-endian. None if not a remote frame."""
    if len(blob) < 4:
        return None
    if (blob[0], blob[1]) not in HEADS:
        return None
    return struct.unpack_from("<H", blob, 2)[0]


def lowstate_thread() -> None:
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

    from bridge.sdk import g1_protocol

    def on_lowstate(msg) -> None:  # noqa: ANN001
        blob = bytes(bytearray(msg.wireless_remote))
        mask = decode_buttons(blob)
        with _lock:
            _buttons["frames"] += 1
            if mask is None:
                return
            _buttons["head_ok"] += 1
            _buttons["mask"] = mask
            if mask:
                _buttons["any_press"] = True
            if (mask & BTN_L1) and (mask & BTN_L2):
                _buttons["seen_l1l2"] = True

    # topics_for(), not a literal: real is rt/lf/lowstate while sim is
    # rt/lowstate, and on this robot rt/lowstate is the topic with the second,
    # corrupt writer on it. Hardcoding the wrong one decodes garbage buttons
    # rather than failing, which is the worst outcome for a probe.
    topic = g1_protocol.topics_for(os.environ.get("SIM_MODE", "real")).lowstate
    sub = ChannelSubscriber(topic, LowState_)
    sub.Init(on_lowstate, 10)
    print(f"remote    : reading {topic} wireless_remote[40]")
    while True:
        time.sleep(1)


def local_iface_addr() -> str:
    import subprocess
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(IFACE_PREFIX):
                return tok.split("/")[0]
    raise SystemExit(f"FATAL: no interface on {IFACE_PREFIX}0/24")


def igmp_joined(group: str) -> bool:
    packed = socket.inet_aton(group)
    want = "".join(f"{b:02X}" for b in reversed(packed))
    try:
        with open("/proc/net/igmp") as fh:
            return want in fh.read().upper()
    except OSError:
        return False


def main() -> None:
    from bridge.sdk.connection import init_dds

    init_dds(robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
             domain_id=0, interface=os.environ.get("DDS_INTERFACE", "eth0"))
    threading.Thread(target=lowstate_thread, daemon=True).start()
    time.sleep(1.5)

    ifaddr = local_iface_addr()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", PORT))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                    socket.inet_aton(GROUP) + socket.inet_aton(ifaddr))
    sock.settimeout(0.2)

    joined = igmp_joined(GROUP)
    print(f"mic       : {GROUP}:{PORT} via {ifaddr}   join verified: {joined}")
    print(f">>> HOLD L1+L2 ON THE REMOTE during the next {SECONDS}s <<<\n")

    total_pkts = total_bytes = 0
    pkts_while_held = 0
    start = time.time()
    while time.time() - start < SECONDS:
        sec_pkts = sec_bytes = 0
        tick = time.time()
        while time.time() - tick < 1.0:
            try:
                data, _ = sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            sec_pkts += 1
            sec_bytes += len(data)
        with _lock:
            mask = _buttons["mask"]
            held = bool((mask & BTN_L1) and (mask & BTN_L2))
        if held:
            pkts_while_held += sec_pkts
        total_pkts += sec_pkts
        total_bytes += sec_bytes
        flag = "  <== L1+L2 HELD" if held else ""
        print(f"  t+{int(time.time()-start):3d}s  btn=0x{mask:04X} "
              f"[{button_names(mask)}]  {sec_pkts:5d} pkts {sec_bytes:8d} B{flag}")

    with _lock:
        frames, head_ok = _buttons["frames"], _buttons["head_ok"]
        saw_l1l2, any_press = _buttons["seen_l1l2"], _buttons["any_press"]

    print(f"\nlowstate frames: {frames}  (remote-shaped: {head_ok})")
    print(f"mic total      : {total_pkts} packets, {total_bytes} bytes"
          f"  ({total_bytes / BYTES_PER_SEC:.2f} s of audio)")
    print(f"L1+L2 observed : {saw_l1l2}   any button at all: {any_press}\n")

    if not head_ok:
        print("VERDICT: INCONCLUSIVE — no remote-shaped frames. The R3 may be off,")
        print("         or wireless_remote is not populated on this firmware.")
    elif not saw_l1l2:
        print("VERDICT: INCONCLUSIVE — the combo was never held during the window.")
        print("         A zero mic count here says nothing. Re-run and hold L1+L2.")
    elif pkts_while_held:
        print("VERDICT: GATED ON WAKE-UP MODE — the feed flows while L1+L2 is held.")
        print("         listen() therefore has a HUMAN PREREQUISITE: no unattended loop.")
    else:
        print("VERDICT: L1+L2 held and the feed stayed SILENT. Wake-up mode is not the")
        print("         gate — the mic is withheld by something else, and the listening")
        print("         half of D6.3 needs a different source (see ROBOT-HARDWARE.md §8.2).")


if __name__ == "__main__":
    main()
