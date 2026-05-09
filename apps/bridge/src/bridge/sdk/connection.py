"""DDS connection setup.

macOS multicast across LAN is unreliable, so we always use a unicast peer config
that explicitly points at ROBOT_HOST. The same code path works for Isaac Sim on
Ubuntu and the real G1 on its Jetson — only ROBOT_HOST changes.

Sets CYCLONEDDS_URI before any DDS code touches the environment, then calls
ChannelFactoryInitialize from unitree_sdk2py to wire the SDK.
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
        <NetworkInterface autodetermine="true" />
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


def _write_peer_xml(robot_host: str) -> Path:
    """Write a unicast-peer cyclonedds config and return its path."""
    xml = _DDS_XML_TEMPLATE.format(robot_host=robot_host)
    # Write to a stable per-process file so it can be inspected for debugging.
    tmp = Path(tempfile.gettempdir()) / f"c3po-cyclonedds-{os.getpid()}.xml"
    tmp.write_text(xml)
    return tmp


def init_dds(*, robot_host: str, domain_id: int = 0) -> None:
    """Set CYCLONEDDS_URI and initialize the Unitree DDS channel factory.

    Must be called once at startup, before any subscriber is created.
    Idempotent: safe to call multiple times (subsequent calls are no-ops if
    the SDK was already initialized).
    """
    xml_path = _write_peer_xml(robot_host)
    os.environ["CYCLONEDDS_URI"] = f"file://{xml_path}"
    log.info(
        "dds.init",
        robot_host=robot_host,
        domain_id=domain_id,
        cyclonedds_uri=os.environ["CYCLONEDDS_URI"],
        cyclonedds_home=os.environ.get("CYCLONEDDS_HOME"),
    )

    # Import lazily so CYCLONEDDS_URI is set before cyclonedds loads.
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    ChannelFactoryInitialize(domain_id)
    log.info("dds.ready")
