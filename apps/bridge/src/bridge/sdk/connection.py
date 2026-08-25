"""DDS connection setup.

macOS multicast across LAN is unreliable, so we always use a unicast peer config
that explicitly points at ROBOT_HOST. The same code path works for Isaac Sim on
Ubuntu and the real G1 on its Jetson — only ROBOT_HOST changes.

Sets CYCLONEDDS_URI before any DDS code touches the environment, then calls
ChannelFactoryInitialize from unitree_sdk2py to wire the SDK.

TODO: the XML below is currently a NO-OP. ChannelFactoryInitialize creates the
domain with the SDK's own inline config, and a domain created with an inline
config ignores CYCLONEDDS_URI (verified empirically 2026-08-19 on cyclonedds
0.10.2). So neither the unicast peer nor the interface pin takes effect; the
bridge runs autodetermine + default multicast. Fix (supervised window only):
pass the interface through to ChannelFactoryInitialize, scope <Domain id="any">
to id="0", and make the unicast workaround SIM_MODE-conditional so it cannot
hide same-segment publishers onboard. See docs/ROBOT-API.md (known divergences)
and apps/perception/README.md (decisions list).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DDS_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
  <Domain id="any">
    <General>
      <AllowMulticast>false</AllowMulticast>
      <Interfaces>
        {interface_element}
      </Interfaces>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <Peers>
        <Peer address="{robot_host}" />
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
"""


def _interface_element(interface: str | None) -> str:
    """Render the <NetworkInterface> entry: a named interface, or autodetermine."""
    if interface:
        return f'<NetworkInterface name="{interface}" />'
    return '<NetworkInterface autodetermine="true" />'


def _write_peer_xml(robot_host: str, interface: str | None) -> Path:
    """Write a unicast-peer cyclonedds config and return its path."""
    xml = _DDS_XML_TEMPLATE.format(
        robot_host=robot_host, interface_element=_interface_element(interface)
    )
    # Write to a stable per-process file so it can be inspected for debugging.
    tmp = Path(tempfile.gettempdir()) / f"c3po-cyclonedds-{os.getpid()}.xml"
    tmp.write_text(xml)
    return tmp


def init_dds(*, robot_host: str, domain_id: int = 0, interface: str | None = None) -> None:
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
    """
    xml_path = _write_peer_xml(robot_host, interface)
    os.environ["CYCLONEDDS_URI"] = f"file://{xml_path}"
    log.info(
        "dds.init",
        robot_host=robot_host,
        domain_id=domain_id,
        interface=interface or "autodetermine",
        cyclonedds_uri=os.environ["CYCLONEDDS_URI"],
        cyclonedds_home=os.environ.get("CYCLONEDDS_HOME"),
    )

    # Import lazily so CYCLONEDDS_URI is set before cyclonedds loads.
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    ChannelFactoryInitialize(domain_id)
    log.info("dds.ready")
