"""Tests for RPC client timeouts (`bridge.sdk.g1_rpc`).

Regression coverage for a false negative found on hardware: arm gestures ack on
*completion of the motion*, not on receipt. A HIGH_WAVE answered code=0 after
4.19s, well past the SDK's default timeout — so every gesture reported
RPC_ERR_CLIENT_API_TIMEOUT (3104) while the robot was visibly performing it.

Reporting "failed" for a command the robot obeyed is the dangerous direction to
be wrong in: an operator or LLM would conclude it was ignored and retry.
"""

from __future__ import annotations

from bridge.sdk import g1_protocol, g1_rpc


def test_arm_timeout_exceeds_observed_gesture_duration():
    # Measured: 4.19s for HIGH_WAVE. Anything at or below that reintroduces the
    # false-failure bug on the very gesture we tested.
    assert g1_rpc.ARM_TIMEOUT_S > 4.19
    # And enough headroom for gestures slower than the one we happened to try.
    assert g1_rpc.ARM_TIMEOUT_S >= 10.0


def test_sport_timeout_covers_physical_posture_transitions():
    # sit_g1 / lie_up / squat are real motions on the sport service, so its
    # timeout has to be a motion duration too, not a network round-trip.
    assert g1_rpc.SPORT_TIMEOUT_S >= 5.0


def _uninitialised_client(api_ids: tuple[int, ...], timeout_s: float | None) -> tuple:
    """Build a _G1Client without running the SDK base __init__.

    Instantiating for real stands up DDS entities, and patching the module's
    `Client` symbol doesn't help — the base class is bound when `_G1Client` is
    created at import. So construct the object directly and shadow the two
    inherited methods `Init` calls with instance attributes.
    """
    client = object.__new__(g1_rpc._G1Client)
    client._api_ids = api_ids
    client._timeout_s = timeout_s
    calls: dict = {"api_ids": []}
    client.SetTimeout = lambda seconds: calls.__setitem__("timeout", seconds)
    client._RegistApi = lambda api_id, version: calls["api_ids"].append(api_id)
    return client, calls


def test_client_applies_its_timeout_on_init():
    client, calls = _uninitialised_client(
        (g1_protocol.API_ID_G1_UPPER_LIMBS,), g1_rpc.ARM_TIMEOUT_S
    )

    client.Init()

    assert calls["timeout"] == g1_rpc.ARM_TIMEOUT_S
    assert calls["api_ids"] == [g1_protocol.API_ID_G1_UPPER_LIMBS]


def test_client_without_timeout_leaves_sdk_default():
    client, calls = _uninitialised_client((7101,), None)

    client.Init()

    assert "timeout" not in calls


def test_all_registered_api_ids_are_applied():
    # A client serving several api_ids must register every one; a missed
    # registration surfaces only as a runtime call failure.
    ids = (7101, 7105, 7001, 7002)
    client, calls = _uninitialised_client(ids, g1_rpc.SPORT_TIMEOUT_S)

    client.Init()

    assert calls["api_ids"] == list(ids)
