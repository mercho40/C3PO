# Vendored robot description

`g1_29dof_rev_1_0.urdf` — Unitree G1 29-DoF, revision 1.0, copied unmodified from
[`unitreerobotics/unitree_ros`](https://github.com/unitreerobotics/unitree_ros/blob/master/robots/g1_description/g1_29dof_rev_1_0.urdf)
on 2026-11-02 (BSD-3-Clause, © Unitree Robotics). Meshes are not vendored. `bridge.kinematics`
reads only joint origins, axes and limits.

Why rev 1.0: its shoulder, waist and D435 offsets match `g1_body29_hand14.urdf` in
`xr_teleoperate`, which already drives this robot's arms. The plain `g1_29dof.urdf` differs by
about 1 cm in the shoulder and camera heights.
