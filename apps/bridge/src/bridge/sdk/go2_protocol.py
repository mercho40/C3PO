"""Go2 protocol catalogue — sport command IDs, topic profile, request shapes.

Reverse-engineered from legion1581/unitree_ui (MIT) and the Unitree Go2 SDK.
Sibling to `g1_protocol.py` — same shape, different robot family.

Go2 vs G1 differences:
- IDL family: `unitree_go.msg.dds_` (vs G1's `unitree_hg`).
- Locomotion: a single `Move(api_id=1008)` sport command with parameter
  `{x, y, z}` (forward, lateral, yaw in m/s and rad/s) — no separate
  velocity stream like Isaac Sim's `rt/run_command/cmd`.
- BMS battery is inside `LowState_` (Go2 only) — no separate `bmsstate` topic.
- LiDAR is exposed via `rt/utlidar/*` (Go2 surfaces it; G1's Mid360 isn't
  enabled in firmware Explorer).
- Sport command IDs 1001-1036 for "Normal/AI/MCF" commands; 2041-2059 for
  MCF-only features (FrontFlip etc). G1 uses api_id=7101 instead.

The wire layer (`rt/api/sport/request` JSON envelope with header + parameter)
is identical across families — only the IDs and parameter shapes differ.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final, NamedTuple


# ---------------------------------------------------------------------------
# Topic profile
# ---------------------------------------------------------------------------


class Go2Topics(NamedTuple):
    """Topic names for a real Go2 over WebRTC (firmware ≤ ~1.1.x; matches the
    Explorer subscription set from legion1581/unitree_ui/src/protocol/topics.ts).
    """

    # State (robot → client)
    lowstate: str
    sportmodestate: str
    multiple_state: str
    selftest: str
    service_state: str
    battery_alarm: str
    robot_odom: str
    lidar_array: str
    lidar_state: str

    # Commands (client → robot)
    sport_request: str
    obstacles_avoid_request: str
    vui_request: str
    bashrunner_request: str
    motion_switcher_request: str
    robot_state_request: str
    audiohub_request: str
    videohub_request: str
    lidar_switch: str
    lowcmd: str
    wireless_controller: str


GO2_TOPICS: Final[Go2Topics] = Go2Topics(
    # State
    lowstate="rt/lf/lowstate",
    sportmodestate="rt/lf/sportmodestate",
    multiple_state="rt/multiplestate",
    selftest="rt/selftest",
    service_state="rt/servicestate",
    battery_alarm="rt/lf/battery_alarm",
    robot_odom="rt/utlidar/robot_pose",
    lidar_array="rt/utlidar/voxel_map_compressed",
    lidar_state="rt/utlidar/lidar_state",
    # Commands
    sport_request="rt/api/sport/request",
    obstacles_avoid_request="rt/api/obstacles_avoid/request",
    vui_request="rt/api/vui/request",
    bashrunner_request="rt/api/bashrunner/request",
    motion_switcher_request="rt/api/motion_switcher/request",
    robot_state_request="rt/api/robot_state/request",
    audiohub_request="rt/api/audiohub/request",
    videohub_request="rt/api/videohub/request",
    lidar_switch="rt/utlidar/switch",
    lowcmd="rt/lowcmd",
    wireless_controller="rt/wirelesscontroller",
)


# ---------------------------------------------------------------------------
# Sport command IDs (api_id on rt/api/sport/request)
# ---------------------------------------------------------------------------


class SportCmd(IntEnum):
    """Go2 sport-command api_ids. Use `Move` for locomotion; the rest are
    one-shot postures / tricks / mode switches.
    """

    # Shared across Normal/AI/MCF (1xxx)
    DAMP = 1001
    BALANCE_STAND = 1002
    STOP_MOVE = 1003
    STAND_UP = 1004
    STAND_DOWN = 1005
    RECOVERY_STAND = 1006
    EULER = 1007
    MOVE = 1008  # parameter: {"x": vx, "y": vy, "z": vyaw}
    SIT = 1009
    RISE_SIT = 1010
    SWITCH_GAIT = 1011
    TRIGGER = 1012
    BODY_HEIGHT = 1013
    FOOT_RAISE_HEIGHT = 1014
    SPEED_LEVEL = 1015
    HELLO = 1016  # shake hand
    STRETCH = 1017
    TRAJECTORY_FOLLOW = 1018
    CONTINUOUS_GAIT = 1019
    CONTENT = 1020
    WALLOW = 1021  # roll over
    DANCE1 = 1022
    DANCE2 = 1023
    GET_BODY_HEIGHT = 1024
    GET_FOOT_RAISE_HEIGHT = 1025
    GET_SPEED_LEVEL = 1026
    SWITCH_JOYSTICK = 1027
    POSE = 1028
    SCRAPE = 1029
    FRONT_FLIP = 1030
    FRONT_JUMP = 1031
    FRONT_POUNCE = 1032
    WIGGLE_HIPS = 1033
    GET_STATE = 1034
    ECONOMIC_GAIT = 1035
    FINGER_HEART = 1036
    # MCF-specific IDs (2xxx) — newer firmware
    LEFT_FLIP = 2041
    BACK_FLIP = 2043
    HAND_STAND = 2044
    FREE_WALK = 2045
    FREE_BOUND = 2046
    FREE_JUMP = 2047
    FREE_AVOID = 2048
    BACK_STAND = 2050
    CROSS_STEP = 2051
    LEAD_FOLLOW = 2056
    RAGE_MODE = 2059
    # MCF gait variants
    STATIC_WALK = 1061
    TROT_RUN = 1062
    MCF_ECONOMIC_GAIT = 1063
    # AI-only (also work in MCF for some firmware)
    WALK_STAIR = 1049


# ---------------------------------------------------------------------------
# Helpers — Move parameter shape, label tables
# ---------------------------------------------------------------------------


def move_parameter(vx: float, vy: float, vyaw: float) -> str:
    """JSON parameter for SportCmd.MOVE (api_id=1008).

    Coordinate convention (Unitree, matches the Go2 SDK):
        x = forward velocity (m/s, +x is forward)
        y = lateral velocity (m/s, +y is to the robot's left)
        z = yaw rate (rad/s, +z is CCW from above)
    """
    return f'{{"x":{float(vx)},"y":{float(vy)},"z":{float(vyaw)}}}'


SPORT_CMD_LABEL: Final[dict[int, str]] = {c.value: c.name.lower() for c in SportCmd}


def sport_cmd_label(api_id: int) -> str:
    return SPORT_CMD_LABEL.get(api_id, f"unknown({api_id})")


# Convenience: skill name → (api_id, parameter generator).
# Locomotion uses `MOVE` with a body-frame velocity vector; the bridge's
# `walk_to` / `turn` skills will keep their velocity loops and just publish
# Move requests through the WebRTC transport instead of `rt/run_command/cmd`.

GO2_SKILL_MOVE_API_ID: Final[int] = SportCmd.MOVE
GO2_SKILL_DAMP_API_ID: Final[int] = SportCmd.DAMP
GO2_SKILL_STAND_API_ID: Final[int] = SportCmd.RECOVERY_STAND
GO2_SKILL_SIT_API_ID: Final[int] = SportCmd.SIT
GO2_SKILL_STAND_UP_API_ID: Final[int] = SportCmd.STAND_UP
GO2_SKILL_STAND_DOWN_API_ID: Final[int] = SportCmd.STAND_DOWN
GO2_SKILL_HELLO_API_ID: Final[int] = SportCmd.HELLO
GO2_SKILL_STRETCH_API_ID: Final[int] = SportCmd.STRETCH
GO2_SKILL_WALLOW_API_ID: Final[int] = SportCmd.WALLOW
