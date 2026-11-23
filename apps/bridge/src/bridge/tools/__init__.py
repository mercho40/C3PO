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
- `sequence`     run_sequence
- `speech`       say, ask_user (and the motion warning's speaker)
- `navigation`   arm/disarm_navigation, navigate_to(_landmark), navigation_status,
                 cancel_navigation, go_to_object
- `memory`       recall/list/forget_object, look_around, describe_surroundings
- `vision`       look, find_object, verify
"""

from bridge.tools import (  # noqa: F401 - they register on import
    arm,
    landmarks,
    listen,
    locomotion,
    manipulation,
    memory,
    navigation,
    posture,
    sequence,
    speech,
    state,
    tasks,
    vision,
)
