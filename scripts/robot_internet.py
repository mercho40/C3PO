#!/usr/bin/env python3
"""Lend the robot the Mac's internet, through ssh, for apt / docker builds / Gemini.

The G1's Jetson has no route out when the Mac is on its cable (the Wi-Fi radio
runs the robot's own hotspot). This runs a tiny HTTP proxy on the Mac's
LOOPBACK and an `ssh -R` that makes it appear on the robot's loopback:

    robot 127.0.0.1:18899  --ssh-->  Mac 127.0.0.1:8899  -->  internet

so `http_proxy=http://127.0.0.1:18899` on the robot (or
`docker build --network host --build-arg http_proxy=...`) reaches the internet
while nothing new listens on any network interface.

Standard library only. Handles plain HTTP forwarding and HTTPS CONNECT, which
is all apt, pip and Google's client libraries use.

    python3 scripts/robot_internet.py unitree@192.168.123.164

It only reaches PUBLIC addresses on ports 80 and 443 (every resolved address
is checked, and the connection goes to the checked one), so a process on the
robot cannot use it to reach the Mac's own loopback services or the LAN.

    python3 scripts/robot_internet.py --self-test

shortcut: no auth on the proxy, which is safe only because both ends bind to
loopback, the robot end exists only while this ssh session does, and it reaches
nothing but public web ports; add auth before it ever listens beyond loopback.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import subprocess
import sys

LOCAL_PORT = 8899
ROBOT_PORT = 18899
ALLOWED_PORTS = (80, 443)


class _Refused(Exception):
    """The destination is not a public address on a web port."""


def _refusal(ip: str, port: int) -> str | None:
    """Why the proxy will not connect to ip:port, or None when it may."""
    if port not in ALLOWED_PORTS:
        return f"port {port} is not 80 or 443"
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if not addr.is_global or addr.is_multicast or addr.is_reserved:
        return f"{addr} is not a public address"
    return None


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open host:port only if EVERY address it resolves to passes _refusal."""
    infos = await asyncio.get_running_loop().getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    for *_, sockaddr in infos:
        if reason := _refusal(sockaddr[0], port):
            raise _Refused(f"{host}:{port}: {reason}")
    # Connect to the checked IPs, never the name: a second lookup could rebind to a private address.
    error: OSError = OSError(f"{host} did not resolve")
    for *_, sockaddr in infos:
        try:
            return await asyncio.open_connection(sockaddr[0], port)
        except OSError as exc:
            error = exc
    raise error


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def _handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        head = await client_r.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_w.close()
        return
    request_line, _, rest = head.partition(b"\r\n")
    try:
        method, target, version = request_line.decode("latin-1").split(" ", 2)
    except ValueError:
        client_w.close()
        return
    try:
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            up_r, up_w = await _connect(host, int(port or 443))
            client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_w.drain()
        else:
            # Absolute-form URL -> origin-form, and talk to that host.
            if not target.startswith("http://"):
                client_w.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                client_w.close()
                return
            hostport, _, path = target[len("http://") :].partition("/")
            host, _, port = hostport.partition(":")
            up_r, up_w = await _connect(host, int(port or 80))
            up_w.write(f"{method} /{path} {version}\r\n".encode("latin-1") + rest)
            await up_w.drain()
    except _Refused as exc:
        client_w.write(f"HTTP/1.1 403 Forbidden\r\n\r\n{exc}".encode())
        client_w.close()
        return
    except (OSError, ValueError) as exc:
        client_w.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}".encode())
        client_w.close()
        return
    await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))


async def _serve() -> None:
    server = await asyncio.start_server(_handle, "127.0.0.1", LOCAL_PORT)
    async with server:
        await server.serve_forever()


async def _self_test_connect() -> bytes:
    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        w.write(b"CONNECT 127.0.0.1:8001 HTTP/1.1\r\n\r\n")
        reply = await asyncio.wait_for(r.read(), 5)
        w.close()
    return reply


def _self_test() -> None:
    assert _refusal("8.8.8.8", 443) is None
    assert _refusal("8.8.8.8", 80) is None
    assert _refusal("2001:4860:4860::8888", 443) is None
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.123.164", "169.254.1.1", "100.64.0.1", "::1",
               "fe80::1", "fc00::1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1", "::7f00:1"):  # fmt: skip
        assert _refusal(ip, 443), ip
    assert _refusal("8.8.8.8", 22)
    reply = asyncio.run(_self_test_connect())
    assert reply.startswith(b"HTTP/1.1 403 "), reply
    print("ok")


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "c3po"
    if host == "--serve-only":
        asyncio.run(_serve())
        return 0
    if host == "--self-test":
        _self_test()
        return 0
    tunnel = subprocess.Popen(
        [
            "ssh", "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=5",
            "-R", f"127.0.0.1:{ROBOT_PORT}:127.0.0.1:{LOCAL_PORT}",
            host,
        ]
    )  # fmt: skip
    # no_proxy: the robot's own loopback services (bridge :8001) must not detour through the Mac, which refuses them.
    print(
        f"robot: export http_proxy=http://127.0.0.1:{ROBOT_PORT} https_proxy=http://127.0.0.1:{ROBOT_PORT}"
        " no_proxy=localhost,127.0.0.1,::1"
    )
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
