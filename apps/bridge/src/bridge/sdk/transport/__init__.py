"""Transport layer for the bridge.

Pluggable connection abstraction so skills don't care whether they're talking
to Isaac Sim over CycloneDDS or a real Unitree robot over WebRTC. See
docs/SPEC.md §16 for the full design.

Today: only the WebRTC Go2 transport is implemented as a self-contained
client. The DDS transport refactor (extracting existing state.py + send_velocity
into transport/dds.py) is a follow-up — current sim code calls the SDK directly
and works fine.
"""
