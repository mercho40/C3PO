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

shortcut: no auth on the proxy, which is safe only because both ends bind to
loopback and the robot end exists only while this ssh session does.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

LOCAL_PORT = 8899
ROBOT_PORT = 18899


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
            up_r, up_w = await asyncio.open_connection(host, int(port or 443))
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
            up_r, up_w = await asyncio.open_connection(host, int(port or 80))
            up_w.write(f"{method} /{path} {version}\r\n".encode("latin-1") + rest)
            await up_w.drain()
    except (OSError, ValueError) as exc:
        client_w.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}".encode())
        client_w.close()
        return
    await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))


async def _serve() -> None:
    server = await asyncio.start_server(_handle, "127.0.0.1", LOCAL_PORT)
    async with server:
        await server.serve_forever()


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "c3po"
    if host == "--serve-only":
        asyncio.run(_serve())
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
    print(f"robot: export http_proxy=http://127.0.0.1:{ROBOT_PORT} https_proxy=http://127.0.0.1:{ROBOT_PORT}")
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
