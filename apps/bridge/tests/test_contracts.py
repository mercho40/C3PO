"""The committed contract files under `contracts/` are what this bridge actually offers.

apps/back and apps/web are TypeScript, so they cannot import the catalogue or
the payload types: they name tools as string literals and restate payloads as
TS types. `scripts/export_contracts.py` writes the bridge's side of that
contract out of the running code, and the TS suites check every consumer
against the files it writes (apps/back/src/contracts.test.ts,
apps/web/tests/contracts.test.ts).

That chain only holds if the files are current, which is the first test here.
The rest hold the declared payload types (bridge/safety.py, bridge/sdk/state.py)
to the dicts the code really returns, since those types are what the schema is
generated from: a key the code adds and the type does not list would never reach
the console's comparison.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
import typing
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from bridge import safety
from bridge.sdk import state

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_contracts.py"


@pytest.fixture
def exporter(monkeypatch) -> types.ModuleType:
    # The script pins SIM_MODE=stub for itself on import; keep that out of later tests.
    monkeypatch.setenv("SIM_MODE", os.environ["SIM_MODE"])
    spec = importlib.util.spec_from_file_location("export_contracts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_contracts_are_current(exporter):
    behind = exporter.stale(exporter.render())
    assert not behind, (
        f"stale: {[str(p.relative_to(exporter.REPO_ROOT)) for p in behind]}. The bridge's "
        f"contract changed and contracts/ was not regenerated. Run `{exporter.REGENERATE}` "
        "and commit the result; apps/back and apps/web are tested against those files."
    )


def test_the_manifest_is_the_catalogue_this_suite_pins(exporter):
    """bridge-tools.json and EXPECTED_TOOLS name the same tools (both come from the server)."""
    from test_mcp_catalogue import EXPECTED_TOOLS

    manifest = json.loads((exporter.CONTRACTS / "bridge-tools.json").read_text())
    assert set(manifest["tools"]) == EXPECTED_TOOLS


# -- the payload types are the payloads ------------------------------------------


def _undeclared(tp: Any, value: Any, path: str = "") -> list[str]:
    """Keys in `value` that TypedDict `tp` (recursively) does not declare.

    pydantic validates the declared keys' types but ignores extra ones, and an
    extra one is exactly the drift that matters: a field the console never hears of.
    """
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        # Only the arms this value could be: `RobotPose | None` holding a dict is a RobotPose.
        found = [
            _undeclared(arm, value, path)
            for arm in typing.get_args(tp)
            if (isinstance(value, dict) and typing.is_typeddict(arm))
            or (isinstance(value, list) and typing.get_origin(arm) is list)
        ]
        return min(found, key=len) if found else []
    if origin is list and isinstance(value, list):
        (item,) = typing.get_args(tp)
        return [k for i, v in enumerate(value) for k in _undeclared(item, v, f"{path}[{i}].")]
    if not typing.is_typeddict(tp) or not isinstance(value, dict):
        return []
    hints = typing.get_type_hints(tp)
    out: list[str] = []
    for key, sub in value.items():
        if key not in hints:
            out.append(f"{path}{key}")
            continue
        hint = hints[key]
        if typing.get_origin(hint) is typing.NotRequired:
            (hint,) = typing.get_args(hint)
        out.extend(_undeclared(hint, sub, f"{path}{key}."))
    return out


def _conforms(tp: Any, value: Any) -> None:
    TypeAdapter(tp).validate_python(value, strict=True)
    extra = _undeclared(tp, value)
    assert not extra, f"{tp.__name__} does not declare {extra}; add them to the type"


def _sampled(*, data: bool) -> state.StateSampler:
    sampler = state.StateSampler()
    if not data:
        return sampler
    now = time.monotonic()
    sampler._lowstate = state._LowStateSnapshot(
        received_at=now, tick=1, mode_machine=5, motor_count=35, raw_message_count=1
    )
    sampler._pose = state._PoseSnapshot(received_at=now, x_meters_world=1.0, raw_message_count=1)
    sampler._fsm = state._FsmSnapshot(received_at=now, fsm_id=501, fsm_mode=0)
    sampler._bms = state._BmsSnapshot(
        received_at=now, soc_pct=80, soh_pct=99, current_ma=-1200, raw_message_count=1
    )
    return sampler


def test_stub_get_state_is_a_robot_state():
    from bridge.mcp_server import get_state

    _conforms(state.RobotState, get_state())


@pytest.mark.parametrize("data", [False, True], ids=["no_data_yet", "receiving"])
def test_sampled_state_is_a_robot_state(data):
    # What `get_state` returns on isaac/real: the sampler's dict plus env/stub.
    sampled = _sampled(data=data).get_state()
    _conforms(state.SampledState, sampled)
    _conforms(state.RobotState, {**sampled, "env": "real", "stub": False})


class FakeRequest:
    def __init__(self, body: Any, secret: str | None = None):
        self._body = body
        self.headers = {"x-c3po-confirm-secret": secret} if secret is not None else {}

    async def json(self) -> Any:
        return self._body


def _body(resp: Any) -> Any:
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_the_safety_status_route_answers_a_safety_status_body():
    from bridge import mcp_server

    _conforms(safety.SafetyStatus, safety.status())
    _conforms(safety.SafetyStatusBody, _body(await mcp_server.safety_status(FakeRequest(None))))


@pytest.mark.asyncio
async def test_every_safety_route_error_is_declared(monkeypatch, tmp_path):
    """Drive each /safety/* refusal and check its `error` is in SafetyRouteError.

    And the converse: every declared code is reachable, so the list the
    console is held to is neither short nor padded.
    """
    from bridge import estop, mcp_server

    seen: dict[str, Any] = {}

    def note(resp: Any) -> None:
        body = _body(resp)
        seen[body["error"]] = body

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "")
    note(await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret="")))
    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    note(await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret="nope")))
    note(await mcp_server.safety_confirm(FakeRequest({"tool": "walk_to"}, secret="s3cret")))
    bad_args = {"tool": "set_hand", "args": {"closure": "abc"}}
    note(await mcp_server.safety_confirm(FakeRequest(bad_args, secret="s3cret")))

    estop.signal_stop()
    shown = estop.last_stop_at()
    time.sleep(0.01)
    estop.signal_stop()
    stale = await mcp_server.safety_ack(FakeRequest({"stopped_at": shown}, secret="s3cret"))
    assert stale.status_code == 409
    note(stale)
    _conforms(safety.StopAckRefused, _body(stale))

    (tmp_path / "nope").write_text("not a directory")
    writable = estop.DEFAULT_RUN_DIR
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path / "nope" / "nested")
    failed = await mcp_server.safety_ack(FakeRequest({}, secret="s3cret"))
    assert failed.status_code == 500
    note(failed)
    _conforms(safety.StopAckRefused, _body(failed))

    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", writable)
    ok = await mcp_server.safety_ack(FakeRequest({}, secret="s3cret"))
    assert ok.status_code == 200
    _conforms(safety.StopAcknowledged, _body(ok))

    assert set(seen) == set(typing.get_args(safety.SafetyRouteError))
