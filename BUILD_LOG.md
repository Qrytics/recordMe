# Build log

Running record of what was tried, measured, and decided — including dead ends, which are the most
useful entries later. Newest at the top.

Format: date, what was attempted, what actually happened, what it means. Paste real numbers and real
error messages; don't summarize them away.

---

## 2026-09-23 — step 1 firmware: I2S capture + serial bench (written, NOT yet run)

**Goal:** write the step-1 firmware ahead of the bench session, so that finding `SAMPLE_SHIFT` —
the riskiest firmware unknown — is wire, flash once, read numbers, instead of a reflash per guess.

**Status, stated plainly: this has never run on hardware. Zero measurements.** It compiles, and
the arithmetic is verified on the host. Nothing else about it is proven.

**Toolchain:** no Homebrew on this Mac, so arduino-cli came from the official
`arduino-cli_latest_macOS_ARM64.tar.gz` into `~/.local/bin`. **arduino-cli 1.5.2, esp32 core
3.3.11** (IDF 5.x, so `driver/i2s_std.h` is present as the plan assumed). Compile is clean at
`--warnings all` except a pre-existing `chunk_seq defined but not used` from the scaffold, which
step 3 will use. Sketch: 301,635 bytes (9% of the 3 MB app partition), 27,548 bytes of static RAM.

**Written:**

- `audio.cpp` — I2S std mode, master, RX only, 32-bit slots. Deliberately **stereo frame with
  software slot selection** rather than `I2S_SLOT_MODE_MONO`: it costs 128 KB/s of DMA (nothing)
  and sidesteps the documented risk that the mono `slot_mask` returns the wrong channel. The bench
  meters *both* slots, so which one the mic is on becomes a measurement instead of a hope.
- `bench.cpp` — PSRAM check, per-second level meter, raw PCM dump, runtime keys (`+`/`-` gain,
  `l`/`r` slot, `d` dump). Statistics are kept in the raw 24-bit domain, so they stay valid when
  the gain is changed mid-session.
- `firmware/tools/serial_to_wav.py` — stdlib-only converter for the dump. Round-trip tested
  against a synthetic capture (boot text + header + 1 s sine + end marker + trailing text):
  output is **bit-identical** to the source PCM, and the truncated-dump, multi-dump, stdin, and
  no-marker paths all behave.

**Findings:**

- **The gain formula is exact, and checked.** The bench suggests `shift = bitlen(peak24) - 7` to
  land peaks at −6 dBFS. Verified numerically across the full range 2^6..2^23: every level lands
  at exactly −6.0 dBFS with no clipping. This mattered enough to test because an off-by-one here
  would send the whole bench session chasing the wrong value.
- **Prediction to falsify at the bench: `SAMPLE_SHIFT 11` is probably 2–3 too high.** From the
  INMP441 datasheet (−26 dBFS at 94 dB SPL, full scale 2^23), normal speech at 30 cm (~70 dB SPL)
  computes to a raw peak of ~26,500, which wants `shift=8`; at shift 11 it would sit around
  −20 dBFS. Left the default at 11 anyway — the measurement decides, not the datasheet.
- **PSRAM check allocates the real 6 MB `PSRAM_BUFFER_BYTES`**, pattern-tests it, and reports
  write bandwidth. "PSRAM is enabled" is the assumption the entire buffering design rests on, so
  it gets tested rather than trusted.
- **Pre-existing partition-table warning, not fixed here:** `gen_esp32part.py` says
  `Partition has name 'littlefs' ... type 0x1 subtype 0x82. Mistake in partition table?` The
  subtype is SPIFFS while the label says littlefs. Harmless today, but note that Arduino's
  `LittleFS.begin()` defaults to the partition **labelled `spiffs`**, so as written it will not
  find this partition without an explicit label argument. That's a step-3 problem; flagging it
  now rather than rediscovering it then. Left `partitions.csv` alone — the arithmetic is right and
  changing it isn't part of step 1.
- USB CDC dump is 32 KB/s, which the S3's native USB carries, but a slow host silently creates
  gaps. The firmware times the dump and reports its realtime ratio at stop, so dropouts get
  attributed to USB rather than to the mic.
- **Caught a hole in the dump workflow while documenting it:** the capture has to be running
  before the header is emitted, so "press `d` in the monitor, then `cat` the port" loses the
  header and the converter finds nothing. Two working routes instead — write `d` to the port from
  a second terminal while `cat` runs, or set `BENCH_DUMP_ON_BOOT` and press RESET with the capture
  already open. The second exists because opening the port from a second process may reset the
  board (USB-Serial-JTAG resets on some DTR/RTS sequences). Untested either way.

**Next (at the bench, in this order):** tap test → confirm slot L vs R → read `suggest shift` at
real speaking distance → set `SAMPLE_SHIFT` in `config.h` and log the measured numbers here →
dump and listen. Then the Pi-side `small.en` benchmark, which is still unmeasured.

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
