#!/usr/bin/env python3
"""Talk to the robot. Live mic -> Spanish transcript, printed as you speak.

    ssh c3po
    cd ~/c3po/apps/bridge
    SIM_MODE=real uv run python scripts/listen_live.py

Ctrl-C to stop. Optional: `--seconds 60` to auto-exit.

IF NOTHING APPEARS, THAT IS THE KNOWN PROBLEM, NOT A CRASH. The mic is a UDP
multicast group published by the control board, and it does not stream while the
robot is idle (docs/ROBOT-HARDWARE.md §8.2). This script says so explicitly
rather than sitting silent, because a silent terminal is indistinguishable from
"nobody is talking". Hold **L1+L2 on the remote** and speak — that is the one
untested hypothesis for opening the feed.

It prints packet counts either way, so you can always tell which of the three
situations you are in: no audio arriving, audio arriving but not decoding, or
decoding fine and you simply have not said anything yet.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after N seconds (default: run until Ctrl-C)")
    ap.add_argument("--quiet-hint-after", type=float, default=6.0,
                    help="say something if no audio has arrived by then")
    args = ap.parse_args()

    from bridge.skills import listen

    ok, why = listen.available()
    if not ok:
        print(f"FATAL: {why}", file=sys.stderr)
        return 1

    print("loading the Spanish model ...", flush=True)
    detector = listen.StopPhraseDetector()
    transcriber = listen.Transcriber()
    print(f"stop phrases: {', '.join(detector.phrases)}")
    print(f"joining {listen.MIC_GROUP}:{listen.MIC_PORT} ...", flush=True)

    frames = listen.iter_mic_frames(timeout_s=0.5)
    started = time.time()
    packets = audio_bytes = 0
    hinted = False

    print("\nlistening — say something in Spanish. Ctrl-C to stop.\n", flush=True)
    try:
        for frame in frames:
            packets += 1
            audio_bytes += len(frame)

            if hit := detector.feed(frame):
                print(f"  *** STOP HEARD: {hit!r} ***", flush=True)
                detector.reset()

            if text := transcriber.feed(frame):
                print(f"  heard: {text}", flush=True)

            now = time.time()
            if args.seconds and now - started > args.seconds:
                break
    except KeyboardInterrupt:
        print("\n(interrupted)")
    finally:
        # Whatever is mid-utterance when we stop. Without this the last thing
        # said is silently dropped — Vosk only finalises on trailing silence.
        if tail := transcriber.flush():
            print(f"  heard: {tail}", flush=True)

    elapsed = time.time() - started
    print()
    print(f"packets: {packets}   audio: {audio_bytes / 32000:.1f} s "
          f"over {elapsed:.0f} s wall clock")
    if packets == 0:
        print()
        print("NO AUDIO ARRIVED. The multicast join succeeded — this is the feed")
        print("not streaming, which is the documented state at rest. Try again")
        print("while holding L1+L2 on the remote; if packets stay at 0 with the")
        print("combo held, wake-up mode is not the gate and §8.2 needs updating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
