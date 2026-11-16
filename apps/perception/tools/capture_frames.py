#!/usr/bin/env python3
"""Save N colour (+ aligned depth) frames from the robot's D435i into a folder.

TWO SOURCES, BECAUSE THE CAMERA HAS ONE OWNER
---------------------------------------------
--realsense (default): opens the D435i itself, colour + depth ALIGNED TO
    COLOUR, exactly as the detector does (it reuses detector.RealSenseSource).
    The detector container must be STOPPED first — /dev/videoN has one owner.
    Runs inside the vision image, which already has pyrealsense2/numpy/Pillow:

        docker run --rm -it --runtime nvidia --entrypoint python3 \\
          --device-cgroup-rule 'c 81:* rmw' --device-cgroup-rule 'c 189:* rmw' \\
          -v /dev:/dev -v /run/udev:/run/udev:ro \\
          -v ~/c3po/apps/perception/tools:/opt/c3po/tools:ro -v ~/c3po-testset:/data \\
          c3po/perception-vision:r35.3.1 /opt/c3po/tools/capture_frames.py /data/raw --prefix d1.5

--url http://127.0.0.1:8081/frame.jpg: colour only, from the running
    detector's MJPEG server (stream.py). No depth, so eval_recall.py takes the
    distance band from the filename prefix instead.

OUTPUT, per frame: <prefix>_<NNN>.jpg, and with --realsense <prefix>_<NNN>.depth.png
(16-bit, raw sensor units). meta.json holds intrinsics + depth_scale.
`--prefix d0.5|d1.5|d3` names the distance you placed the objects at; the eval
uses it when a frame has no depth. Frames continue numbering if the folder
already has some, so several runs can share one folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

# In the repo, c3po_vision lives at ../vision; in the image it is on PYTHONPATH.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision"))


def next_index(out_dir: str, prefix: str) -> int:
    taken = [f for f in os.listdir(out_dir) if f.startswith(prefix + "_") and f.endswith(".jpg")]
    return len(taken)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Save colour (+ depth) frames for the eval set.")
    ap.add_argument("out_dir")
    ap.add_argument("--n", type=int, default=10, help="frames to save (default 10)")
    ap.add_argument("--prefix", default="f", help="e.g. d0.5 / d1.5 / d3 — the placement distance")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--enter", action="store_true", help="wait for Enter before each frame")
    ap.add_argument("--url", default=None, help="MJPEG /frame.jpg URL instead of the camera")
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    start = next_index(args.out_dir, args.prefix)

    source = None
    if args.url is None:
        from c3po_vision.detector import RealSenseSource

        source = RealSenseSource()
        source.start()
        for _ in range(30):  # let auto-exposure settle; the first frames are dark
            source.read()
        intr = source.intrinsics
        with open(os.path.join(args.out_dir, "meta.json"), "w") as fh:
            json.dump({"depth_scale": source.depth_scale, "intrinsics": intr.__dict__}, fh, indent=1)

    from PIL import Image

    try:
        for i in range(start, start + args.n):
            if args.enter:
                input(f"[{i - start + 1}/{args.n}] Enter to capture ")
            else:
                time.sleep(args.interval)
            stem = os.path.join(args.out_dir, f"{args.prefix}_{i:03d}")
            if source is None:
                with urllib.request.urlopen(args.url, timeout=5) as resp:
                    data = resp.read()
                with open(stem + ".jpg", "wb") as fh:
                    fh.write(data)
            else:
                import numpy as np

                color, depth = source.read()
                Image.fromarray(np.ascontiguousarray(color[:, :, ::-1])).save(stem + ".jpg", quality=95)
                Image.fromarray(depth.astype(np.uint16)).save(stem + ".depth.png")
            print(f"saved {stem}.jpg")
    finally:
        if source is not None:
            source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
