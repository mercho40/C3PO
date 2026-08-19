#!/usr/bin/env bash
# Build the TensorRT plan on first run.
#
# A plan is specific to the GPU, the TRT version and the build flags. Baking one
# into the image would mean running CUDA during `docker build`, which would mean
# setting "default-runtime": "nvidia" in /etc/docker/daemon.json and restarting
# dockerd — on a daemon shared with the gemm team, where any `docker compose up`
# that RECREATES their containers would silently pick up the new runtime.
# Building here keeps the build runtime-agnostic and the daemon untouched.
set -euo pipefail

# The TensorRT assertion the Dockerfile cannot make.
#
# `import tensorrt` dlopens libnvdla_compiler.so, which is not in the image and
# not in any deb — it is on the host rootfs and the nvidia container runtime
# puts it here. So it fails under runc and succeeds under --runtime nvidia, and
# `docker build` has no --runtime flag. The build checks that the bindings are
# INSTALLED; this checks that they LOAD, which is the thing that actually has to
# be true before anything else in this container matters.
#
# It is also the earliest, clearest signal that the container was started
# without --runtime nvidia — a mistake whose next symptom would otherwise be
# trtexec or the detector failing several minutes later with something about
# CUDA. perception_up passes the flag; a hand-rolled `docker run` might not.
if ! python3 -c "import tensorrt" 2>/dev/null; then
    echo "FATAL: cannot import tensorrt." >&2
    echo "  Almost always: this container was started WITHOUT --runtime nvidia." >&2
    echo "  libnvdla_compiler.so is injected from the host by that runtime; under" >&2
    echo "  runc it is absent and the import fails. Start via perception_up, or" >&2
    echo "  add --runtime nvidia. The real error follows:" >&2
    python3 -c "import tensorrt" || true
    exit 1
fi

ENGINE="${C3PO_ENGINE:-/opt/c3po/engines/yolo11n.fp16.plan}"
ONNX="${C3PO_ONNX:-/opt/c3po/models/yolo11n.onnx}"

if [ ! -f "$ENGINE" ]; then
    if [ ! -f "$ONNX" ]; then
        echo "no engine at $ENGINE and no ONNX at $ONNX" >&2
        echo "export off-device with opset<=17 — TensorRT 8.5 supports no higher," >&2
        echo "and ultralytics defaults to opset 20, which the parser REJECTS." >&2
        exit 1
    fi
    mkdir -p "$(dirname "$ENGINE")"
    echo "building TensorRT engine (one-off, 2-5 min)..." >&2
    # --memPoolSize is the 8.4+ form and forward-compatible with TRT 10.
    # If this binary rejects it, fall back to the 8.x-only --workspace=2048.
    # Symptom of a mis-sized workspace is the nonsense error
    #   "Dimension 2 has value 2748779069440 which exceeds range of int32_t"
    # — that is the workspace, not the model.
    /usr/src/tensorrt/bin/trtexec \
        --onnx="$ONNX" --saveEngine="$ENGINE" --fp16 \
        --memPoolSize=workspace:2048MiB \
        --timingCacheFile="$(dirname "$ENGINE")/timing.cache" \
        --buildOnly \
      || /usr/src/tensorrt/bin/trtexec \
        --onnx="$ONNX" --saveEngine="$ENGINE" --fp16 --workspace=2048 --buildOnly
    # No --int8: buys ~0.6 ms (4.13 -> 3.49 ms on this class of part) at the cost
    # of a 300+ image calibration set, a cache to keep in sync with the weights,
    # and mAP. No --useDLACore: Orin's DLA falls back to GPU for much of this
    # graph, and TRT 8.5's DLA validation is where the reported Jetson export
    # failures cluster.
    echo "engine written to $ENGINE" >&2
fi

exec "$@"
