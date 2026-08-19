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

# Fail LOUDLY at build time, not at 3am on the robot, if this base ever stops
# shipping the Python bindings. Importing tensorrt does not execute CUDA, which
# is why this whole image builds under the default runc runtime and we never
# have to set default-runtime=nvidia on a daemon shared with the gemm team.
RUN python3 -c "import tensorrt, sys; print('TensorRT', tensorrt.__version__, 'py', sys.version.split()[0])" \
 && test -x /usr/src/tensorrt/bin/trtexec

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
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir \
      "pyrealsense2==2.55.1.6486" \
      "numpy<2" \
      "cyclonedds==0.10.2" \
      "pycuda==2022.2.2"
# cyclonedds 0.10.2 pinned to match apps/bridge exactly. Do NOT drift to 0.7.x —
# that is the Foxy copy on this host and its config schema is incompatible
# (<NetworkInterfaceAddress> vs <Interfaces>).
# pycuda 2022.2.2 is the known-good pin for CUDA 11.4 / py3.8; newer assumes CUDA 12.

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
