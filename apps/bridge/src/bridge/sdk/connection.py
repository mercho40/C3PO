"""DDS connection setup.

Sets CYCLONEDDS_URI, then calls ChannelFactoryInitialize from unitree_sdk2py to
wire the SDK.

WHAT CYCLONEDDS_URI DOES AND DOES NOT REACH
-------------------------------------------
`ChannelFactory.Init` calls `Domain(id, config)` with its OWN inline XML —
`ChannelConfigAutoDetermine`, or `ChannelConfigHasInterface` when it is given an
interface name (unitree_sdk2py/core/channel.py:212-215). `dds_create_domain`
with a non-NULL config overrides CYCLONEDDS_URI, so **the domain the SDK creates
never reads the file this module writes**. Verified empirically 2026-08-19 on
cyclonedds 0.10.2, and confirmed against the SDK source 2026-08-29.

Two consequences, and they pull in opposite directions:

  * The unicast `<Peers>` block cannot be delivered this way at all. The SDK's
    inline config has no Peers element and no seam to add one. The macOS
    "unicast across LAN" workaround in the header of this file has therefore
    never been in effect for the SDK's domain, and cannot be without patching
    the vendor SDK. It is left here because it still describes the intent, and
    because it is what a bare participant on this domain would want.
  * The interface pin CAN be delivered — by passing the name to
    `ChannelFactoryInitialize`, which is what `init_dds` now does. Setting
    DDS_INTERFACE used to be silently inert, which was the worst of the three
    possible behaviours: the operator followed the documented remedy for
    "CycloneDDS selected arbitrarily from: eth0, docker0, wlan0" and got
    arbitrary selection anyway.

The `<Domain>` id is scoped to the domain being initialized, NOT `any`.
`id="any"` applied to EVERY domain created without its own config, so a bare
`DomainParticipant(42)` anywhere in this process would silently inherit
"multicast off, unicast to the control board" and discover nothing.
`perception_link.py` documents that trap at length and defends itself by
passing its own config; scoping it here means the next module does not have to.
"""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DDS_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
  <Domain id="{domain_id}">
    <General>
      <AllowMulticast>{allow_multicast}</AllowMulticast>
      <Interfaces>
        {interface_element}
      </Interfaces>
    </General>{discovery}
  </Domain>
</CycloneDDS>
"""

_UNICAST_DISCOVERY = """
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <Peers>
        <Peer address="{robot_host}" />
      </Peers>
    </Discovery>"""


def _interface_element(interface: str | None) -> str:
    """Render the <NetworkInterface> entry: a named interface, or autodetermine."""
    if interface:
        return f'<NetworkInterface name="{interface}" />'
    return '<NetworkInterface autodetermine="true" />'


def wants_unicast(sim_mode: str | None) -> bool:
    """Unicast-to-one-peer is a SIMULATOR workaround. Onboard it is a hazard.

    It exists because macOS multicast across a LAN is unreliable, which is a
    developer-laptop-to-simulator problem. On the robot every participant is on
    one segment, multicast works, and `AllowMulticast=false` plus a single
    `<Peer>` pointing at the control board would hide EVERY other
    192.168.123.x publisher — the LiDAR, the colleague's stack, the video hub —
    with no error at all, just silence where topics used to be.

    That has never fired, because the config has never been read (see the
    module docstring). Which makes now, while it is still inert, the only
    cheap moment to make it conditional.
    """
    return (sim_mode or "").strip().lower() != "real"


def _write_peer_xml(
    robot_host: str, interface: str | None, domain_id: int, unicast: bool = True
) -> Path:
    """Write the cyclonedds config for this domain and return its path."""
    xml = _DDS_XML_TEMPLATE.format(
        allow_multicast="false" if unicast else "true",
        discovery=_UNICAST_DISCOVERY.format(robot_host=robot_host) if unicast else "",
        interface_element=_interface_element(interface),
        domain_id=domain_id,
    )
    # Write to a stable per-process file so it can be inspected for debugging.
    tmp = Path(tempfile.gettempdir()) / f"c3po-cyclonedds-{os.getpid()}.xml"
    tmp.write_text(xml)
    return tmp


def available_interfaces() -> list[str] | None:
    """NIC names on this host, or None when they cannot be enumerated."""
    try:
        return [name for _, name in socket.if_nameindex()]
    except (OSError, AttributeError):
        # Not fatal and not the caller's problem to solve — "unknown" is a
        # third answer, distinct from "absent", and it must not be collapsed
        # into one (see `resolve_interface`).
        return None


def resolve_interface(
    interface: str | None, available: list[str] | None
) -> tuple[str | None, str]:
    """Decide what to hand the SDK, and say why. Pure, so it can be tested.

    Returns (name_to_pin_or_None, reason). The fallback direction is deliberate:
    every path that is not a confident pin returns None, which is
    autodetermine — exactly the behaviour that shipped before the pin worked at
    all. A DDS_INTERFACE naming a NIC that is not on this host therefore costs
    nothing beyond a loud log line, rather than stranding the bridge with a
    name CycloneDDS cannot bind.
    """
    if not interface:
        return None, "autodetermine"
    if available is None:
        # Cannot enumerate: trust the operator, who set this deliberately.
        return interface, "pinned-unverified"
    if interface in available:
        return interface, "pinned"
    return None, "missing"


def init_dds(
    *,
    robot_host: str,
    domain_id: int = 0,
    interface: str | None = None,
    sim_mode: str | None = None,
) -> None:
    """Set CYCLONEDDS_URI and initialize the Unitree DDS channel factory.

    Must be called once at startup, before any subscriber is created.
    Idempotent: safe to call multiple times (subsequent calls are no-ops if
    the SDK was already initialized).

    `interface` pins CycloneDDS to one NIC by name. Leave it unset on a
    developer machine, where autodetermine is right. **Set it to `eth0` when
    running onboard the G1's Jetson**: that host has `eth0`, `wlan0` and
    `docker0`, and CycloneDDS otherwise picks among them arbitrarily — it says
    so itself, `selected arbitrarily from: eth0, docker0, wlan0`. Only `eth0`
    reaches the control board, so landing on either of the others means seeing
    none of the robot, intermittently and depending on boot order.

    The name is passed to `ChannelFactoryInitialize`, which is the only channel
    that reaches the SDK's domain; the XML this writes does not (see the module
    docstring). An unrecognised name falls back to autodetermine rather than
    being handed to CycloneDDS.
    """
    if sim_mode is None:
        sim_mode = os.environ.get("SIM_MODE")
    unicast = wants_unicast(sim_mode)

    pinned, reason = resolve_interface(interface, available_interfaces())
    xml_path = _write_peer_xml(robot_host, pinned, domain_id, unicast=unicast)
    os.environ["CYCLONEDDS_URI"] = f"file://{xml_path}"

    if reason == "missing":
        log.error(
            "dds.interface_not_found",
            requested=interface,
            available=available_interfaces(),
            effect="falling back to autodetermine — DDS may bind the wrong NIC",
        )

    log.info(
        "dds.init",
        robot_host=robot_host,
        domain_id=domain_id,
        interface=pinned or "autodetermine",
        interface_reason=reason,
        sim_mode=sim_mode or "unset",
        unicast_peer=unicast,
        cyclonedds_uri=os.environ["CYCLONEDDS_URI"],
        cyclonedds_home=os.environ.get("CYCLONEDDS_HOME"),
        # Stated because the URI above reads like a configuration that is in
        # force, and for the SDK's own domain it is not.
        peer_xml_read_by_sdk=False,
    )

    # Import lazily so CYCLONEDDS_URI is set before cyclonedds loads.
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    ChannelFactoryInitialize(domain_id, pinned)
    log.info("dds.ready", interface=pinned or "autodetermine")
