# c3po-perception-nav — Livox Mid-360 + FAST-LIO2 + Nav2. No GPU, ever.
#
# Ubuntu 22.04 is legal here for exactly one reason: nothing in this container
# touches CUDA. JetPack 5.1.1's CUDA 11.4 / cuDNN 8.6 / TensorRT 8.5.2.2 exist
# only as focal debs and are NOT csv-mounted from the host — l4t.csv on this
# robot mounts libcuda.so.1.1 and nothing else. A GPU container here must be
# 20.04. See ../vision/Dockerfile.
#
# Staying on 22.04 buys the entire ROS binary archive: Nav2 1.1.20 arm64 debs
# in 90 seconds instead of a ~2 h source build that OOMs at 15 GB.
#
# Classic builder syntax only: this Jetson's docker (24.0.7 from the Ubuntu
# archive) has no buildx. No --platform, no RUN --mount=type=cache.
FROM ros:humble-ros-base-jammy

SHELL ["/bin/bash", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-navigation2 \
      ros-humble-nav2-bringup \
      ros-humble-rmw-cyclonedds-cpp \
      ros-humble-pcl-ros \
      ros-humble-pcl-conversions \
      ros-humble-pointcloud-to-laserscan \
      ros-humble-tf2-ros \
      ros-humble-tf2-sensor-msgs \
      ros-humble-tf2-geometry-msgs \
      ros-humble-foxglove-bridge \
      libpcl-dev libeigen3-dev libapr1-dev \
      python3-dev python3-numpy \
      cmake git build-essential ca-certificates patch \
 && rm -rf /var/lib/apt/lists/*
# python3-dev: FAST-LIO's CMakeLists does find_package(PythonLibs REQUIRED) for
#              matplotlibcpp and fails at configure without it.
# libapr1-dev: livox_ros_driver2 does pkg_check_modules(APR apr-1).
# Sophus is deliberately absent — that is a LIO-SAM dependency, not FAST-LIO2's.

# --- Livox-SDK2 must precede the driver ------------------------------------
# livox_ros_driver2's CMake hard-codes
#   find_library(LIVOX_LIDAR_SDK_LIBRARY liblivox_lidar_sdk_shared.so /usr/local/lib REQUIRED)
# It is a plain cmake project, so rosdep/colcon will never fetch it.
#
# THE SDK AND THE DRIVER ARE A MATCHED PAIR. Pin both, and pin them to these
# two. Measured across the actual refs:
#
#                          kLivoxLidarTypeMid360s   DoubleEcho symbols
#   SDK  v1.2.5                     absent               absent
#   SDK  v1.3.1                     present              absent
#   SDK  master                     present              present
#   driver 1.2.6                    uses it              does NOT use
#   driver master                   uses it              USES (4 sites)
#
# So driver master needs DoubleEcho, which NO SDK TAG carries — it exists only
# on SDK master. Building the head of both is how you end up pinned to nothing.
# driver 1.2.6 + SDK v1.3.1 is the newest pair that are both tags and both
# consistent, and 1.2.6 is precisely the release that introduced -DDISTRO_ROS.
#
# The old pinning (SDK v1.2.5 + driver master) was wrong on both sides at once
# and failed as a wall of `is not a member of LivoxLidarDeviceType` inside
# pub_handler.cpp — an error that names neither the SDK nor the version skew.
ARG LIVOX_SDK2_TAG=v1.3.1
RUN git clone --depth 1 -b ${LIVOX_SDK2_TAG} https://github.com/Livox-SDK/Livox-SDK2.git /tmp/sdk2 \
 && grep -q 'kLivoxLidarTypeMid360s' /tmp/sdk2/include/livox_lidar_def.h \
      || (echo "FATAL: this Livox-SDK2 predates kLivoxLidarTypeMid360s; the Mid-360 driver will not compile" && false) \
 && cmake -S /tmp/sdk2 -B /tmp/sdk2/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/sdk2/build -j3 \
 && cmake --install /tmp/sdk2/build \
 && ldconfig && rm -rf /tmp/sdk2

WORKDIR /opt/c3po/ws

# --- livox_ros_driver2 ------------------------------------------------------
# PIN 1.2.6, NOT master. -DDISTRO_ROS was only added in commit 13eb05e
# (2026-04-14), which tag 1.2.6 is the first to carry. Every earlier tag uses
# -DHUMBLE_ROS instead, and passing the wrong one is SILENTLY IGNORED: you land
# in the pre-Humble typesupport branch and CMake dies inside rosidl generation
# with no mention of the distro. So 1.2.6 is the FLOOR.
#
# It is also the ceiling, because master has moved on to SDK symbols that exist
# on no SDK tag — see the matrix above the Livox-SDK2 clone. "Newest of both"
# is not a pinning strategy; this pair is.
ARG LIVOX_DRIVER_REF=1.2.6
RUN git clone --depth 1 -b ${LIVOX_DRIVER_REF} \
      https://github.com/Livox-SDK/livox_ros_driver2.git src/livox_ros_driver2 \
 && grep -q 'DISTRO_ROS' src/livox_ros_driver2/CMakeLists.txt \
      || (echo "FATAL: pinned livox_ros_driver2 predates -DDISTRO_ROS; use -DHUMBLE_ROS or repin" && false) \
 && ! grep -rq 'DoubleEcho' src/livox_ros_driver2/src/ \
      || (echo "FATAL: this driver needs SDK DoubleEcho symbols, which exist on no Livox-SDK2 TAG (master only). Repin the pair together." && false)

# The repo ships NO package.xml — colcon skips any directory without one, so a
# bare `colcon build` finds zero packages. Upstream's build.sh copies these two
# files. We replicate them inline rather than running build.sh, because build.sh
# opens with `rm -rf ../../build ../../devel ../../install` and would wipe
# fast_lio out of this shared workspace on every rebuild.
RUN cd src/livox_ros_driver2 \
 && cp -f package_ROS2.xml package.xml \
 && cp -rf launch_ROS2/ launch/

# --- FAST-LIO2 --------------------------------------------------------------
# hku-mars upstream, ROS2 branch. NOT deepglint/FAST_LIO_LOCALIZATION_HUMANOID:
# its humble branch ships no livox driver, its README still says catkin_make,
# and it is map-relative localization rather than SLAM. The upside-down mount is
# handled by a static TF in our launch file, not by anything in here.
ARG FASTLIO_REF=ROS2
RUN git clone --recursive --depth 1 -b ${FASTLIO_REF} \
      https://github.com/hku-mars/FAST_LIO.git src/fast_lio \
 && git -C src/fast_lio submodule update --init --recursive \
 && test -f src/fast_lio/include/ikd-Tree/ikd_Tree.h \
      || (echo "FATAL: ikd-Tree submodule missing (clone without --recursive?)" && false)

# Three upstream defects that only bite on Humble/aarch64. Each sed is followed
# by an assertion, because a sed that silently fails to match is exactly how you
# get a build that succeeds and a robot that runs at 1/6 speed.
#
# 1. CMakeLists sets -std=c++17 and then APPENDS "-std=c++14 -std=c++0x
#    -std=c++14". Last -std on the command line wins, so a stock build compiles
#    Humble's rclcpp/pcl_ros headers as C++14.
# 2. -DMP_EN / MP_PROC_NUM are only set when CMAKE_SYSTEM_PROCESSOR matches
#    x86/amd64. On aarch64 OpenMP is compiled out and MP_PROC_NUM=1. It builds,
#    it runs, it is just single-threaded — invisible until you wonder why
#    odometry stalls. The Orin NX has 8 cores.
# 3. publish_odometry() never touches twist. Nav2 reads the robot's ACTUAL
#    velocity from that field; left at zero, DWB samples a velocity space
#    anchored at standstill every cycle. The patch publishes the IESKF state
#    velocity rotated into the body frame — do NOT "fix" this by differentiating
#    the pose (world-frame velocity in a child_frame_id field = a silent,
#    heading-dependent sign bug).
COPY patches/fastlio-publish-twist.patch /tmp/
RUN cd src/fast_lio \
 && sed -i 's/ -std=c++14 -pthread -std=c++0x -std=c++14 -fexceptions/ -pthread -fexceptions/' CMakeLists.txt \
 && ! grep -q 'std=c++0x' CMakeLists.txt \
 && sed -i 's/MATCHES "(x86)|(X86)|(amd64)|(AMD64)"/MATCHES "(x86)|(X86)|(amd64)|(AMD64)|(aarch64)|(arm64)"/' CMakeLists.txt \
 && grep -q 'aarch64' CMakeLists.txt \
 && patch -p1 --forward < /tmp/fastlio-publish-twist.patch \
 && grep -q 'twist.twist.linear' src/laserMapping.cpp

# THE THIRD-PARTY C++ IS BUILT BEFORE OUR PACKAGE IS COPIED IN, AND THAT ORDER
# IS THE WHOLE POINT.
#
# These two are the expensive half — PCL and Sophus translation units, ~8 min on
# a loaded Orin NX. c3po_perception is ament_python and builds in 1.4 seconds.
# With a single `colcon build` after a single COPY, editing one launch file
# invalidated the layer and rebuilt all three, so every config iteration cost
# the full 8 minutes. It happened five times in one afternoon, and on a robot
# shared with the gemm stack that is not just our own wall-clock — a colcon
# build at -j3 competes with their live SLAM for the same 8 cores.
#
# Split this way, the pins above are the only thing that can invalidate this
# layer, which is correct: it is exactly what those pins describe.
#
# -j3 / 2 workers deliberately: 8 cores but 15 GB RAM, and PCL translation
# units are the classic Orin NX OOM. Do not raise these.
RUN source /opt/ros/humble/setup.bash \
 && MAKEFLAGS="-j3" colcon build --parallel-workers 2 \
      --packages-select livox_ros_driver2 fast_lio \
      --cmake-args -DCMAKE_BUILD_TYPE=Release \
                   -DROS_EDITION=ROS2 -DDISTRO_ROS=humble \
 && test -d install/livox_ros_driver2 && test -d install/fast_lio \
 && rm -rf build log

# --- domain_bridge: the LiDAR arrives on domain 0, we live on domain 42 ------
#
# Our own livox_ros_driver2 does NOT get the sensor while the vendor's
# `lidar_driver` service is running: the command channel connects, the host
# config push appears to succeed, and the point data keeps going to the control
# board (ROBOT-HARDWARE.md §4.3, measured). The vendor's DDS republish is the
# route that actually works — and it is multi-consumer, so it costs the
# co-tenant nothing.
#
# But it publishes on DOMAIN 0, and this container is deliberately domain-42
# only. A ROS 2 process is single-domain (ROS_DOMAIN_ID is process-wide), so
# consuming it means either putting FAST-LIO on the shared domain — which would
# put our TF and costmaps on the wire domain 42 exists to keep them off — or
# bridging. domain_bridge is purpose-built for exactly this: one process, two
# contexts, named topics only.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-domain-bridge \
 && test -f /opt/ros/humble/lib/domain_bridge/domain_bridge \
      || (echo "FATAL: domain_bridge did not install" && false) \
 && rm -rf /var/lib/apt/lists/*

COPY ws/src/c3po_perception src/c3po_perception

# Our package alone, so this is the only layer a launch/config/python edit
# rebuilds. It depends on neither package above (ament_python, and its
# exec_depends are stock ROS), so --packages-select cannot silently skip a
# dependency — but the overlay is sourced anyway so a future dependency would
# resolve rather than fail confusingly.
#
# The assertion is not decoration. `colcon build` EXITS 0 WHEN IT SELECTS NO
# PACKAGES, so a typo in the name here, or a package.xml that stops being
# discoverable, produces a green build and an image whose entrypoint dies on
# "package not found" much later. Verify the install tree, not the exit code.
RUN source /opt/ros/humble/setup.bash \
 && source install/setup.bash \
 && colcon build --packages-select c3po_perception \
      --cmake-args -DCMAKE_BUILD_TYPE=Release \
 && test -f install/c3po_perception/share/c3po_perception/launch/nav2.launch.py \
      || (echo "FATAL: c3po_perception did not install — colcon exits 0 when it selects nothing" && false) \
 && test -d install/livox_ros_driver2 \
      || (echo "FATAL: the second colcon build dropped the first one's install tree" && false) \
 && rm -rf build log

# NOT a multi-stage build, and it must not become one: the runtime needs the ROS
# install tree, the source tree and the workspace overlay together. (The spec
# carried a `COPY --from=0 /dev/null /dev/null` no-op here to make that visible;
# it is dropped because stage 0 IS this stage and the classic builder errors on
# it. This comment is the same fence.)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Domain 42, not 0: keeps our TF, costmaps and planner traffic off the domain
# the control board, gemm-bringup and gemm-ai all share.
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    CYCLONEDDS_URI=file:///opt/c3po/config/cyclonedds-domain42.xml \
    ROS_DOMAIN_ID=42

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
