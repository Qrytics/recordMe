# Build log

Running record of what was tried, measured, and decided — including dead ends, which are the most
useful entries later. Newest at the top.

Format: date, what was attempted, what actually happened, what it means. Paste real numbers and real
error messages; don't summarize them away.

---

## 2026-09-23 — step 4: Pi pipeline implemented and proven end-to-end

**Goal:** implement the Pi side (receiver → VAD → Whisper → SQLite → keyword search) and prove it
without any bench hardware, since nothing is wired yet.

**Setup:** Dev machine is macOS arm64, Python 3.9.6, SQLite 3.51. `pi/.venv` with
faster-whisper 1.2.1 / CTranslate2 4.8.2. Test speech generated with macOS
`say -o x.wav --data-format=LEI16@16000` — which conveniently emits exactly the project's
16 kHz/16-bit/mono format, so no resampling was needed anywhere in the test path.

**Result:** 121 tests pass. Full chain verified over real HTTP against uvicorn, not just
`TestClient`:

```
upload 2.56 s of speech → 200 {"chunk_id":1,"duplicate":false,...}
worker (real faster-whisper + Silero VAD) → status done
GET /search?q=dentist → "Remember to call the [dentist] about the appointment on Friday."
                         match_offsets_s [0.0], audio_path, segments
GET /search?q=helicopter → count 0
GET /search (no password) → 401
```

Replay/gap behaviour, also over real HTTP:

```
seq 0 → {"chunk_id":1,"duplicate":false,"gaps":[]}
seq 0 again → {"chunk_id":1,"duplicate":true,"gaps":[]}     # same id, one file, one row
seq 2 → {"gaps":[[1,1]]}                                     # gap opened
seq 1 → {"gaps":[]}                                          # gap healed
no token → 401 ; 0 *.part files left behind
```

Local benchmark (tiny.en, int8, 4 threads, 64.3 s of speech, **macOS arm64 — not the Pi**):
7.59× realtime, ~13 min of compute per day of wear. Useful only as a script smoke test; the
number that matters is `small.en` on the Pi and is still unmeasured.

**Findings:**

- **PEP 604 annotations broke the scaffold on Python 3.9.** `sqlite3.Row | None` and
  `str | None` are evaluated at definition time, so `pi/recordme/db.py` wouldn't even import.
  `from __future__ import annotations` does *not* rescue the FastAPI routes — FastAPI resolves
  annotations via `get_type_hints`, which evaluates the string and fails the same way. Switched
  to `Optional[...]`, which works on 3.9 and on the Pi's 3.11. No design change.
- **`check_same_thread=False` is required.** FastAPI runs a sync dependency and a
  `run_in_threadpool` call on *different* worker threads, so a per-request connection
  legitimately crosses threads. Safe because nothing shares a connection between concurrent
  requests.
- **Real faster-whisper API matched what was coded against**, verified rather than assumed:
  `model.transcribe(...)` returns a *generator* plus info, and `info.duration_after_vad` does
  exist in 1.2.1 (the code still falls back to summing segment spans for older builds).
- **Dead end / bug caught by tests:** `segments_for()` returns columns `start_s`/`end_s`, but
  `search.py` read `s["start"]`. Ten tests failed with `IndexError: No item with that key`.
  Worth noting because SQLite raises this at row-access time, not query time — silent until hit.
- **`gaps` needed to be self-healing, not append-only.** A missing seq is usually just a retry
  still in flight, so `detect_gaps` recomputes and *deletes* filled ranges. It also anchors at
  seq 0 rather than `min(seq)` — the firmware resets `chunk_seq` per boot, so anchoring at
  `min(seq)` would have hidden a lost *first* chunk entirely.
- **Added `requeue_stale_transcribing()`** (not in the original scaffold contract). A worker
  killed mid-chunk leaves its claim behind and nothing would ever pick that chunk up again —
  audio never transcribed, and silently so, which the spec forbids.
- **Cosmetic, non-blocking:** faster-whisper 1.2.1 emits numpy `divide by zero`/`overflow`
  RuntimeWarnings from `feature_extractor.py` matmul on this Mac. Output was correct regardless.
  Watch whether it recurs on the Pi; likely a numpy-version interaction.

**Decided:** `/ask` returns 501 rather than being implemented — LLM Q&A is step 7, and keyword
search is what step 4 called for. Added a `/gaps` endpoint, on the grounds that "don't lose audio
silently" isn't satisfied by a table nobody can read.

**Next:** benchmark `small.en` vs `large-v3-turbo` on the real Pi (needs the ssh target) and log
the numbers here; then step 1 bench wiring once the hardware is wired.

---

## 2026-09-22 — planning and feasibility review

**Done:** Wrote up the project context (`CLAUDE.md`), reviewed the plan for feasibility
(`docs/PLAN_REVIEW.md`), worked out the compact layout and mounting acoustics
(`hardware/FORM_FACTOR.md`), and scaffolded the repo.

**Findings that changed the design:**

- Flash is far too small to be the audio buffer. ~4.875 MB of LittleFS at 32 KB/s is only ~2.5
  minutes. Switched the primary buffer to **PSRAM** (8 MB ≈ 4 min), with flash as overflow only.
  Also avoids cycling the whole filesystem hundreds of times a day.
- The Pi 5 removes the transcription throughput worry — but only because VAD cuts 8 h of wear down
  to ~50–100 min of actual speech. VAD is load-bearing, not an optimization.
- Revised the Whisper pick from whisper.cpp to **faster-whisper**: the Pi 5 makes the Python stack a
  non-issue, and it bundles Silero VAD plus word timestamps.
- The 2 TB HDD holds ~6 years of raw WAV even with no VAD, so **no retention policy is needed** —
  deleted a whole planned subsystem.
- The stock Zulkit box (64 × 38 × 12 mm) is ~1 mm too short for the stack. Enclosure will be
  fabricated to ~58 × 38 × 13 mm.
- Belt mounting measured out at ~−11.5 dB vs collar, which is ~+3.5 dB SNR in an office — below
  where Whisper holds up. Chest level is ~−4 dB and is the recommendation. To be confirmed
  empirically at step 1.
- "Cloud" resolved as self-hosted + private tunnel for remote access. **The device never uses the
  tunnel** — it uploads on the LAN only, which keeps TLS and VPN complexity off the ESP32.

**Decisions pending a measurement:** everything in the power budget, the I2S gain shift, and the
mount position.

**Next:** wear mockup (tape the bare parts together), then step 1 bench wiring, with the Whisper
benchmark in parallel on the Pi.

---

## Template for new entries

```
## YYYY-MM-DD — short title

**Goal:**
**Setup:** (what was wired/configured, model versions, exact commands)
**Result:** (real numbers, real output)
**Interpretation:**
**Decided:**
**Next:**
```
