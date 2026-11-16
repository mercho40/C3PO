"""The serial hand probe, against a fake RS485 line.

The frames are Modbus RTU as the vendor server speaks it to these hands
(ROBOT-HARDWARE §7.1: 460800 baud, slave 126 left, 127 right). The CRC is
pinned to the textbook vector so a wrong CRC cannot pass by agreeing with itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hand_probe  # noqa: E402
from hand_probe import (  # noqa: E402
    Reply,
    _with_crc,
    holders,
    parse_reply,
    probe_line,
    read_request,
    report,
    run,
)


class FakeLine:
    """Answers each request with a scripted frame; silent otherwise."""

    def __init__(self, answers: dict[int, bytes], echo: bool = False) -> None:
        self.answers = answers
        self.echo = echo
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def read(self, timeout: float) -> bytes:
        req = self.written[-1]
        return (req if self.echo else b"") + self.answers.get(req[0], b"")

    def close(self) -> None:
        self.closed = True


def ok(slave: int, *regs: int) -> bytes:
    data = b"".join(r.to_bytes(2, "big") for r in regs)
    return _with_crc(bytes([slave, 0x03, len(data)]) + data)


def test_the_request_is_the_textbook_modbus_frame() -> None:
    assert read_request(1, 0, 1) == bytes.fromhex("010300000001840a")
    assert read_request(127, 0, 1)[:6] == bytes([127, 3, 0, 0, 0, 1])


def test_a_register_reply_decodes_with_its_side() -> None:
    reply = parse_reply(ok(127, 0x0106), 127)
    assert reply == Reply(slave=127, registers=(0x0106,))
    assert reply.side == "right"


def test_an_exception_still_proves_the_hand_is_alive() -> None:
    reply = parse_reply(_with_crc(bytes([126, 0x83, 0x02])), 126)
    assert reply is not None and reply.exception == 2 and reply.side == "left"


@pytest.mark.parametrize(
    "frame",
    [
        ok(127, 1)[:-1] + b"\x00",  # bad CRC
        bytes([127, 0x83, 0x02, 0, 0]),  # exception with a bad CRC
        ok(126, 1),  # another slave
        b"\x7f\x03",  # short
        b"",
    ],
)
def test_noise_and_silence_are_not_answers(frame: bytes) -> None:
    assert parse_reply(frame, 127) is None


def test_a_line_that_echoes_our_request_still_reads_the_reply() -> None:
    received, replies = probe_line(FakeLine({127: ok(127, 7)}, echo=True), 0)
    assert [r.side for r in replies] == ["right"]
    assert received == 8 + 8 + len(ok(127, 7))  # both echoes count as received bytes


def test_probe_only_reads() -> None:
    line = FakeLine({})
    probe_line(line, 5)
    assert [f[1] for f in line.written] == [0x03, 0x03]
    assert [f[0] for f in line.written] == [126, 127]


def _lines(per_port: dict[str, FakeLine]):
    def open_line(port: str) -> FakeLine:
        if port not in per_port:
            raise OSError("Permission denied")
        return per_port[port]

    return open_line


def test_exit_codes_count_hands_not_ports() -> None:
    both = {"a": FakeLine({126: ok(126, 1)}), "b": FakeLine({127: ok(127, 1)})}
    assert run(["a", "b"], 0, _lines(both))[0] == 0
    one = {"a": FakeLine({}), "b": FakeLine({127: ok(127, 1)})}
    assert run(["a", "b"], 0, _lines(one))[0] == 1
    none = {"a": FakeLine({})}
    code, results = run(["a", "x"], 0, _lines(none))
    assert code == 2
    assert none["a"].closed
    text = report(results)
    assert "a: opened yes, 0 bytes received, silent" in text
    assert "x: opened no (Permission denied)" in text


def _proc(tmp_path: Path, pid: int, cmdline: str, fds: list[str]) -> None:
    d = tmp_path / str(pid)
    (d / "fd").mkdir(parents=True)
    (d / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())
    for i, target in enumerate(fds):
        os.symlink(target, d / "fd" / str(i))


def test_holders_finds_who_has_the_port_and_the_vendor_server(tmp_path: Path) -> None:
    port = tmp_path / "ttyUSB1"
    port.touch()
    proc = tmp_path / "proc"
    _proc(proc, 10, "python other.py", [str(port)])
    _proc(proc, 11, "./brainco_hand_server --network_interface eth0", [])
    _proc(proc, 12, "bash", [str(tmp_path / "elsewhere")])
    found = sorted(holders(str(port), proc))
    assert [f.split()[0] for f in found] == ["10", "11"]


def test_main_refuses_a_busy_port(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(hand_probe, "holders", lambda port: ["42 brainco_hand_server"])
    monkeypatch.setattr(hand_probe, "run", lambda *a: pytest.fail("probed a busy port"))
    assert hand_probe.main(["/dev/ttyUSB0"]) == 3
    assert "pkill -f brainco_hand_server" in capsys.readouterr().out
