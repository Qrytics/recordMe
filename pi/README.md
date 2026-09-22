# Pi server

Runs on the Raspberry Pi 5 (16 GB, active cooler, 2 TB USB HDD). **Scaffold only so far** —
structure, config, and schema are in place; the implementations aren't.

## Services

| Service | Binds to | Auth | Reachable from |
|---|---|---|---|
| `receiver` | LAN address, :8000 | shared-secret bearer token | home Wi-Fi only |
| `transcribe` | — (worker, no socket) | — | — |
| `search` | localhost / tunnel, :8001 | tunnel + password | anywhere, via Tailscale |
| `ollama` | localhost, :11434 | — | via `search` |

Those bind addresses are the design, not an accident: **the device never uses the tunnel.** It
uploads over the LAN only, which keeps TLS and VPN complexity off the ESP32 entirely. Only the
search interface is remotely reachable. **Never port-forward any of these to the internet** — the
tunnel is the access path. See `docs/PLAN_REVIEW.md` §9.

## Layout

```
recordme/
  config.py       .env loading
  db.py           SQLite access, gap detection, FTS search
  receiver.py     FastAPI upload endpoint (LAN-only)
  transcribe.py   worker: pending chunks -> VAD -> Whisper -> DB
  search.py       FastAPI keyword search + LLM Q&A (tunnel-facing)
schema.sql        tables, FTS5 index, triggers
.env.example      copy to .env and fill in
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill it in
sqlite3 "$RECORDME_DB_PATH" < schema.sql
```

Run the receiver and search app with uvicorn, and `transcribe` as a plain worker. All three want
systemd units with `Restart=always` once they work.

## Storage split

**DB on the SD card, audio on the HDD.** The DB is only ~55 MB/year, and keeping it and its FTS
index off a spinning USB disk keeps search latency low. Audio is bulky and written sequentially,
which is what an HDD is good at.

The 2 TB drive holds **~6 years** of raw WAV even with no VAD at all, so there is no retention
policy and no Opus transcode — keep the WAV. Just monitor free space.

Two caveats: the drive will stay spun up all day since uploads arrive every few minutes (expected,
not a problem), and one drive means no redundancy. Audio is replaceable-ish; the transcripts are
not. A nightly `sqlite3 .backup` elsewhere is cheap insurance.

## Transcription

**faster-whisper**, `small.en`, int8. Picked over whisper.cpp because the Pi 5 makes the Python
stack a non-issue and faster-whisper bundles Silero VAD plus word timestamps — see
`docs/PLAN_REVIEW.md` §3.2. If CTranslate2 has no aarch64 wheel for this Pi's Python version, fall
back to whisper.cpp.

Benchmark before building anything else on top: time `small.en` and `large-v3-turbo` against a real
sample. A day of wear is only ~50–100 min of actual speech after VAD, so there's room to spend on
accuracy — but confirm that with numbers, and log them in `BUILD_LOG.md`.

VAD is load-bearing, not an optimization. Besides the time saved, it stops Whisper hallucinating
text over silence, which matters a lot when most of the audio has no speech in it.

## Search

Keyword search first, and make it good — retrieval does the real work. The LLM layer is
retrieval-then-answer: parse any time expression, FTS5 for candidates, pass only those rows to
Ollama with their timestamps, return the answer *with* its sources. Never stuff a whole day of
transcripts into the context window; an 8B model on a Pi runs at single-digit tokens per second.
