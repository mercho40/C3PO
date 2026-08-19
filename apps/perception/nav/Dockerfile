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
ARG LIVOX_SDK2_TAG=v1.2.5
RUN git clone --depth 1 -b ${LIVOX_SDK2_TAG} https://github.com/Livox-SDK/Livox-SDK2.git /tmp/sdk2 \
 && cmake -S /tmp/sdk2 -B /tmp/sdk2/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/sdk2/build -j3 \
 && cmake --install /tmp/sdk2/build \
 && ldconfig && rm -rf /tmp/sdk2

WORKDIR /opt/c3po/ws

# --- livox_ros_driver2 ------------------------------------------------------
# PIN MASTER. -DDISTRO_ROS was only added in commit 13eb05e (2026-04-14, tag
# 1.2.6). Every earlier tag uses -DHUMBLE_ROS instead, and passing the wrong
# one is SILENTLY IGNORED: you land in the pre-Humble typesupport branch and
# CMake dies inside rosidl generation with no mention of the distro.
ARG LIVOX_DRIVER_REF=master
RUN git clone --depth 1 -b ${LIVOX_DRIVER_REF} \
      https://github.com/Livox-SDK/livox_ros_driver2.git src/livox_ros_driver2 \
 && grep -q 'DISTRO_ROS' src/livox_ros_driver2/CMakeLists.txt \
      || (echo "FATAL: pinned livox_ros_driver2 predates -DDISTRO_ROS; use -DHUMBLE_ROS or repin" && false)

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

COPY ws/src/c3po_perception src/c3po_perception

# -j3 / 2 workers deliberately: 8 cores but 15 GB RAM, and PCL translation
# units are the classic Orin NX OOM. Do not raise these.
RUN source /opt/ros/humble/setup.bash \
 && MAKEFLAGS="-j3" colcon build --parallel-workers 2 \
      --cmake-args -DCMAKE_BUILD_TYPE=Release \
                   -DROS_EDITION=ROS2 -DDISTRO_ROS=humble \
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
