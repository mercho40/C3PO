"""C3PO's nav-container ROS 2 nodes.

Two nodes, deliberately: g1_odom_tf (frames + twist for Nav2) and
world_model_publisher (the D7 handover). Nothing here imports anything from
apps/bridge and nothing in apps/bridge imports rclpy — the two halves meet only
on DDS domain 42, as JSON in a std_msgs/String and a geometry_msgs/Twist.
"""
