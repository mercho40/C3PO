"""nav2.launch.py's flag table, executed off-robot against recording stand-ins.

The four Phase 3 flags decide WHICH nodes exist and which parameter files they
read, inside an OpaqueFunction — logic, not declaration, so it is worth running
here rather than discovering on the robot. `launch`/`launch_ros` are replaced by
classes that only record their kwargs. That makes this a check of OUR choices
(which node, which file, which remap); whether Nav2 accepts the result is the
test-day checklist's job, and nothing here pretends otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest
from conftest import NAV_PKG


class _Rec:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class _LaunchConfiguration(_Rec):
    def perform(self, context):
        return context[self.args[0]]


class _Node(_Rec):
    pass


def _load_launch(monkeypatch):
    stubs = {
        "launch": {"LaunchDescription": _Rec},
        "launch.actions": {n: _Rec for n in (
            "DeclareLaunchArgument", "ExecuteProcess", "IncludeLaunchDescription",
            "OpaqueFunction")},
        "launch.conditions": {"LaunchConfigurationEquals": _Rec},
        "launch.launch_description_sources": {"PythonLaunchDescriptionSource": _Rec},
        "launch.substitutions": {"LaunchConfiguration": _LaunchConfiguration,
                                 "PathJoinSubstitution": _Rec},
        "launch_ros": {},
        "launch_ros.actions": {"Node": _Node},
        "launch_ros.substitutions": {"FindPackageShare": _Rec},
    }
    for name, attrs in stubs.items():
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location(
        "nav2_launch", NAV_PKG / "launch" / "nav2.launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEFAULTS = {"slam": "off", "controller": "dwb", "collision_monitor": "true", "map": "c3po"}


def nodes(monkeypatch, **flags):
    launch = _load_launch(monkeypatch)
    out = launch._navigation(dict(DEFAULTS, **flags), share="SHARE", params="PARAMS",
                             bt_to_pose="BT1", bt_through_poses="BT2")
    return {n.kwargs["name"]: n.kwargs for n in out}


def files_of(node):
    return [p.args[0][-1] if isinstance(p, _Rec) else p for p in node["parameters"]
            if not isinstance(p, dict)]


def test_defaults_are_the_old_stack_plus_the_monitor_and_the_relay(monkeypatch):
    n = nodes(monkeypatch)
    assert "collision_monitor" in n and "goal_relay" in n
    assert not {"slam_toolbox", "map_server", "lifecycle_manager_localization"} & set(n)
    assert files_of(n["controller_server"]) == ["PARAMS"]
    assert ("cmd_vel_smoothed", "cmd_vel_smoothed") in n["velocity_smoother"]["remappings"]
    assert n["goal_relay"]["parameters"] == [{"global_frame": "odom"}]


def test_without_the_monitor_the_smoother_names_c3po_cmd_vel_again(monkeypatch):
    n = nodes(monkeypatch, collision_monitor="false")
    assert "collision_monitor" not in n
    assert ("cmd_vel_smoothed", "/c3po/cmd_vel") in n["velocity_smoother"]["remappings"]
    managed = [p for p in n["lifecycle_manager_navigation"]["parameters"] if isinstance(p, dict)]
    assert managed and "collision_monitor" not in managed[0]["node_names"]


def test_mppi_is_layered_over_the_base_file(monkeypatch):
    n = nodes(monkeypatch, controller="mppi")
    assert files_of(n["controller_server"]) == ["PARAMS", "nav2_mppi.yaml"]
    assert files_of(n["velocity_smoother"]) == ["PARAMS", "nav2_mppi.yaml"]


def test_localization_moves_nav2_into_map_and_serves_the_saved_map(monkeypatch):
    n = nodes(monkeypatch, slam="localization", map="lab")
    assert files_of(n["planner_server"]) == ["PARAMS", "nav2_map_frame.yaml"]
    assert n["goal_relay"]["parameters"] == [{"global_frame": "map"}]
    assert n["slam_toolbox"]["executable"] == "localization_slam_toolbox_node"
    slam = n["slam_toolbox"]["parameters"][1]
    assert slam["map_file_name"] == "/opt/c3po/maps/lab" and slam["map_name"] != "/map"
    assert n["map_server"]["parameters"][0]["yaml_filename"] == "/opt/c3po/maps/lab.yaml"
    # Serving a map may autostart; driving may not.
    assert n["lifecycle_manager_localization"]["parameters"][0]["autostart"] is True
    assert n["lifecycle_manager_localization"]["parameters"][0]["node_names"] == ["map_server"]


def test_mapping_keeps_nav2_in_odom(monkeypatch):
    n = nodes(monkeypatch, slam="mapping")
    assert n["slam_toolbox"]["executable"] == "async_slam_toolbox_node"
    assert "map_server" not in n
    assert files_of(n["planner_server"]) == ["PARAMS"]


@pytest.mark.parametrize("flags", [{"slam": "on"}, {"controller": "teb"},
                                   {"collision_monitor": "yes"}, {"map": "../x"}])
def test_a_bad_flag_fails_the_launch(monkeypatch, flags):
    with pytest.raises(RuntimeError):
        nodes(monkeypatch, **flags)
