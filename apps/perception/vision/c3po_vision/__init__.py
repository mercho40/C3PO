"""c3po-perception-vision — the RealSense/TensorRT half of the perception split.

This package has NO ROS in it, by design (apps/perception/README.md;
docs/DECISIONS.md D2.2 option 1). It
owns the D435i, runs YOLO11 on TensorRT, resolves each detection to an
egocentric range/bearing using a fixed extrinsic, and publishes the result as
one std_msgs/String of JSON on DDS domain 42. That is the entire contract.

Layout, and the reason for it:

    grounding.py   pure maths, stdlib only — imports on a Mac with no hardware,
                   which is what makes the sign conventions testable
    ros_idl.py     the one wire type; must stay in lockstep with the bridge's
    detector.py    devices, CUDA and DDS — everything that cannot be unit tested

Run as `python3 -m c3po_vision.detector`. Under python3.8 in the container
(JetPack 5's TensorRT bindings are cp38-only); grounding.py and ros_idl.py also
import cleanly under the bridge's python3.12.
"""

# Deliberately no __all__ and no submodule imports here: importing this package
# must never drag in detector.py, which reaches for pyrealsense2/TensorRT. The
# tests import c3po_vision.grounding on a laptop, and that has to stay free.
