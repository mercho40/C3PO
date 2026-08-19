import os
from glob import glob

from setuptools import setup

package_name = "c3po_perception"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    # The launch files and the YAML/JSON they point at MUST be installed, not
    # read out of the source tree: the container runs from the colcon install
    # overlay (/opt/c3po/ws/install), and the Dockerfile deletes build/ and
    # log/ after building. A config that is only in src/ resolves fine on a
    # developer's machine and is missing on the robot.
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"),
         glob("launch/*.launch.py")),
        # glob("config/*") on purpose — MID360_config.json is consumed by
        # livox_ros_driver2 via an absolute path we build from this share
        # directory, so it has to land here alongside the YAML.
        (os.path.join("share", package_name, "config"),
         [p for p in glob("config/*") if os.path.isfile(p)]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="C3PO",
    maintainer_email="ivanmersich@gmail.com",
    description="FAST-LIO2 -> Nav2 frame adapter and the D7 world-model handover.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "g1_odom_tf = c3po_perception.g1_odom_tf:main",
            "world_model_publisher = c3po_perception.world_model_publisher:main",
        ],
    },
)
