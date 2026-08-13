"""Safety and capability metadata attached to every MCP tool.

This travels to clients on MCP's `_meta` field, which `FastMCP.tool(meta=...)`
populates and `list_tools()` returns. That matters more than it sounds: until
now this metadata lived only in `apps/back`'s TypeScript catalogue, so the one
client actually driving the robot — Claude Code, which `.mcp.json` spawns
against this server directly over stdio — never saw any of it. Every
`danger_level` and `works_real=False` we had written was invisible to the
driver that needed it.

Putting it here also puts it where the knowledge is. `preconditions` are FSM
rules, `cancellable` is a property of how the skill loop is written, and
`works_real` records whether anyone has ever run the thing on hardware. None of
that is knowable from a TypeScript service on another machine; all of it is
knowable here.

`works_real` is the load-bearing one. It is what should stop an agent
commanding untested motion at a humanoid with people near it, and several
skills are deliberately False: accepted by the firmware, never observed to
work. Do not flip one to True because it "should" work — that is the exact
reasoning this field exists to interrupt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Coarse grouping for the operator UI. Keep the set small.
Classification = Literal[
    "introspection",
    "locomotion",
    "posture",
    "gesture",
    "perception",
    "speech",
    "memory",
    "safety",
    "task",
]

# How much autonomy the caller should assume. `low` may be invoked freely;
# `medium` warrants a confirmation; `high` should not happen without a human
# deciding to.
DangerLevel = Literal["low", "medium", "high"]

# Where the implementation actually stands, as opposed to where we would like
# it to be. `stub` means the tool returns success without doing anything, which
# an agent cannot otherwise detect.
Status = Literal["real", "stub", "planned"]


@dataclass(frozen=True)
class SkillMeta:
    """What a caller needs to know before invoking a tool on a real robot."""

    classification: Classification
    danger_level: DangerLevel
    status: Status
    cancellable: bool
    expected_duration_s: float
    # True only where the behaviour has actually been observed on that target.
    # "The firmware accepted it" is not the same as "it works" — see
    # `balance_stand`, which returns rpc code 0 and does nothing visible.
    works_sim: bool
    works_real: bool
    preconditions: list[str] = field(default_factory=list)
    typical_failure_modes: list[str] = field(default_factory=list)

    def to_meta(self) -> dict[str, Any]:
        """Render for MCP's `_meta`, namespaced so we cannot collide with the
        protocol's own keys or another server's."""
        return {
            "c3po": {
                "classification": self.classification,
                "danger_level": self.danger_level,
                "status": self.status,
                "cancellable": self.cancellable,
                "expected_duration_s": self.expected_duration_s,
                "works": {"sim": self.works_sim, "real": self.works_real},
                "preconditions": list(self.preconditions),
                "typical_failure_modes": list(self.typical_failure_modes),
            }
        }


def meta(
    *,
    classification: Classification,
    danger_level: DangerLevel,
    status: Status = "real",
    cancellable: bool = False,
    expected_duration_s: float = 1.0,
    works_sim: bool = False,
    works_real: bool = False,
    preconditions: list[str] | None = None,
    typical_failure_modes: list[str] | None = None,
) -> dict[str, Any]:
    """Build the `meta=` argument for `@mcp.tool()`.

    Defaults are deliberately pessimistic: a tool that forgets to declare
    `works_real` is reported as not working on hardware rather than silently
    claiming it does.
    """
    return SkillMeta(
        classification=classification,
        danger_level=danger_level,
        status=status,
        cancellable=cancellable,
        expected_duration_s=expected_duration_s,
        works_sim=works_sim,
        works_real=works_real,
        preconditions=preconditions or [],
        typical_failure_modes=typical_failure_modes or [],
    ).to_meta()
