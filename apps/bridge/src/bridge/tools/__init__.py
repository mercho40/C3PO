"""The MCP tools: the skill catalogue, one module per domain.

Each module registers its tools on `bridge.server.mcp` when imported; importing
this package registers all of them. `bridge.mcp_server` does, and is the only
thing that should: it is the catalogue a client sees.

- `state`        get_state
- `locomotion`   walk_to, turn, walk_velocity
- `posture`      the firmware's posture and gesture skills (damp, prepare, wave, …)
- `arm`          gesture, move_arm, release_arm_control, set_hand, open_hands
- `listen`       listen
- `landmarks`    remember / recall / list / forget_landmark
- `tasks`        cancel_task, list_active_tasks
- `manipulation` pick, place, handover, reach, arm_state
"""

from bridge.tools import (  # noqa: F401 - they register on import
    arm,
    landmarks,
    listen,
    locomotion,
    manipulation,
    posture,
    state,
    tasks,
)
