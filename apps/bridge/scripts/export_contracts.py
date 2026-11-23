"""Write the bridge's contract with apps/back and apps/web to `contracts/` at the repo root.

    cd apps/bridge && uv run python scripts/export_contracts.py          # regenerate
    cd apps/bridge && uv run python scripts/export_contracts.py --check  # exit 1 if stale

The bridge is the source of truth for what it offers. apps/back and apps/web
are TypeScript and cannot import Python, so they name its tools, its safety set
and its gestures as string literals, and restate its HTTP payloads as TS types.
This writes all of that out of the running code, once, to files both sides read:

  contracts/bridge-tools.json            every MCP tool from the server's real
                                         catalogue (tier, classification, danger,
                                         needs-confirmation, parameter schema), the
                                         safety set, the gesture names
  contracts/bridge-payloads.schema.json  JSON Schema for the HTTP bodies TS restates
                                         (safety status, robot state, stop error codes)
  contracts/bridge-payloads.ts           the same schema as TS types, for
                                         compile-time comparison with the hand-written ones

`tests/test_contracts.py` fails when the committed files differ from what this
writes, so a bridge change that alters the contract cannot land without the
files, and the TS suites (apps/back/src/contracts.test.ts,
apps/web/tests/contracts.test.ts) then check every consumer against them.

Imports the server in `SIM_MODE=stub`, the way tests/test_mcp_catalogue.py
does: stub skips the DDS init at import time, so this runs on any machine.
"""

from __future__ import annotations

import os

# Set, not defaulted: apps/bridge/.env says isaac or real on a dev machine, and
# the environment beats the file (bridge/env_file.py). Must precede the imports.
os.environ["SIM_MODE"] = "stub"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Literal  # noqa: E402

from pydantic import TypeAdapter  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
REGENERATE = "cd apps/bridge && uv run python scripts/export_contracts.py"

# Keys under which a JSON Schema holds a NAME -> schema mapping. Their keys are
# parameter names and are kept even when a parameter is called "title".
_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
# Prose. It reaches the LLM through listTools and changes with every docstring
# edit; it is not what a caller has to agree with, so it stays out of the file.
_PROSE = frozenset({"title", "description"})


def _without_prose(node: Any) -> Any:
    if isinstance(node, list):
        return [_without_prose(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _PROSE:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _without_prose(sub) for name, sub in value.items()}
        else:
            out[key] = _without_prose(value)
    return out


def tools_manifest() -> dict[str, Any]:
    from bridge import safety
    from bridge.mcp_server import mcp
    from bridge.skills import gesture

    listed = sorted(asyncio.run(mcp.list_tools()), key=lambda t: t.name)
    tools: dict[str, Any] = {}
    for tool in listed:
        c3po = (tool.meta or {})["c3po"]
        tools[tool.name] = {
            "tier": c3po["tier"],
            "classification": c3po["classification"],
            "danger_level": c3po["danger_level"],
            "needs_confirmation": safety.needs_confirmation(tool.meta),
            "parameters": _without_prose(tool.inputSchema),
        }
    return {
        "$comment": f"GENERATED from the bridge's MCP catalogue. Do not edit: {REGENERATE}",
        "tools": tools,
        # classification "safety" bypasses the admin gate in apps/back
        # (skills/invoke.ts SAFETY_SKILLS), so the set is part of the contract.
        "safety_tools": sorted(n for n, t in tools.items() if t["classification"] == "safety"),
        # What the `gesture` tool's `name` accepts (skills/gesture.py, from g1_protocol.Gesture).
        "gestures": gesture.available_gestures(),
    }


def payload_schema() -> dict[str, Any]:
    from bridge import safety
    from bridge.sdk import state

    exported: list[Any] = [
        state.RobotState,
        safety.SafetyStatusBody,
        safety.StopAckRefused,
        safety.StopAcknowledged,
    ]
    adapters: list[tuple[Any, Literal["serialization"], TypeAdapter[Any]]] = [
        (t, "serialization", TypeAdapter(t)) for t in exported
    ]
    _, schema = TypeAdapter.json_schemas(adapters, ref_template="#/$defs/{model}")
    defs = dict(schema["$defs"])
    # A Literal alias has no name of its own in pydantic's output; give it one.
    defs["SafetyRouteError"] = TypeAdapter(safety.SafetyRouteError).json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": (
            "GENERATED from bridge/safety.py and bridge/sdk/state.py. "
            f"Do not edit: {REGENERATE}"
        ),
        "$defs": _without_prose(dict(sorted(defs.items()))),
    }


# -- JSON Schema -> TypeScript, for exactly the shapes pydantic emits here -----


def _ts(node: dict[str, Any]) -> str:
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in node:
        return " | ".join(_ts(n) for n in node["anyOf"])
    if "enum" in node:
        return " | ".join(json.dumps(v) for v in node["enum"])
    if "const" in node:
        return json.dumps(node["const"])
    kind = node.get("type")
    if kind in ("number", "integer"):
        return "number"
    if kind == "string":
        return "string"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        item = _ts(node.get("items", {}))
        return f"({item})[]" if " | " in item else f"{item}[]"
    if kind == "object" and "properties" in node:
        return _ts_object(node)
    if kind == "object":
        return "Record<string, unknown>"
    if not node:
        return "unknown"
    raise ValueError(f"export_contracts: no TypeScript rendering for {node}")


def _ts_object(node: dict[str, Any]) -> str:
    required = set(node.get("required", []))
    lines = ["{"]
    for name, sub in node["properties"].items():
        optional = "" if name in required else "?"
        lines.append(f"  {name}{optional}: {_ts(sub)};")
    lines.append("}")
    return "\n".join(lines)


def payload_types(schema: dict[str, Any]) -> str:
    out = [
        f"// GENERATED from contracts/bridge-payloads.schema.json. Do not edit: {REGENERATE}",
        "//",
        "// The bridge's HTTP payloads as TypeScript, so the console's hand-written",
        "// types can be compared with them at compile time (apps/back and apps/web",
        "// contracts.test.ts). Types only: nothing imports a value from here.",
        "",
    ]
    for name, node in schema["$defs"].items():
        out.append(f"export type {name} = {_ts(node)};")
        out.append("")
    return "\n".join(out)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def render() -> dict[Path, str]:
    schema = payload_schema()
    return {
        CONTRACTS / "bridge-tools.json": _json(tools_manifest()),
        CONTRACTS / "bridge-payloads.schema.json": _json(schema),
        CONTRACTS / "bridge-payloads.ts": payload_types(schema),
    }


def stale(rendered: dict[Path, str]) -> list[Path]:
    return [
        path
        for path, text in rendered.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 if a file is stale")
    args = parser.parse_args(argv)
    rendered = render()
    behind = stale(rendered)
    if args.check:
        for path in behind:
            print(f"stale: {path.relative_to(REPO_ROOT)} (run: {REGENERATE})", file=sys.stderr)
        return 1 if behind else 0
    CONTRACTS.mkdir(exist_ok=True)
    for path, text in rendered.items():
        path.write_text(text, encoding="utf-8")
        mark = "updated" if path in behind else "unchanged"
        print(f"{mark}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
