# Pi server

Runs on the Raspberry Pi 5 (16 GB, active cooler, 2 TB USB HDD). **Implemented** — receiver,
transcription worker, and keyword search all work and are covered by tests. LLM Q&A (`/ask`) is
still build step 7 and returns 501.

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
bench_whisper.py  measure a model on the host it will run on
tests/            pytest suite; uses a fake Whisper model, no ML deps needed
.env.example      copy to .env and fill in
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill it in
```

`schema.sql` is applied automatically by `db.connect()` on first use, so there's no manual
`sqlite3 < schema.sql` step.

## Running

```bash
uvicorn recordme.receiver:app --host 0.0.0.0 --port 8000   # LAN only
uvicorn recordme.search:app   --host 127.0.0.1 --port 8001 # tunnel + localhost only
python -m recordme.transcribe                              # worker, no socket
```

Those two bind addresses are the security model, not a default — see the table above. All three
want systemd units with `Restart=always`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q          # 121 tests, ~1 s, no hardware and no Whisper model required
```

The suite injects a fake Whisper model, so it runs anywhere. It leans on the guarantees that
actually matter: replay idempotency, gap detection *and* healing, the atomic claim, crash
recovery, and that one poison chunk can't wedge the queue.

## Benchmark

Model choice is still an estimate until this runs on the Pi (`docs/PLAN_REVIEW.md` §3.1). It needs
no hardware and nothing else from the build — only the Pi itself.

Paste-ready, from scratch on the Pi (`qrytics@marioServer`):

```bash
git clone <this repo> recordme && cd recordme/pi     # or git pull
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                       # watch for the CTranslate2 wheel

# A WAV with real speech in it — a tone measures nothing. Either record one:
arecord -f S16_LE -r 16000 -c 1 -d 60 sample.wav
# ...or generate one on a Mac and scp it over:
#   say -o sample.wav --data-format=LEI16@16000 -f some-paragraph.txt

python3 bench_whisper.py --audio sample.wav \
  --models small.en,large-v3-turbo --threads 4 --json bench.json
```

A minute or more of continuous speech matters — on a three-second clip, warm-up dominates and the
realtime figure is meaningless.

What the output decides:

- **`small.en` at comfortably over 1× realtime** → the plan stands as written.
- **`large-v3-turbo` also over ~0.5×** → it clears a day's speech overnight, so it's a live
  accuracy upgrade (`PLAN_REVIEW.md` §3.1) rather than a theoretical one.
- **CTranslate2 fails to import** → no aarch64 wheel for that Python; fall back to whisper.cpp
  (`PLAN_REVIEW.md` §3.2). The script reports this explicitly instead of failing obscurely.

Log the real numbers in `BUILD_LOG.md` — the comparison to beat is `~13 min/day at 7.59×` from
tiny.en on a dev Mac, which is a script smoke test, not a result.

## Typing note

Annotations avoid `X | None` in favour of `Optional[...]`. The Pi's Python 3.11 is fine either
way, but 3.9 dev machines evaluate annotations at definition time and fail to import — and
`from __future__ import annotations` does not help FastAPI routes, which resolve them via
`get_type_hints`.

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
