"""Stage 0 test harness: runs on the Mac with no robot attached.

No DDS, no camera, no CUDA, no ROS. That is the whole point of this directory —
everything in `apps/perception/` that can be wrong in a way a human would not
notice is either pure maths (`c3po_vision.grounding`) or a JSON shape crossing
domain 42, and both are checkable on a laptop before a single container exists.

Two source trees are put on `sys.path` here rather than installed, because
neither is an installable package on this machine:

  * `apps/perception/vision`  — copied into the vision image as `/opt/c3po`.
    Its interpreter there is **python 3.8** (JetPack 5's TensorRT bindings
    hard-depend `python3 (>= 3.8), python3 (<< 3.9)`), so anything imported
    from it must stay 3.8-valid. It runs here under 3.12; `from __future__
    import annotations` is what makes both true at once.
  * `apps/bridge/src`         — the other side of the DDS boundary. Importing
    `bridge.world_model` pulls in no DDS: the module's whole design point is
    that the contract is testable with no robot.

The nav workspace IS on the path, but only its PURE modules may be imported.
Most modules there import `rclpy` at module scope, which does not exist on the
Mac and never will — `test_report_shape.py` reads `world_model_publisher.py` as
source instead of importing it, and says so. `costmap_png.py` is the exception:
stdlib only (struct + zlib), the same promise `c3po_vision.grounding` makes, so
it is imported normally and tested directly.

That works only while `c3po_perception/__init__.py` stays import-light — it is a
docstring and nothing else today, and `test_nav_package_init_stays_import_light`
below is what keeps it that way. If someone adds `from .g1_odom_tf import ...`
to it, importing ANY module in the package drags in rclpy and this whole suite
stops running off-robot.

`c3po_vision/__init__.py` MUST stay import-light (no `pyrealsense2`, no
`tensorrt`, no `pycuda`, no `cyclonedds`). `grounding.py` is specified as pure
functions with no camera, DDS or ROS dependency; if importing the package that
contains it drags in a CUDA runtime, that promise is broken and this suite
stops running off-robot — which is the failure it exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

VISION_SRC = REPO_ROOT / "apps" / "perception" / "vision"
BRIDGE_SRC = REPO_ROOT / "apps" / "bridge" / "src"

# Source of truth for the two files test_report_shape.py reads without
# importing. Kept here so a lane that moves one of them gets a single, obvious
# place to update rather than a grep across the suite.
NAV_PKG = REPO_ROOT / "apps" / "perception" / "nav" / "ws" / "src" / "c3po_perception"
WORLD_MODEL_PUBLISHER_PY = NAV_PKG / "c3po_perception" / "world_model_publisher.py"
PERCEPTION_LINK_PY = BRIDGE_SRC / "bridge" / "sdk" / "perception_link.py"

# NAV_PKG itself, not its parent. The ament layout nests the python package
# inside the ROS package of the same name — .../src/c3po_perception (ROS)
# contains .../c3po_perception (python) — so NAV_PKG is already the directory
# that CONTAINS the importable package. Using .parent finds the ROS package dir
# as a namespace package and then fails on the submodule, which reads as
# "no module named c3po_perception.costmap_png" and sends you looking in the
# wrong place entirely.
NAV_SRC = NAV_PKG

for _p in (VISION_SRC, BRIDGE_SRC, NAV_SRC):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


def test_nav_package_init_stays_import_light() -> None:
    """`c3po_perception/__init__.py` must not import a submodule.

    Guarding the property the docstring above depends on. Adding
    `from .g1_odom_tf import ...` there would make `import
    c3po_perception.costmap_png` pull in rclpy, and the entire off-robot suite
    would stop collecting — with an error pointing at the wrong file.
    """
    src = (NAV_PKG / "c3po_perception" / "__init__.py").read_text()
    offenders = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert offenders == [], f"__init__.py must import nothing, found: {offenders}"
