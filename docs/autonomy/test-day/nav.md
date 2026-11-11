# Test day — Nav2 goals, a saved map, a landmark goal (Phase 3)

Branch `feat/autonomy`. Still nothing here has run on the robot: the Mid-360S scan module
is faulted (BACKLOG 4.1), and the nav-image rebuild was cut off by the battery (4.5). Only
§2 (`nav2-fake`, gate closed) can run until both are fixed. Every step changes one
thing, says what you should see, and has a one-line rollback. Stop at the
first step whose expected result does not appear. The next step assumes it did.

**Before anything, every time:** the physical e-stop is in someone's hand, a
second person watches the robot, the sensor window is agreed with the gemm team
(`C3PO_TAKE_OVER=1` needs their OK, see `docs/OPERATIONS.md` §5), and nothing
else is commanding the robot (`c3po status`, one commander, §4).

Tools are MCP calls on the bridge. Use a Claude Code session with the
`c3po-bridge` server, with `scripts/robot_tunnel.sh` up so `127.0.0.1:8001`
reaches the robot. Shell lines run on the robot (`ssh c3po`, then `bash -l`).

## What is new, and the flags that turn it off

| Piece                                                                                     | Default                    | Rollback                                                                               |
| ----------------------------------------------------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------- |
| `goal_relay` node (`/c3po/nav_goal` → NavigateToPose → `/c3po/nav_status`)                | on, in every `nav2*` stage | none needed. It only relays goals, and goals only move the robot through an armed gate |
| Collision Monitor after the velocity smoother (stop box 0.6 m ahead, slow box 1.0 m)      | **on**                     | `C3PO_NAV_COLLISION_MONITOR=0` (smoother publishes `/c3po/cmd_vel` as before)          |
| slam_toolbox mapping / localization + map_server                                          | **off**                    | leave `C3PO_NAV_SLAM` unset                                                            |
| MPPI (Omni) controller                                                                    | **off**, DWB stays         | leave `C3PO_NAV_CONTROLLER` unset                                                      |
| MCP tools `navigate_to`, `navigate_to_landmark`, `navigation_status`, `cancel_navigation` | present                    | they refuse while the gate is closed. `stop_everything` cancels the goal               |

With every flag unset, `c3po up nav2` passes the same launch arguments it
always did. The only differences are the monitor and the relay.

A useful shell alias for the steps below:

```bash
nav() { docker exec c3po-perception-nav /entrypoint.sh "$@"; }   # sources both ROS overlays
activate() { nav ros2 service call /lifecycle_manager_navigation/manage_nodes \
  nav2_msgs/srv/ManageLifecycleNodes "{command: 0}"; }
```

---

## 1. Deploy (robot stopped, nobody on it)

Deploy as in `docs/OPERATIONS.md` §5 (push from the Mac, `git merge --ff-only incoming` on
the robot). The nav build needs the proxy (`docs/OPERATIONS.md` §2) and must include the
Mid360s config from a0243e5.

```bash
./scripts/robot/c3po install && c3po restart                   # bridge: new tools + stop cancels goals
export http_proxy=http://127.0.0.1:18899 https_proxy=http://127.0.0.1:18899 no_proxy=localhost,127.0.0.1,::1
c3po perception build nav                                      # nav image: + slam_toolbox layer
```

**Expect:** the build ends without `FATAL:`. The new layer asserts
`slam_toolbox`, `nav2_collision_monitor` and `nav2_map_server`. Only the
layers after the expensive colcon build rebuild (one apt layer plus our
package), not the 8-minute PCL build. `c3po restart` says the bridge answers
on 8001.

**Rollback:** as in `docs/OPERATIONS.md` §5 ("Rollback"). The old nav image tag is overwritten, so a rebuild on the old branch is needed to fully revert the container (~2 min for the top layers).

## 2. Sensor-free rehearsal: `nav2-fake`, gate CLOSED

> ⚠️ **Never `arm_navigation` in `nav2-fake`.** The fake robot never moves, so
> Nav2 keeps commanding. An armed gate forwards those commands to the **real**
> legs, open-loop. Everything in this step runs with the gate shut.

```bash
c3po up nav2-fake            # claims no sensor
activate                     # expect: success: True after ~10-20 s
nav ros2 lifecycle get /collision_monitor     # expect: active [3]
nav ros2 topic echo --once /c3po/nav_status   # expect: "nav2_ready":true, "global_frame":"odom"
```

MCP:

1. `navigation_status()` → `relay_online: true`, `nav2_ready: true`, `gate.enabled: false`.
2. `navigate_to(x=0.5, y=0)` → `status: refused`, `reason: gate_closed`, and a
   `how_to_fix` naming `arm_navigation`. **The gate must still be closed afterwards.**

Then the relay path itself, with the gate still closed. Publish a goal by hand,
because the tool will not send one through a shut gate:

```bash
nav ros2 topic pub --once /c3po/nav_goal std_msgs/msg/String \
  "{data: '{\"v\":1,\"id\":\"t1\",\"x\":0.5,\"y\":0.0}'}"
nav ros2 topic echo --once /c3po/nav_status          # goal.id t1, state accepted/active
curl -s 127.0.0.1:8001/telemetry/gate                # dropped_while_disabled climbing, last_sent null
nav ros2 topic pub --once /c3po/nav_goal std_msgs/msg/String \
  "{data: '{\"v\":1,\"cancel\":true}'}"
nav ros2 topic echo --once /c3po/nav_status          # goal.state canceled
```

**Failure readings:**

- `activate` fails and `docker logs c3po-perception-nav` names `collision_monitor`: a parameter spelling mismatch (Humble uses `max_points` and a flat `points` list). Rerun with `C3PO_NAV_COLLISION_MONITOR=0 c3po up nav2-fake` and note the error.
- `nav2_ready: false` after `activate` succeeded: bt_navigator is not active. Check the logs.
- No `/c3po/nav_status` at all: `goal_relay` crashed. Check `docker logs c3po-perception-nav | grep goal_relay`.

## 3. Real Nav2 on the gantry (robot hanging, feet just clear)

First `c3po preflight`: it must show the LiDAR online. If it does not, stop here.

```bash
c3po down
C3PO_TAKE_OVER=1 c3po up nav2      # with the gemm team's OK
activate
nav ros2 run tf2_ros tf2_echo odom base_footprint    # z ≈ 0, roll/pitch ≈ 0
```

MCP, with the e-stop in hand, robot in its walk mode (`prepare` →
`start_walking_waist`, same as for the walk pad; confirm `fsm_id` 501, because 802 accepts
velocity and ignores it):

1. `navigation_status()` → note `pose` (in `odom`).
2. `arm_navigation(reason="gantry: first Nav2 goal, 0.5 m", seconds=30)`.
3. `navigate_to(x=pose.x + 0.5·cos(yaw), y=pose.y + 0.5·sin(yaw))` → `status: started` and a `task_id`.
4. Watch: the legs step, and `/telemetry/gate` `last_sent` is non-zero.
   Hanging, the robot cannot translate, so after ~12 s the progress checker
   aborts: `navigation_status().task.status == "failed"`, `phase: aborted`.
   **That is the pass.** It proves goal → plan → cmd_vel → gate → legs.
5. Repeat 2–3, then call `stop_everything` mid-goal. Expect the legs to stop,
   `goal.state` to go `canceled`, the task `cancelled`, and the gate closed.
6. Repeat 2–3 and let the TTL run out (`seconds=10`). Expect task
   `phase: gate_closed` and the goal cancelled in Nav2: it must **not**
   resume when you arm again.

**Rollback:** `stop_everything`, then `c3po down`.

## 4. First free-standing goal: 0.5 m, empty floor

Same as step 3, on the floor, `arm_navigation(..., seconds=30)`, a 0.5 m goal
straight ahead.

**Expect:** the robot walks about 0.5 m at ≤ 0.35 m/s and stops within 0.20 m
and 0.20 rad of the goal. The task reaches `completed`. The bridge clamps and
`velocity_smoother` limits are what bound it. Note the actual distance walked
with a tape measure.

Then the Collision Monitor. Put a box 0.8 m ahead and send a 1.5 m goal:
**expect** the robot to slow inside 1.0 m and stop before 0.6 m. Nothing
within 0.45 m is visible to it (the self-body crop). If it walks into the box,
`stop_everything` and rerun with `C3PO_NAV_COLLISION_MONITOR=0` only to tell a
monitor problem apart from a costmap one.

## 5. Mapping session

```bash
c3po down
C3PO_TAKE_OVER=1 c3po map start lab       # = C3PO_NAV_SLAM=mapping c3po up nav2
nav ros2 topic hz /map                    # slam_toolbox publishing, ~0.5 Hz
```

**Mark the robot's start spot on the floor.** Localization starts there.
Walk the robot slowly around the room with the console walk pad (Nav2 is not
needed and the gate stays closed). Close at least one loop back to the start.
Then:

```bash
c3po map save lab
ls -l ~/.c3po/maps/                        # lab.posegraph lab.data lab.yaml lab.pgm
```

**Failure readings:**

- `LaserRangeScan contains N range readings, expected M` in the logs: the 360° scan's bin count. Note it and stop. The fix is in `pointcloud_to_laserscan.yaml` `angle_max`, which also changes the costmap's input, so it needs its own step.
- The map smears when the robot walks: sway is tilting the scan. Note it. Research 03 §2 step 2 (3D localization) is the documented next move, not tuning.

## 6. Localization after a restart

```bash
c3po down                                  # (or reboot the robot)
# robot back on the start mark, facing the same way
C3PO_TAKE_OVER=1 C3PO_NAV_SLAM=localization C3PO_NAV_MAP=lab c3po up nav2
activate
nav ros2 run tf2_ros tf2_echo map odom     # a transform exists
```

**Expect:** `navigation_status()` reports `localized: true`,
`global_frame: map`, and `pose` near (0, 0, 0). Walk 1 m with the pad: `pose`
moves about 1 m in the right direction. `c3po up` refuses with a pointer to
`nav_map_save` if `~/.c3po/maps/lab.yaml` is missing.

Started off the mark? Give an initial pose:
`nav ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "{header: {frame_id: map}, pose: {pose: {position: {x: X, y: Y}, orientation: {z: SIN_HALF_YAW, w: COS_HALF_YAW}}}}"`

**Rollback:** `c3po down && C3PO_TAKE_OVER=1 c3po up nav2` (odom only, as in step 3).

## 7. Landmark goal

With step 6's stack up:

1. Walk the robot (pad) to the door. Call `remember_landmark("puerta")`.
   Expect `nav_pose.frame == "map"`.
2. Walk it about 2 m away. Call `arm_navigation(reason="landmark goal: puerta", seconds=60)`,
   then `navigate_to_landmark("puerta")`.
   **Expect** `completed` within 0.2 m of the spot.
3. Persistence: `c3po down`, then bring step 6's stack back up and `activate`.
   `navigate_to_landmark("puerta")` must still be **accepted**, because `map`
   landmarks survive a restart. An `odom` landmark (one saved in step 3 or 4)
   must be refused as `odom_frame_restarted`.

## 8. Optional, last: MPPI

```bash
nav ros2 pkg prefix nav2_mppi_controller   # must exist; it is not asserted at build
c3po down && C3PO_NAV_CONTROLLER=mppi c3po up nav2-fake && activate
```

Gate closed (see step 2). If `controller_server` dies on activation
(SIGILL, navigation2#5061), MPPI is out for this image. Unset the flag. Only
if it activates cleanly, repeat step 4 with it. Note that the bridge clamps
`vy` to ±0.20 m/s however MPPI is configured.

---

## Unverified until this checklist runs

- Humble 1.1.20's exact Collision Monitor parameter names, and whether
  `nav2_collision_monitor` / `nav2_mppi_controller` ship in this image's
  `ros-humble-navigation2`.
- The goal relay's action-client callbacks, its TF pose and the 2 Hz heartbeat
  (pure JSON logic is tested off-robot; the ROS half is not).
- slam_toolbox on a 360° scan from a swaying humanoid, and relocalization from
  `map_start_at_dock`.
- Stop and slow polygon sizes, and `max_points: 2`. They are reasoned from the
  footprint, not measured.
- The console map (`/telemetry/costmap.png`) in localization mode. The costmap is
  then in `map`, and the payload's `frame_id` says so, but the console's robot
  marker has not been checked against it.
