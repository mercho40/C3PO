"""Continuous teleoperation: a Quest headset drives the G1 in real time.

Distinct from `bridge.skills`, and deliberately so. A skill is a discrete,
cancellable, MCP-addressable *task* — "walk to (3, 2)", "wave" — dispatched at
human speed by an agent. Teleoperation is a 30-60 Hz control stream from a
worn headset, where the operator is the controller and every frame is a fresh
setpoint that expires. Routing that through MCP would put a JSON-RPC
round-trip, a task registry entry and a progress notification in the path of
every frame; the two shapes do not belong in the same transport.

So this package runs its own WebSocket ingest (`server.py`, port 8767) beside
the MCP server, and reuses the skill layer only where the semantics genuinely
match: locomotion still goes through `skills._locomotion.send_velocity_async`,
so the hardware velocity clamp and the firmware deadman apply to teleop
exactly as they apply to `walk_to`.

Layering, outermost first:

    server.py     WebSocket session, dead-man, dispatch    [I/O]
    protocol.py   wire frame -> validated dataclass        [pure]
    retarget.py   operator wrist pose -> G1 joint angles   [pure]
    arm_sdk.py    joint angles -> rt/arm_sdk LowCmd_       [DDS]
    hands.py      grip scalar -> hand command topic        [DDS]

The two pure modules carry all the geometry and all the unit tests. The two
DDS modules carry all the ways this can hurt someone, and both default to
**disabled** — see their module docstrings for what has to be verified, by a
human standing next to the robot, before either is switched on.
"""
