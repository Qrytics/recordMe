# recordMe

A tiny wearable voice recorder plus a self-hosted transcription and search pipeline.

The device is a clip-on box whose entire interface is one switch: flip it on and it records, flip it
off and it stops. No screen, no buttons, no companion app. It bursts its audio over home Wi-Fi to a
Raspberry Pi 5, which transcribes everything locally with Whisper, stores timestamped transcripts in
SQLite, and serves keyword search plus a local-LLM Q&A layer so you can ask things like "what did I
say about X on Tuesday."

**Everything stays on hardware I own.** No cloud services, no third-party APIs, no accounts — the
audio and transcripts live only on the Pi. The search interface is reachable from outside the house
over a private tunnel, but the data itself never leaves my control.

## Two boxes

| | |
|---|---|
| **Wearable** | XIAO ESP32-S3, INMP441 I2S mic, 1200 mAh LiPo, one switch. ~58 × 38 × 13 mm, ~8 h per charge |
| **Server** | Raspberry Pi 5 (16 GB, 2 TB HDD) on the home network — receiver, Whisper, SQLite + FTS, Ollama |

## Layout

```
firmware/     device code (Arduino/ESP32)
pi/           receiver, transcription worker, DB, search + LLM interface
hardware/     wiring, pinout, power measurements, enclosure and form factor
docs/         plan review and longer-form notes
BUILD_LOG.md  running log of what was tried, measured, and decided
```

## Status

Pre-build. The plan has been reviewed for feasibility and has no open blockers — see
[`docs/PLAN_REVIEW.md`](docs/PLAN_REVIEW.md) for the assessment and
[`hardware/FORM_FACTOR.md`](hardware/FORM_FACTOR.md) for the compact layout and mounting analysis.
[`CLAUDE.md`](CLAUDE.md) is the working context: requirements, decided technical choices, and
gotchas.

## Note

This records everyone within earshot, not just the wearer. Recording-consent law varies by
jurisdiction — some places require all-party consent.
