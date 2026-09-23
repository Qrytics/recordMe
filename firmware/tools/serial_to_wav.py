#!/usr/bin/env python3
"""Turn a raw serial capture from the step-1 audio bench into a WAV file.

The bench's dump mode (press `d`) writes raw little-endian 16-bit PCM to USB serial,
wrapped in markers so it can be found inside a capture that also contains boot
messages and level lines:

    "RECPCM16" | u32 sample_rate | u16 bits | u8 channels | u8 shift   <- 16-byte header
    ... raw PCM ...
    "ENDPCM16"

Capture and convert:

    cat /dev/tty.usbmodem* > dump.bin          # Ctrl-C to stop
    python3 serial_to_wav.py dump.bin out.wav

Why bother: the level meter tells you whether the gain is right, but only listening
tells you whether it sounds like a voice. Stdlib only, no pyserial.
"""

import argparse
import math
import struct
import sys
import wave

BEGIN = b"RECPCM16"
END = b"ENDPCM16"
HEADER_LEN = 16


def find_segments(blob):
    """Yield (header_dict, pcm_bytes) for every dump found in the capture."""
    pos = 0
    while True:
        start = blob.find(BEGIN, pos)
        if start < 0:
            return
        head = blob[start : start + HEADER_LEN]
        if len(head) < HEADER_LEN:
            return
        rate, bits, channels, shift = struct.unpack("<IHBB", head[8:HEADER_LEN])
        body_start = start + HEADER_LEN
        stop = blob.find(END, body_start)
        # No end marker means the capture was cut short mid-dump; keep what we have.
        body_end = stop if stop >= 0 else len(blob)
        pos = (stop + len(END)) if stop >= 0 else len(blob)
        yield (
            {"rate": rate, "bits": bits, "channels": channels, "shift": shift,
             "truncated": stop < 0},
            blob[body_start:body_end],
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="captured byte stream, or - for stdin")
    ap.add_argument("output", nargs="?", default="out.wav", help="WAV to write")
    ap.add_argument("--segment", type=int, default=1,
                    help="which dump to convert when the capture holds several (1-based)")
    args = ap.parse_args()

    if args.input == "-":
        blob = sys.stdin.buffer.read()
    else:
        with open(args.input, "rb") as fh:
            blob = fh.read()

    segments = list(find_segments(blob))
    if not segments:
        sys.exit(
            "no 'RECPCM16' marker in {} ({} bytes).\n"
            "Did the bench actually enter dump mode (press 'd'), and was the capture "
            "raw rather than a line-buffered log?".format(args.input, len(blob))
        )

    if not 1 <= args.segment <= len(segments):
        sys.exit("--segment {} out of range: capture holds {} dump(s)".format(
            args.segment, len(segments)))

    meta, pcm = segments[args.segment - 1]
    if meta["bits"] != 16:
        sys.exit("unexpected bit depth {} in header".format(meta["bits"]))

    # An odd byte count means the capture was cut mid-sample; drop the stray byte
    # rather than shifting every following sample by one byte into noise.
    if len(pcm) % 2:
        pcm = pcm[:-1]

    with wave.open(args.output, "wb") as wf:
        wf.setnchannels(meta["channels"] or 1)
        wf.setsampwidth(2)
        wf.setframerate(meta["rate"])
        wf.writeframes(pcm)

    frames = len(pcm) // 2 // (meta["channels"] or 1)
    seconds = frames / float(meta["rate"] or 1)
    print("wrote {}: {:.2f} s, {} Hz, {} ch, captured at SAMPLE_SHIFT={}".format(
        args.output, seconds, meta["rate"], meta["channels"], meta["shift"]))
    if meta["truncated"]:
        print("note: no end marker — capture stopped mid-dump, file may end abruptly")
    if len(segments) > 1:
        print("note: capture holds {} dumps; converted #{} (see --segment)".format(
            len(segments), args.segment))

    # Peak/RMS here is a sanity check on the capture, not a substitute for the bench
    # meter: a file that peaks at full scale was clipping, one that peaks near zero
    # means SAMPLE_SHIFT is too high.
    if frames:
        samples = struct.unpack("<{}h".format(len(pcm) // 2), pcm)
        peak = max(max(samples), -min(samples))
        rms = (sum(s * s for s in samples) / float(len(samples))) ** 0.5
        dbfs = 20 * math.log10(peak / 32768.0) if peak else float("-inf")
        print("peak={} ({:.1f} dBFS) rms={:.0f}".format(peak, dbfs, rms))
        if peak >= 32767:
            print("note: peak is at full scale — clipped. Raise SAMPLE_SHIFT.")


if __name__ == "__main__":
    main()
