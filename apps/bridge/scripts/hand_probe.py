"""Does a BrainCo hand answer on its serial line? Reads only; nothing moves.

The chain is ttyUSB -> `brainco_hand_server` (vendor) -> `rt/brainco/{side}/*`
-> the bridge. Since 2026-11-03 nothing answers at the end of it, and the
vendor server only says "Failed to get device info". This asks the hands
directly, without the server, so a silent hand is a silent hand.

    ssh c3po
    cd ~/c3po/apps/bridge
    sudo .venv/bin/python scripts/hand_probe.py                 # every /dev/ttyUSB*
    sudo .venv/bin/python scripts/hand_probe.py /dev/ttyUSB1    # one line

Protocol, from `docs/ROBOT-HARDWARE.md` §7.1 (the vendor server's own log of
the right hand binding on this robot): Modbus RTU, 460800 baud, 8N1, slave
126 = left, 127 = right. Each port gets one Modbus "read holding registers"
(function 0x03) per slave id. A Modbus read changes nothing on the device.

shortcut: the register map is in BrainCo's closed SDK, not in this repo, so
the default register (0) is a guess. That does not matter for the question:
a hand that is alive answers a read of an address it lacks with a Modbus
exception, which still has its slave id (= its side) and a valid CRC. Pass
`--register N` once the device-info address is known.

No `--move-test`: our hand driver speaks DDS to the vendor server, not this
serial protocol, so there is no motion frame in this repo to reuse. Move a
hand through the bridge (`set_hand`) once the server binds it.

Exit: 0 both hands answered, 1 one did, 2 none, 3 refused (port in use).
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import select
import sys
import termios
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

BAUD = 460800
SLAVES = {126: "left", 127: "right"}
READ_HOLDING = 0x03
# A reply is ~10 bytes (~0.2 ms at 460800); the vendor polls at 100 Hz. 0.2 s is generous.
REPLY_TIMEOUT_S = 0.2
# The vendor server re-probes every port at start: never race it for a line.
VENDOR_SERVER = "brainco_hand_server"


def crc16(data: bytes) -> int:
    """CRC-16/MODBUS (poly 0xA001 reflected, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _with_crc(body: bytes) -> bytes:
    return body + crc16(body).to_bytes(2, "little")


def read_request(slave: int, register: int, count: int = 1) -> bytes:
    """Read holding registers: slave, 0x03, address, count, CRC (low byte first)."""
    return _with_crc(
        bytes([slave, READ_HOLDING]) + register.to_bytes(2, "big") + count.to_bytes(2, "big")
    )


@dataclass(frozen=True)
class Reply:
    slave: int
    registers: tuple[int, ...] = ()
    exception: int | None = None

    @property
    def side(self) -> str:
        return SLAVES.get(self.slave, f"slave {self.slave}")

    def describe(self) -> str:
        if self.exception is not None:
            return f"Modbus exception {self.exception} (alive; register not readable)"
        return "registers " + ", ".join(f"0x{r:04x}" for r in self.registers)


def parse_reply(frame: bytes, slave: int) -> Reply | None:
    """A valid answer from `slave` to a 0x03 read, or None for silence/noise."""
    if len(frame) < 5 or frame[0] != slave:
        return None
    if frame[1] == READ_HOLDING | 0x80:
        body = frame[:3]
        if frame[3:5] != crc16(body).to_bytes(2, "little"):
            return None
        return Reply(slave=slave, exception=frame[2])
    if frame[1] != READ_HOLDING:
        return None
    n = frame[2]
    body = frame[: 3 + n]
    if n % 2 or len(frame) < 5 + n or frame[3 + n : 5 + n] != crc16(body).to_bytes(2, "little"):
        return None
    regs = tuple(int.from_bytes(body[3 + i : 5 + i], "big") for i in range(0, n, 2))
    return Reply(slave=slave, registers=regs)


class Line(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


class SerialLine:
    """Raw 8N1 tty at BAUD, stdlib only (pyserial is not in the bridge's venv)."""

    def __init__(self, path: str) -> None:
        speed = getattr(termios, "B460800", None)
        if speed is None:
            raise OSError("this OS has no 460800 baud constant; run it on the robot")
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)  # nobody else opens it while we probe
            tty.setraw(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~(termios.CSTOPB | termios.PARENB | getattr(termios, "CRTSCTS", 0))
            attrs[4] = attrs[5] = speed
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except BaseException:
            os.close(self.fd)
            raise

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)
        termios.tcdrain(self.fd)

    def read(self, timeout: float) -> bytes:
        """Everything until the line goes quiet (or the timeout, if nothing came)."""
        got = b""
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select([self.fd], [], [], left if not got else min(left, 0.01))
            if not ready:
                if got:
                    break
                continue
            got += os.read(self.fd, 256)
        return got

    def close(self) -> None:
        os.close(self.fd)


@dataclass
class PortResult:
    port: str
    opened: bool
    error: str = ""
    received: int = 0
    replies: tuple[Reply, ...] = ()


def probe_line(line: Line, register: int) -> tuple[int, tuple[Reply, ...]]:
    """Ask each slave id once. Returns (bytes received, valid replies)."""
    received = 0
    replies = []
    for slave in SLAVES:
        request = read_request(slave, register)
        line.write(request)
        frame = line.read(REPLY_TIMEOUT_S)
        received += len(frame)
        # An RS485 adapter that hears itself hands our own request back first.
        frame = frame.removeprefix(request)
        reply = parse_reply(frame, slave)
        if reply is not None:
            replies.append(reply)
    return received, tuple(replies)


def holders(port: str, proc: Path = Path("/proc")) -> list[str]:
    """'pid name' of every process with `port` open, plus any vendor hand server."""
    target = os.path.realpath(port)
    found = []
    for pid_dir in proc.glob("[0-9]*"):
        try:
            name = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
        except OSError:
            continue
        hit = VENDOR_SERVER in name
        if not hit:
            try:
                hit = any(os.path.realpath(fd) == target for fd in (pid_dir / "fd").iterdir())
            except OSError:  # not ours to read (run with sudo) or gone
                pass
        if hit:
            found.append(f"{pid_dir.name} {name[:80]}")
    return found


def run(
    ports: list[str], register: int, open_line: Callable[[str], Line] = SerialLine
) -> tuple[int, list[PortResult]]:
    results = []
    for port in ports:
        try:
            line = open_line(port)
        except OSError as exc:
            results.append(PortResult(port, opened=False, error=str(exc)))
            continue
        try:
            received, replies = probe_line(line, register)
        finally:
            line.close()
        results.append(PortResult(port, opened=True, received=received, replies=replies))
    sides = {r.side for res in results for r in res.replies}
    code = 0 if {"left", "right"} <= sides else 1 if sides else 2
    return code, results


def report(results: list[PortResult]) -> str:
    out = []
    for res in results:
        if not res.opened:
            out.append(f"{res.port}: opened no ({res.error})")
            continue
        head = f"{res.port}: opened yes, {res.received} bytes received"
        if not res.replies:
            out.append(f"{head}, silent" + (" (noise, no valid frame)" if res.received else ""))
        for r in res.replies:
            out.append(f"{head}, {r.side} hand (slave {r.slave}): {r.describe()}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ports", nargs="*", help="default: every /dev/ttyUSB*")
    parser.add_argument("--register", type=int, default=0, help="holding register (default 0)")
    args = parser.parse_args(argv)

    ports = args.ports or sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        print("No /dev/ttyUSB* at all: the FT4232H is not enumerated (lsusb | grep 0403:6011).")
        return 2
    busy = {p: h for p in ports if (h := holders(p))}
    if busy:
        for port, procs in busy.items():
            print(f"{port}: in use by " + "; ".join(procs))
        print(
            "Refusing: two programs on one RS485 line garble each other.\n"
            "Free it: `sudo pkill -f brainco_hand_server` for the vendor server; any other\n"
            "holder, stop that process (the bridge never opens a ttyUSB). Then re-run."
        )
        return 3
    print(f"Modbus RTU {BAUD} 8N1, register {args.register}, slaves 126 (left) and 127 (right).\n")
    code, results = run(ports, args.register)
    print(report(results))
    print(["\nBoth hands answered.", "\nOnly one hand answered.", "\nNo hand answered."][code])
    return code


if __name__ == "__main__":
    sys.exit(main())
