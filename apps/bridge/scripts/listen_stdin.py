#!/usr/bin/env python3
"""Transcribe 16 kHz mono PCM arriving on stdin. The mic-agnostic entry point.

The robot's own microphone is a multicast feed that does not stream at rest
(docs/ROBOT-HARDWARE.md §8.2), so this exists to make the rest of the pipeline
usable while that is unresolved: pipe audio in from anywhere and the same
recogniser, stop detector and transcripts run unchanged.

Drive it from a laptop microphone:

    ffmpeg -hide_banner -loglevel error -f avfoundation -i ":0" \\
        -ar 16000 -ac 1 -f s16le - \\
      | ssh c3po 'cd ~/c3po/apps/bridge && SIM_MODE=real \\
            ~/.local/bin/uv run python scripts/listen_stdin.py'

(`-f avfoundation -i ":0"` is macOS; on Linux use `-f alsa -i default`.)

THE FORMAT IS NOT NEGOTIABLE: 16 kHz, mono, signed 16-bit little-endian. It is
what Vosk is constructed with and what the robot's own feed carries, so the two
sources stay interchangeable. Feeding 44.1 kHz here does not error — it decodes
as gibberish, or as nothing, which is a far more confusing failure than a crash.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

FRAME_BYTES = 4000          # 0.125 s


def main() -> int:
    from bridge.skills import listen

    ok, why = listen.available()
    if not ok:
        print(f"FATAL: {why}", file=sys.stderr)
        return 1

    print("loading the Spanish model ...", file=sys.stderr, flush=True)
    detector = listen.StopPhraseDetector()
    transcriber = listen.Transcriber()
    print(f"stop phrases: {', '.join(detector.phrases)}", file=sys.stderr, flush=True)
    print("listening on stdin — speak. Ctrl-C to stop.\n", file=sys.stderr, flush=True)

    stream = sys.stdin.buffer
    total = 0
    started = time.time()
    try:
        while True:
            # read() rather than read1(): a short read at a frame boundary would
            # desynchronise the 16-bit samples and every later frame would decode
            # as noise. Vosk wants whole samples, not whatever arrived.
            frame = stream.read(FRAME_BYTES)
            if not frame:
                break
            total += len(frame)

            if hit := detector.feed(frame):
                print(f"  *** STOP HEARD: {hit!r} ***", flush=True)
                detector.reset()

            if text := transcriber.feed(frame):
                print(f"  heard: {text}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        # Vosk finalises only on trailing silence, so without this the last
        # thing said is dropped — and when you are testing by speaking one
        # sentence, that is usually the ONLY thing you said.
        if tail := transcriber.flush():
            print(f"  heard: {tail}", flush=True)

    secs = total / 32000
    print(f"\n{secs:.1f} s of audio in {time.time() - started:.0f} s wall clock",
          file=sys.stderr)
    if total == 0:
        print("NO AUDIO ARRIVED ON STDIN — check the ffmpeg side is running and "
              "that your terminal has microphone permission.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
