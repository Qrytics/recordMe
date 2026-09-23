#!/usr/bin/env python3
"""Benchmark faster-whisper on the machine it will actually run on.

Why this exists: docs/PLAN_REVIEW.md section 3.1 estimates that a Pi 5 clears a day
of wear comfortably, and picks small.en with large-v3-turbo as a possible upgrade.
Those are estimates. This turns them into measurements, and it can still invalidate
the model choice — which is why CLAUDE.md section 11 says to run it during step 1-2
rather than leaving it to step 4.

Run it on the Pi, not on a dev machine:

    python3 bench_whisper.py --audio sample.wav
    python3 bench_whisper.py --audio sample.wav --models small.en,large-v3-turbo

Getting a sample with real speech in it (Whisper on a sine tone measures nothing):

    # on the Pi, if a USB mic is attached:
    arecord -f S16_LE -r 16000 -c 1 -d 60 sample.wav
    # or generate one on a Mac and copy it over:
    say -o sample.wav --data-format=LEI16@16000 "$(cat some-paragraph.txt)"

A minute or more of continuous speech gives a far more stable number than a
three-second clip, where model load and warm-up dominate.
"""

import argparse
import json
import platform
import sys
import time
import wave
from pathlib import Path

# A day of wear is ~50-100 min of actual speech after VAD (PLAN_REVIEW.md 3.1).
# We project against the pessimistic end.
DAILY_SPEECH_MINUTES = 100


def audio_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            print(
                "  note: sample is {} Hz, {} ch, {}-bit — the device sends 16000 Hz "
                "mono 16-bit, so timings here may not transfer exactly".format(
                    w.getframerate(), w.getnchannels(), w.getsampwidth() * 8
                )
            )
        return w.getnframes() / float(w.getframerate())


def bench_one(model_name, audio, compute, threads, vad, runs):
    from faster_whisper import WhisperModel

    duration = audio_duration_s(audio)

    # Load time is reported separately because production loads the model once at
    # worker startup and then keeps it — it is not a per-chunk cost.
    t0 = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type=compute, cpu_threads=threads)
    load_s = time.monotonic() - t0

    timings = []
    text = ""
    speech_s = None
    for i in range(runs):
        t0 = time.monotonic()
        segments, info = model.transcribe(
            str(audio), vad_filter=vad, word_timestamps=True, beam_size=5
        )
        out = [s.text.strip() for s in segments]  # generator: this runs the model
        timings.append(time.monotonic() - t0)
        if i == 0:
            text = " ".join(t for t in out if t)
            speech_s = getattr(info, "duration_after_vad", None)

    best = min(timings)
    rtf = duration / best if best else float("inf")
    daily_min = (DAILY_SPEECH_MINUTES / rtf) if rtf else float("inf")

    return {
        "model": model_name,
        "compute": compute,
        "threads": threads,
        "vad": vad,
        "audio_s": round(duration, 2),
        "speech_s_after_vad": round(speech_s, 2) if speech_s is not None else None,
        "load_s": round(load_s, 2),
        "transcribe_s": [round(t, 2) for t in timings],
        "best_s": round(best, 2),
        "realtime_factor": round(rtf, 2),
        "minutes_to_clear_a_day": round(daily_min, 1),
        "text_head": text[:160],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", required=True, type=Path, help="16 kHz mono WAV with real speech")
    ap.add_argument("--models", default="small.en", help="comma-separated (e.g. small.en,large-v3-turbo)")
    ap.add_argument("--compute", default="int8", help="int8 (default), int8_float32, float32")
    ap.add_argument("--threads", type=int, default=4, help="cpu_threads; the Pi 5 has 4 cores")
    ap.add_argument("--runs", type=int, default=2, help="repeats; the best is reported")
    ap.add_argument("--no-vad", action="store_true", help="measure without the Silero VAD stage")
    ap.add_argument("--json", type=Path, help="also write raw results here")
    args = ap.parse_args()

    if not args.audio.exists():
        sys.exit("audio file not found: {}".format(args.audio))

    print("host     : {} {} / Python {}".format(
        platform.system(), platform.machine(), platform.python_version()))
    try:
        import ctranslate2

        # On the Pi this line is the aarch64-wheel check from PLAN_REVIEW.md 3.2:
        # if the import fails there, whisper.cpp is the documented fallback.
        print("ctranslate2: {} (imported OK for {})".format(
            ctranslate2.__version__, platform.machine()))
    except ImportError:
        sys.exit("ctranslate2 missing — fall back to whisper.cpp (see PLAN_REVIEW.md 3.2)")

    print("audio    : {} ({:.1f}s)".format(args.audio, audio_duration_s(args.audio)))
    print("projection assumes {} min of speech per day of wear\n".format(DAILY_SPEECH_MINUTES))

    results = []
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        print("--- {} ---".format(name))
        try:
            r = bench_one(name, args.audio, args.compute, args.threads, not args.no_vad, args.runs)
        except Exception as exc:  # noqa: BLE001 - a failed model shouldn't abort the rest
            print("  FAILED: {}: {}\n".format(type(exc).__name__, exc))
            continue
        results.append(r)
        print("  load          : {:.2f}s (once at worker startup, not per chunk)".format(r["load_s"]))
        print("  transcribe    : {:.2f}s best of {}".format(r["best_s"], args.runs))
        print("  realtime      : {:.2f}x {}".format(
            r["realtime_factor"],
            "(faster than realtime)" if r["realtime_factor"] >= 1 else "(SLOWER than realtime)"))
        print("  a day of wear : ~{:.0f} min of compute".format(r["minutes_to_clear_a_day"]))
        print("  text          : {}\n".format(r["text_head"]))

    if results:
        print("=== summary ===")
        print("{:<20} {:>8} {:>10} {:>14}".format("model", "best(s)", "realtime", "min/day"))
        for r in results:
            print("{:<20} {:>8.2f} {:>9.2f}x {:>14.0f}".format(
                r["model"], r["best_s"], r["realtime_factor"], r["minutes_to_clear_a_day"]))
        print("\nPaste these into BUILD_LOG.md with the model and the host.")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print("wrote {}".format(args.json))


if __name__ == "__main__":
    main()
