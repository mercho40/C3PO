"""The DDS interface pin, and the config file that is not read.

`DDS_INTERFACE` was documented as the remedy for CycloneDDS "selected
arbitrarily from: eth0, docker0, wlan0" and did nothing at all: the name went
into an XML file that the SDK's own domain never reads, because
`ChannelFactory.Init` passes an inline config to `Domain(id, config)` and a
non-NULL config overrides CYCLONEDDS_URI. Nothing failed, nothing warned, and
the NIC stayed arbitrary — which onboard the Jetson means seeing none of the
robot, depending on boot order.

These tests hold the two halves of the fix: the name now reaches the SDK, and
every path that is not a confident pin degrades to the behaviour that shipped
before it worked.
"""

from __future__ import annotations

import pytest

from bridge.sdk import connection
from bridge.sdk.connection import (
    _DDS_XML_TEMPLATE,
    _interface_element,
    _write_peer_xml,
    resolve_interface,
    wants_unicast,
)


@pytest.fixture(autouse=True)
def _tmp_config_dir(tmp_path, monkeypatch):
    """Keep the written config out of the real /tmp.

    `_write_peer_xml` names the file after the pid, so a test run would
    otherwise overwrite the config of a bridge running on this machine.
    """
    monkeypatch.setattr(connection.tempfile, "gettempdir", lambda: str(tmp_path))


# --- resolve_interface ------------------------------------------------------


def test_no_interface_is_autodetermine():
    """The developer-machine default, unchanged."""
    assert resolve_interface(None, ["lo", "en0"]) == (None, "autodetermine")
    assert resolve_interface("", ["lo", "en0"]) == (None, "autodetermine")


def test_a_real_interface_is_pinned():
    assert resolve_interface("eth0", ["lo", "eth0", "docker0"]) == ("eth0", "pinned")


def test_an_interface_that_is_not_on_this_host_falls_back_rather_than_binding():
    """The fallback direction is the whole safety argument for landing this.

    Handing CycloneDDS a name it cannot bind stops the bridge from seeing the
    robot. Falling back to autodetermine is exactly what happened before the
    pin worked, so a stale DDS_INTERFACE can cost a log line and nothing more.
    """
    pinned, reason = resolve_interface("eth0", ["lo", "en0", "wlan0"])
    assert pinned is None
    assert reason == "missing"


def test_unenumerable_interfaces_trust_the_operator():
    """"Cannot tell" is not "absent".

    Collapsing the two would silently discard a deliberate setting on any host
    where `if_nameindex` does not work, which is the one case where the
    operator knows more than this process does.
    """
    assert resolve_interface("eth0", None) == ("eth0", "pinned-unverified")


# --- the XML that the SDK does not read -------------------------------------


def test_the_config_is_scoped_to_one_domain_not_to_any():
    """`id="any"` applied to every domain created without its own config.

    A bare `DomainParticipant(42)` anywhere in this process would inherit
    "multicast off, unicast to the control board" and discover nothing, with no
    error — the trap `perception_link.py` documents and defends itself against.
    """
    assert 'id="any"' not in _DDS_XML_TEMPLATE
    xml = _write_peer_xml("192.168.123.161", "eth0", 0).read_text()
    assert '<Domain id="0">' in xml
    assert 'id="any"' not in xml


def test_the_domain_id_follows_the_domain_being_initialized():
    xml = _write_peer_xml("192.168.123.161", None, 42).read_text()
    assert '<Domain id="42">' in xml


def test_the_written_config_reflects_what_was_actually_pinned():
    """Not what was requested.

    The file is the thing somebody opens when DDS misbehaves. If it names an
    interface that was rejected and never pinned, it is evidence pointing the
    wrong way.
    """
    xml = _write_peer_xml("192.168.123.161", None, 0).read_text()
    assert 'autodetermine="true"' in xml
    assert "NetworkInterface name=" not in xml


def test_the_peer_address_is_the_robot_host():
    xml = _write_peer_xml("10.10.32.19", None, 0).read_text()
    assert '<Peer address="10.10.32.19" />' in xml


# --- the unicast workaround is a simulator workaround -----------------------


def test_real_mode_does_not_ask_for_unicast_to_one_peer():
    """Onboard, this config would be actively harmful.

    `AllowMulticast=false` plus a single `<Peer>` at the control board hides
    every other 192.168.123.x publisher — the LiDAR, the colleague's stack, the
    video hub — with no error, just silence where topics used to be. It has
    never fired only because the file has never been read.
    """
    assert wants_unicast("real") is False
    assert wants_unicast("REAL") is False
    assert wants_unicast(" real ") is False


def test_the_simulator_modes_keep_it():
    """macOS multicast across a LAN is why this exists at all."""
    for mode in ("isaac", "mujoco_local", "", None):
        assert wants_unicast(mode) is True, mode


def test_real_mode_writes_neither_peers_nor_a_multicast_ban():
    xml = _write_peer_xml("192.168.123.161", "eth0", 0, unicast=False).read_text()
    assert "<Peer" not in xml
    assert "<AllowMulticast>true</AllowMulticast>" in xml
    # Still a valid single-domain document with the interface pin intact.
    assert '<Domain id="0">' in xml
    assert '<NetworkInterface name="eth0" />' in xml


# Moved here from test_locomotion_dispatch.py, which is about locomotion.


def test_interface_element_defaults_to_autodetermine():
    assert 'autodetermine="true"' in _interface_element(None)
    assert 'autodetermine="true"' in _interface_element("")


def test_interface_element_pins_named_nic():
    element = _interface_element("eth0")
    assert element == '<NetworkInterface name="eth0" />'
    assert "autodetermine" not in element


def test_peer_xml_embeds_pinned_interface_and_host():
    xml = _write_peer_xml("192.168.123.161", "eth0", 0).read_text()

    assert '<NetworkInterface name="eth0" />' in xml
    assert '<Peer address="192.168.123.161" />' in xml
    # Unicast peers only: multicast is what we can't rely on across the Mac's
    # Wi-Fi, and onboard the peer address is enough to find the control board.
    assert "<AllowMulticast>false</AllowMulticast>" in xml
