#!/usr/bin/env python3
"""Is the G1's raw mic multicast reachable without the remote's wake-up mode?

    ssh c3po 'python3 ~/c3po/apps/bridge/scripts/mic_probe.py 20'

Run it once idle, then again while somebody holds L1+L2 on the remote. That pair
of runs is the whole experiment; see docs/ROBOT-HARDWARE.md §8.2 for the result
so far (silent at rest) and what each outcome would mean.

Everything in the listening half of the voice stack is downstream of this
(docs/DECISIONS.md D6.2/D6.3). Zero-risk: it joins a multicast group and counts
bytes. It transmits nothing and claims no device.

THE TRAP THIS IS WRITTEN AROUND: binding INADDR_ANY returns zero packets with no
error, so "I saw nothing" proves nothing unless the join went to the right
interface. The feed is on the robot's internal wired LAN (192.168.123.0/24,
eth0), NOT wlan0. So this pins the interface explicitly and then VERIFIES the
join landed by reading /proc/net/igmp back, before believing any silence.
"""
import socket
import sys
import time

GROUP = "239.168.123.161"
PORT = 5555
IFACE_PREFIX = "192.168.123."
# 16 kHz mono s16le -> 32000 bytes per second of audio.
BYTES_PER_SEC = 16000 * 1 * 2
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 12


def local_iface_addr() -> str:
    import subprocess
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(IFACE_PREFIX):
                return tok.split("/")[0]
    raise SystemExit(f"FATAL: no interface on {IFACE_PREFIX}0/24 — wrong host?")


def igmp_joined(group: str) -> bool:
    """Read the join back out of the kernel. Hex, network byte order reversed."""
    packed = socket.inet_aton(group)
    want = "".join(f"{b:02X}" for b in reversed(packed))
    try:
        with open("/proc/net/igmp") as fh:
            return want in fh.read().upper()
    except OSError:
        return False


ifaddr = local_iface_addr()
print(f"interface : {ifaddr} (eth0, the robot's internal wired LAN)")
print(f"group     : {GROUP}:{PORT}")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", PORT))
mreq = socket.inet_aton(GROUP) + socket.inet_aton(ifaddr)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

joined = igmp_joined(GROUP)
print(f"join verified in /proc/net/igmp: {joined}")
if not joined:
    print("  !! the join did NOT register — a zero count below means NOTHING.")

sock.settimeout(1.0)
print(f"listening {SECONDS}s ...")

total_pkts = total_bytes = 0
first_size = None
start = time.time()
while time.time() - start < SECONDS:
    sec_pkts = sec_bytes = 0
    tick = time.time()
    while time.time() - tick < 1.0:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            break
        except OSError:
            break
        sec_pkts += 1
        sec_bytes += len(data)
        if first_size is None:
            first_size = len(data)
    total_pkts += sec_pkts
    total_bytes += sec_bytes
    print(f"  t+{int(time.time()-start):2d}s  {sec_pkts:5d} pkts  {sec_bytes:8d} B")

print()
print(f"TOTAL     : {total_pkts} packets, {total_bytes} bytes")
if total_pkts:
    print(f"packet size (first): {first_size} B")
    print(f"audio implied      : {total_bytes / BYTES_PER_SEC:.2f} s "
          f"over a {SECONDS}s window (1.00x = a real-time 16 kHz mono feed)")
    print("VERDICT: MIC IS REACHABLE — the listening half is unblocked.")
elif joined:
    print("VERDICT: join OK, ZERO packets. Either the feed is gated on the")
    print("         remote's wake-up mode, or it only streams on demand.")
    print("         Re-run while pressing L1+L2 on the remote to tell them apart.")
else:
    print("VERDICT: INCONCLUSIVE — the join never registered.")
