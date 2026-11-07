# Test day: perception (PLAN Phase 2) — YOLOE-26 on the robot

What this session must answer, in order: **does YOLOE-26 build and run on the Orin
(TensorRT 8.5)**, **how fast**, and **how much more does it see than YOLO11n on our own
objects at 0.5 / 1.5 / 3 m**. Everything below is on branch `feat/autonomy`; the
tools are in `apps/perception/tools/`. Rollback (§8) is one env var unset.

What is already verified on a Mac (2026-11-02, ultralytics 8.4.198): the exports, their
shapes and ops, the detector's decoding and masks on real ONNX Runtime outputs, the
grounding/plane/tracker maths, and both tools end to end. On the robot (✅ 2026-11-03): the
engine built on the Orin and runs live at ~40 ms/tick. Which export ran (NMS-free or raw head)
is not recorded **[?]**. The camera pitch (§4) and the test set (§5–7) are still open.

| export (all opset 16, input `[1,3,640,640]`)   | outputs                                | classes |
| ---------------------------------------------- | -------------------------------------- | ------- |
| `yoloe-26s-seg.onnx` (default, NMS-free)       | `[1,300,38]` + protos `[1,32,160,160]` | 1,223   |
| `yoloe-26s-seg-pf.onnx` (prompt-free)          | `[1,300,38]` + protos                  | 4,585   |
| `yoloe-26s-seg.onnx --raw-head` (fallback, §3) | `[1,1259,8400]` + protos               | 1,223   |
| `yolo11n.onnx` (today's, the baseline)         | `[1,84,8400]`                          | 80      |

CPU decode on an M3, real outputs: NMS-free 0.2 ms; raw head 10 ms (and 42 MB/frame off
the GPU). The Orin's A78 cores are slower — expect roughly 2-4x.

---

## 0. Before leaving the lab laptop (10 min)

```bash
git checkout feat/autonomy
cd apps/perception && uv run pytest -q && cd -        # 0 failed
T=apps/perception/tools
uv run --with "ultralytics>=8.4" --with onnx python $T/export_yoloe_onnx.py yoloe-26s-seg.pt out/
uv run --with "ultralytics>=8.4" --with onnx python $T/export_yoloe_onnx.py yoloe-26s-seg-pf.pt out/
uv run --with "ultralytics>=8.4" --with onnx python $T/export_yoloe_onnx.py yoloe-26s-seg.pt out-raw/ --raw-head
```

Each run must end with `opsets: ai.onnx@16` and the outputs in the table above (the
first one spends ~1 min in `set_classes`; weights + a 250 MB text encoder download on
first use). Copy to the robot, **into the same models dir as YOLO11n** — labels are
`<stem>.labels.txt`, so nothing of YOLO11n's is overwritten:

```bash
rsync -P out/yoloe-26s-seg.onnx out/yoloe-26s-seg.labels.txt \
         out/yoloe-26s-seg-pf.onnx out/yoloe-26s-seg-pf.labels.txt c3po:~/.c3po/models/
rsync -P out-raw/yoloe-26s-seg.onnx c3po:~/.c3po/models/yoloe-26s-seg-raw.onnx
```

(`rsync -P` rather than scp: the README notes big single scp's to this robot stall
silently; `-P` resumes. The raw-head file is renamed so it cannot shadow the default.)

## 1. Get the code onto the robot and rebuild the vision image (~25 min)

`detector.py`, `grounding.py` and the new `tracking.py` are COPIED into the image, so the
image must be rebuilt — the new engine alone is not enough.

Deploy as in `docs/OPERATIONS.md` §5 (push from the Mac, `git merge --ff-only incoming` on
the robot). The build needs internet: run `scripts/robot_internet.py` on the Mac and export
the proxy in the robot shell first (`docs/OPERATIONS.md` §2; `bringup/build.py` then adds
`--network host` and the build-args, 9a6063d).

```bash
export http_proxy=http://127.0.0.1:18899 https_proxy=http://127.0.0.1:18899 no_proxy=localhost,127.0.0.1,::1
c3po perception build vision
```

No Dockerfile change: inference needs nothing new (numpy, pycuda, TensorRT already
there). The export runs on the laptop (§0) and the plan is built by the image's own
`trtexec` (§2) — ultralytics never goes on the robot.

## 2. Build the YOLOE engine ON the Orin (5-15 min, first time)

The three keys below are read by `build_perception`, by `perception_up` (passed into
the container — `bringup/spec.py: VISION_ENV_PASSTHROUGH`) and by the detector. Put them
once in `~/.c3po/perception.env`, which both commands read on every run, so a new ssh
session or a reboot keeps the model (a shell `export` of the same key still wins, for a
one-off). If the robot still has an old `~/.c3po/yoloe.env`, merge its lines into
`perception.env` once and delete it: only `perception.env` is read.

```bash
cat >> ~/.c3po/perception.env <<'EOF'
C3PO_ONNX=/opt/c3po/models/yoloe-26s-seg.onnx
C3PO_ENGINE=/opt/c3po/engines/yoloe-26s-seg.fp16.plan
C3PO_LABELS=/opt/c3po/models/yoloe-26s-seg.labels.txt
EOF
c3po perception build engine         # prints "from ~/.c3po/perception.env: C3PO_ENGINE, ..."
```

The file takes `KEY=VALUE` lines (`export` and `#` comments allowed). A line it cannot
read refuses with its line number before anything runs. Only the model, `C3PO_VISION_*`
tuning, `C3PO_CAMERA_*` and `C3PO_LIDAR_SOURCE` keys are taken
(`bringup/spec.py: PERSISTENT_ENV_KEYS`); anything else, `C3PO_TAKE_OVER` for one, is
ignored with a warning.

- **Pass:** `engine written to /opt/c3po/engines/yoloe-26s-seg.fp16.plan`.
- **If trtexec rejects a node** (TopK / ArgMax / GatherElements / Where are the new ones;
  write down the exact error), switch to the raw head and rebuild: in
  `~/.c3po/perception.env` set `C3PO_ONNX=/opt/c3po/models/yoloe-26s-seg-raw.onnx` and
  `C3PO_ENGINE=/opt/c3po/engines/yoloe-26s-seg-raw.fp16.plan`
  — same labels file. Its ops are YOLO11's plus ConvTranspose. The detector decodes it
  too (class-wise CPU NMS), at the CPU cost in the table above.
- Repeat for the prompt-free model with `-pf` in all three names if time allows.

## 3. Speed: GPU, then the whole tick

```bash
c3po perception build bench          # trtexec on $C3PO_ENGINE: note median / p99 GPU ms
```

YOLO11n measured 5-8 ms here. Record YOLOE's next to it (power mode is printed; MAXN or
the numbers are not comparable).

Then the real loop, with the camera (gemm stopped: `C3PO_TAKE_OVER=1` if asked):

```bash
c3po up perception                   # reads ~/.c3po/perception.env (§2)
docker logs -f c3po-perception-vision 2>&1 | grep -E "engine.ready|alive|failed"
```

- `engine.ready ... output=(1, 300, 38) masks=32` — the right plan loaded. YOLO11n shows
  `output=(1, 84, 8400) masks=0`.
- Every 30 s: `alive ... mean_tick_ms=<n>`. **Budget is 100 ms at 10 Hz.** That number is
  grab + inference + decode + masks + grounding, on core 5. If it is over ~80 ms, try
  `C3PO_VISION_HZ=5` before anything else and record both.
- Look at the payload once (the object memory reads it): each object now has
  `track_id` and `depth_source`; a top-level `unranged` list holds things seen without
  depth. Nothing else changed (`v` is still 1).

While it runs, also note `tegrastats` GPU % and RAM — Phase 2 step 8 has never been
measured.

## 4. Quick sanity on the things people ask for (5 min)

With the operator view open, put on a table at ~1 m: TV/monitor, laptop, phone, watch,
wallet, keys, glasses, backpack, mug, charger, headphones, a drinking glass. Ask the
agent what it sees (or read `/c3po/objects`). Expected labels are the vocab names:
`television set`, `computer monitor`, `laptop`, `mobile phone`, `wristwatch`, `wallet`,
`keys`/`key`, `eyeglasses`, `backpack`, `mug`, `phone charger`, `headphones`,
`drinking glass`. The glass should come back with `"depth_source":"plane"` and a range
close to the tape measure — that is the transparent-object fallback working.

**Camera pitch (2 min, do it first).** The URDF mounts the D435 **47.6° nose-down**, and
the vision container's default extrinsic is that CAD mount (x 0.054, y 0.018, z 0.474 from
the pelvis); the start log says `extrinsic_source=URDF d435_joint (CAD)`, or `env` if a
`C3PO_CAMERA_*` override is set. Verify it: put one object on the floor 1.0 m ahead (tape
from the pelvis projection) and read its `range_m`, which should be within about 5 cm. If it
is not, measure the mount and put the numbers in `~/.c3po/perception.env`
(`C3PO_CAMERA_PITCH_DEG=…`, no rebuild), then read it again. Write down both. This changes
every range: person rule, object memory, `find_object`.

## 5. Capture the test set (~50 classes x 0.5 / 1.5 / 3 m, ~10 frames each)

The capture opens the camera itself, so stop the detector first (`c3po perception stop`).
For each distance, place the objects, then:

```bash
mkdir -p ~/c3po-testset
docker run --rm -it --runtime nvidia --entrypoint python3 \
  --device-cgroup-rule 'c 81:* rmw' --device-cgroup-rule 'c 189:* rmw' \
  -v /dev:/dev -v /run/udev:/run/udev:ro \
  -v ~/c3po/apps/perception/tools:/opt/c3po/tools:ro -v ~/c3po-testset:/data \
  c3po/perception-vision:r35.3.1 /opt/c3po/tools/capture_frames.py /data/raw \
  --prefix d1.5 --n 10 --enter
```

`--prefix d0.5 | d1.5 | d3` = where you put the objects; `--enter` waits for you between
frames so you can move things. Each frame is `d1.5_NNN.jpg` + `d1.5_NNN.depth.png`
(aligned, 16-bit) + one `meta.json`. If the camera is busy, `--url
http://127.0.0.1:8081/frame.jpg` grabs colour-only frames from the running detector
instead (band then comes from the prefix).

**Check the camera extrinsic while the tape is out** (height of the lens above the
pelvis, forward offset, pitch) against the CAD default above. Only if it differs, apply
without a rebuild: `C3PO_CAMERA_Z_M=… C3PO_CAMERA_X_M=… C3PO_CAMERA_PITCH_DEG=…` in
`~/.c3po/perception.env` before `c3po up`.

## 6. Label it

Copy `~/c3po-testset/raw` to a laptop and box every object, **using the vocab names**
(`apps/perception/vision/vocab.txt`) as class names — e.g. `television set`, not `tv`.
Either export YOLO format (`<stem>.txt` + one `classes.txt` in the folder; Label Studio
and Roboflow both do this) or write `<stem>.json`:
`{"objects": [{"label": "wallet", "box": [x0, y0, x1, y1]}]}` in pixels. Put the labels
next to the `.jpg`/`.depth.png` files and copy the folder back as `~/c3po-testset/labelled`.

## 7. Recall: YOLO11n (baseline) vs YOLOE vs YOLOE-pf

On the robot, through the exact production decode (vision container stopped is not
required — this does not touch the camera):

```bash
run_eval() {  # $1 = engine, $2 = labels
  docker run --rm --runtime nvidia --entrypoint python3 \
    -e C3PO_ENGINE=$1 -e C3PO_LABELS=$2 \
    -v c3po-trt-engines:/opt/c3po/engines -v ~/.c3po/models:/opt/c3po/models:ro \
    -v ~/c3po/apps/perception/tools:/opt/c3po/tools:ro -v ~/c3po-testset:/data \
    c3po/perception-vision:r35.3.1 /opt/c3po/tools/eval_recall.py /data/labelled --json /data/$3.json
}
run_eval /opt/c3po/engines/yolo11n.fp16.plan       /opt/c3po/models/labels.txt                yolo11n
run_eval /opt/c3po/engines/yoloe-26s-seg.fp16.plan /opt/c3po/models/yoloe-26s-seg.labels.txt  yoloe
run_eval /opt/c3po/engines/yoloe-26s-seg-pf.fp16.plan /opt/c3po/models/yoloe-26s-seg-pf.labels.txt yoloe-pf
```

Each prints a class x band table of hits and the **mean per-class recall per band** — the
Phase 2 exit metric (>= 75 % at <= 1.5 m, >= 50 % at 3 m). COCO names are mapped to the
vocab names for the baseline (`tv` counts for `television set`, etc.;
`DEFAULT_ALIASES` in the script, `--aliases file.json` to add). Paste the three tables
into `docs/autonomy/research/02-perception.md` §10 and add every class YOLOE still misses
to the extras at the end of `vocab.txt` (then re-export, §0). `--conf 0.25` is worth one
extra run: the NMS-free head's scores sit lower than YOLO11n's.

Same thing on a laptop without TensorRT:
`uv run --with "ultralytics>=8.4" python apps/perception/tools/eval_recall.py <folder> --backend ultralytics --model out/yoloe-26s-seg.onnx`.

## 8. Rollback to YOLO11n (seconds)

```bash
sed -i '/^\(export \)\?C3PO_\(ONNX\|ENGINE\|LABELS\)=/d' ~/.c3po/perception.env
unset C3PO_ONNX C3PO_ENGINE C3PO_LABELS   # a shell export would still win over the file
c3po down && c3po up perception
docker logs c3po-perception-vision 2>&1 | grep engine.ready     # output=(1, 84, 8400)
```

YOLO11n's ONNX, labels and plan were never touched (different file names, same volume).
The new code runs YOLO11n exactly as before plus `track_id`/`depth_source`; to roll the
code back too, check out the previous commit and `c3po perception build vision`.

## What to write down

1. trtexec result for the NMS-free export (pass / exact error) — the main unknown.
2. GPU median/p99 (bench) and `mean_tick_ms` for YOLO11n and YOLOE, with power mode.
3. The three recall tables.
4. The measured camera extrinsic.
5. 3D error at 1.5 m for one opaque object, the glass and the monitor (tape vs `range_m`).
