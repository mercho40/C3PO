# c3po-perception-vision — RealSense D435i + YOLO11 on TensorRT.
#
# Ubuntu 20.04 is NOT a choice on this host:
#   * /etc/nvidia-container-runtime/host-files-for-container.d/l4t.csv mounts
#     libcuda.so.1.1 and its symlinks. No libcudart, no libcudnn, no libnvinfer.
#     The host's TensorRT 8.5.2.2 is unreachable from any container, so
#     CUDA + cuDNN + TRT must live IN the image.
#   * The only source of those for L4T r35 is repo.download.nvidia.com/jetson
#     COMMON r35.3 (not t234 — that is the BSP), which is focal-only.
#   * python3-libnvinfer 8.5.2 hard-depends `python3 (>= 3.8), python3 (<< 3.9)`.
#     The TRT Python bindings are literally uninstallable on jammy.
#
# l4t-jetpack:r35.3.1 over l4t-tensorrt:r8.5.2.2-devel because the latter is
# built on an r35.4 (JetPack 5.1.2) base — container newer than host is the
# unsupported direction. This tag matches /etc/nv_tegra_release exactly.
#
# NO ROS. Under D2.2 option 1 this container publishes already-resolved
# egocentric detections as plain CycloneDDS; the camera extrinsic is a fixed
# constant, so there is nothing for TF to do. (In a MONOLITH that argument
# fails — dustynv:desktop already ships cv_bridge/image_transport/vision_msgs —
# but in this split it removes an entire from-source ROS build on focal.)
FROM nvcr.io/nvidia/l4t-jetpack:r35.3.1

SHELL ["/bin/bash", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

# Fail LOUDLY if this base ever stops shipping the TensorRT Python bindings.
#
# WHAT THIS CANNOT CHECK HERE, AND WHY. `import tensorrt` does NOT work at build
# time, and the reason is not a missing package: it dlopens
# libnvdla_compiler.so, which is not in the image and is not in any deb. It
# lives on the HOST rootfs at /usr/lib/aarch64-linux-gnu/tegra/ and is put into
# the container by the nvidia container runtime. Verified on this robot:
#
#   docker run --rm                  ... python3 -c "import tensorrt"
#       -> ImportError: libnvdla_compiler.so: cannot open shared object file
#   docker run --rm --runtime nvidia ... python3 -c "import tensorrt"
#       -> TensorRT 8.5.2.2 py 3.8.10
#
# `docker build` has no --runtime flag, so the only way to import it here would
# be "default-runtime": "nvidia" in /etc/docker/daemon.json — on a daemon shared
# with the gemm team, where any recreate of their containers would silently pick
# the new runtime up. Not worth it for an assertion.
#
# So the build checks what a build CAN check — the bindings and trtexec are
# installed, and the interpreter is the 3.8 the bindings hard-depend on — and
# entrypoint.sh runs the real `import tensorrt` at container start, under
# --runtime nvidia, where it is meaningful.
RUN test -x /usr/src/tensorrt/bin/trtexec \
 && test -f /usr/lib/python3.8/dist-packages/tensorrt/__init__.py \
 && dpkg -s python3-libnvinfer >/dev/null \
 && python3 -c "import sys; assert sys.version_info[:2] == (3, 8), sys.version"

RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake build-essential pkg-config ca-certificates \
      python3-dev python3-pip \
      libusb-1.0-0-dev libudev-dev \
 && rm -rf /var/lib/apt/lists/*

# --- RealSense: the STOCK V4L2 wheel, not an RSUSB source build -------------
# 2.55.1.6486 is the LAST cp38/aarch64 wheel on PyPI (Apr 2024); 2.56+ dropped
# cp38 aarch64 entirely, and this container is python3.8 because JetPack 5's TRT
# bindings are. Pin it.
#
# This wheel is a V4L2 build — verified by symbol inspection: backend-v4l2.cpp,
# v4l_uvc_device, is_platform_jetson, and ZERO rsusb/libuvc symbols. So it needs
# /dev/video* and /run/udev passed through, and it does NOT need the host udev
# rules file that an RSUSB build would (which is not installed on this robot and
# would need root on a shared machine to install). RSUSB would additionally
# claim the WHOLE D435i by detaching uvcvideo, and Intel labels it
# "not officially validated ... for prototyping and demos" with Orin NX not on
# the validated board list. V4L2 is the lower-risk path here.
#
# Its dynamic deps are libcuda.so.1, libudev.so.1, libusb-1.0.so.0 (cudart is
# statically linked), which is why this container MUST run --runtime nvidia even
# though the default runtime on this daemon is runc.
# --- CycloneDDS, the C library, BEFORE the Python binding -------------------
# The `cyclonedds` PyPI package publishes no aarch64/cp38 wheel, so pip builds
# it from source, and its build backend needs the C library at BUILD time:
#
#   Could not locate cyclonedds. Try to set CYCLONEDDS_HOME or CMAKE_PREFIX_PATH
#
# That message names the fix but not the cause, and it arrives ~4 minutes into
# the image build. There is no apt package for it on focal either — the host's
# copy is a from-source tree at ~/cyclonedds_ws, which is exactly what the
# bridge's own .env points CYCLONEDDS_HOME at. So we build the same thing here.
#
# 0.10.2 to match apps/bridge EXACTLY. This is not tidiness: the two ends of
# domain 42 must agree on the config schema, and 0.7.x (the Foxy copy also on
# this host) uses <NetworkInterfaceAddress> where 0.10.x uses <Interfaces>.
# A mismatch there means the XML parses on one side and is ignored on the
# other, and the reader silently matches nothing.
#
# -DBUILD_IDLC=NO: nothing in this container compiles .idl — ros_idl.py
# hand-writes the one struct that leaves here, precisely so no code generation
# sits in the runtime path. (idlc itself is plain C in 0.10.2 and needs no JDK;
# we skip it because it is unused, not because it is expensive.)
#
# -DBUILD_DDSPERF=NO IS REQUIRED WHENEVER BUILD_IDLC=NO. Both default to
# ${not_crosscompiling}, i.e. ON for a native build, and ddsperf generates its
# types with idlc — so turning the compiler off without turning ddsperf off
# fails at 19% with
#   make[1]: *** [.../ddsperf/CMakeFiles/ddsperf_types_generate.dir/all] Error 2
# which says nothing about IDLC. ddsperf is a benchmarking tool we never run.
ARG CYCLONEDDS_TAG=0.10.2
ENV CYCLONEDDS_HOME=/usr/local
RUN git clone --depth 1 -b ${CYCLONEDDS_TAG} https://github.com/eclipse-cyclonedds/cyclonedds.git /tmp/cdds \
 && cmake -S /tmp/cdds -B /tmp/cdds/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DBUILD_IDLC=NO -DBUILD_DDSPERF=NO -DBUILD_TESTING=NO -DBUILD_EXAMPLES=NO \
 && cmake --build /tmp/cdds/build -j3 \
 && cmake --install /tmp/cdds/build \
 && ldconfig && rm -rf /tmp/cdds \
 && test -f /usr/local/lib/libddsc.so \
      || (echo "FATAL: libddsc.so missing — the cyclonedds python binding will fail to build" && false)

# numpy is PINNED EXACTLY, not bounded. `numpy<2` looks like a pin and is not:
# l4t-jetpack:r35.3.1 already ships numpy 1.17.4, which satisfies it, so pip
# resolved the constraint to "already installed" and changed nothing. The image
# then ran on a 2019 numpy while every comment here claimed otherwise.
#
# 1.24.4 is the last release with cp38 wheels, and cp38 is not negotiable —
# JetPack 5's python3-libnvinfer hard-depends python3 (<< 3.9). pyrealsense2
# 2.55 and pycuda are both built against a much newer C-API than 1.17's, and an
# ABI mismatch there surfaces as a segfault inside np.asanyarray() on a camera
# frame, not as an import error.
#
# Pillow is the fifth package, and it buys the operator's live view: it is the
# JPEG encoder behind c3po_vision/stream.py. The alternative was OpenCV for
# `imencode` — ~100 MB of image, a second numpy ABI to keep in step, and a
# python3-opencv from focal built against the base image's 1.17 — to compress
# one 640x480 frame five times a second. 10.4.0 is the LAST Pillow that supports
# python 3.8 (11.0 requires >=3.9) and it ships a cp38 manylinux2014_aarch64
# wheel, so this is a download, not a source build against libjpeg headers.
# Do not float it: the next minor drops this interpreter and pip would resolve
# to a version that cannot install here.
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir \
      "pyrealsense2==2.55.1.6486" \
      "numpy==1.24.4" \
      "cyclonedds==0.10.2" \
      "pycuda==2022.2.2" \
      "Pillow==10.4.0"
# cyclonedds 0.10.2 pinned to match apps/bridge exactly. Do NOT drift to 0.7.x —
# that is the Foxy copy on this host and its config schema is incompatible
# (<NetworkInterfaceAddress> vs <Interfaces>).
# pycuda 2022.2.2 is the known-good pin for CUDA 11.4 / py3.8; newer assumes CUDA 12.

# Assert the pins TOOK. pip is silent when a base-image package already
# satisfies a constraint, so "it installed without error" does not mean "the
# version you asked for is what is here" — which is exactly how 1.17.4 survived
# a `numpy<2`. Checking the C-API version too, because that is the number that
# actually has to match what pyrealsense2 and pycuda were compiled against.
#
# Single-line `python3 -c`, NOT a heredoc: this Jetson's docker is 24.0.7 from
# the Ubuntu archive with no buildx, so `RUN <<EOF` is unavailable and a
# heredoc without backslash continuations parses as separate instructions.
RUN python3 -c "import numpy; assert numpy.__version__ == '1.24.4', 'numpy is ' + numpy.__version__ + ', not the pinned 1.24.4 — a base-image copy satisfied the constraint and pip did nothing'; print('numpy', numpy.__version__, 'C-API', numpy.core._multiarray_umath._get_ndarray_c_version())" \
 && python3 -c "import pyrealsense2, cyclonedds, sys; print('pyrealsense2 + cyclonedds import under python', sys.version.split()[0])" \
 && python3 -c "import PIL; assert PIL.__version__ == '10.4.0', 'Pillow is ' + PIL.__version__ + ', not the pinned 10.4.0'; from PIL import features; assert features.check('jpg'), 'Pillow built without JPEG support — the wheel was not used and the operator video would fail at the first encode, at runtime, on the robot'; print('Pillow', PIL.__version__, 'jpeg ok')"

COPY c3po_vision /opt/c3po/c3po_vision
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/opt/c3po:${PYTHONPATH} \
    CYCLONEDDS_URI=file:///opt/c3po/config/cyclonedds-domain42.xml \
    C3PO_DDS_DOMAIN=42 \
    C3PO_ONNX=/opt/c3po/models/yolo11n.onnx \
    C3PO_ENGINE=/opt/c3po/engines/yolo11n.fp16.plan

# The engine is built at first start into this volume — a TRT plan is tied to
# the exact GPU, TRT version and build flags, so it cannot be baked in, which is
# also why the build needs no GPU.
VOLUME ["/opt/c3po/engines"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "c3po_vision.detector"]
