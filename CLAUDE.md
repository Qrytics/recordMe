# recordMe — project context for Claude

A wearable voice recorder + a fully local transcription/search pipeline. This file is the
durable context for the build. Keep it current; append measurements and decisions to
`BUILD_LOG.md`, not here.

## Working agreement

- **Iterative, small, verifiable.** Propose a plan, get agreement, implement one step, prove it
  works (serial output, a measurement, a passing request) before moving on.
- **Never change the bill of materials or add parts without asking first.** Every part is already
  purchased. If a part looks unfit, say why and propose alternatives — but wait for a decision.
  This includes "trivial" additions: resistors, capacitors, foam tape, connectors.
- Prefer measuring over assuming. Power, audio quality, and transcription throughput are all
  claims to be tested, not calculated once and trusted.
- Document gotchas as they're found. `BUILD_LOG.md` is a running log of what we tried, measured,
  and decided (including dead ends).

## 1. What we're building

A collar-clip device whose entire user interface is one physical switch. Switch on → it records
the wearer's voice. Switch off → it stops. Audio is uploaded over home Wi-Fi to a Raspberry Pi,
which transcribes locally, stores timestamped transcripts in SQLite, and exposes keyword search
plus a local-LLM Q&A layer over the history.

## 2. Hard requirements (non-negotiable)

| Requirement | Detail |
|---|---|
| Mic only | No screen, no speaker, no extra sensors, no features past record on/off |
| Form factor | Small/light enough to clip to a shirt collar |
| One control | A single switch, wired as a hard power cut (off = zero standby drain) |
| Battery life | ~8 hours measured, not calculated |
| 100% local | Device → Pi over LAN. Transcription, storage, search all on the Pi. No third-party cloud, no accounts. Data lives only on the Pi |
| Remote access | Search interface reachable from outside the house over a private tunnel (Tailscale). **The device itself never uses the tunnel** |
| Searchable | Keyword search + "what did I say about X on Tuesday" via local LLM |
| No app | No companion app required for daily use |

"Local" here means self-hosted with private remote access: all data lives on the Pi and no third
party holds it, but the search interface is reachable from outside the house over a tunnel. See
`docs/PLAN_REVIEW.md` §9.

## 3. Bill of materials (fixed — Amazon #114-9467886-7105804, delivered)

1. **Seeed Studio XIAO ESP32-S3** — 21 × 17.5 mm, ESP32-S3R8: 8 MB flash, 8 MB octal PSRAM,
   Wi-Fi, hardware I2S, USB-C (flash + LiPo charging), BAT+/BAT− pads on the underside,
   U.FL external antenna (ships with a small IPEX antenna).
2. **INMP441 I2S MEMS mic × 2** (one is a spare). 3.3 V, omnidirectional, digital I2S.
   Wiring: VDD→3V3, GND→GND, SD→I2S data-in, WS→word select, SCK→bit clock, **L/R→GND** (mono,
   left channel).
3. **Liter 3.7 V 1200 mAh 603450 LiPo** — roughly 6 × 34 × 50 mm, ~23 g. Confirm it has an
   integrated protection circuit before use.
4. **Switch(es)** — hard power cut in the battery line.
5. **Zulkit ABS project boxes (5-pack)** — 64 × 38 × 12 mm exterior. Too short by ~1 mm for the
   stack, so the enclosure will be **makeshift/fabricated** to the compact layout in
   `hardware/FORM_FACTOR.md`. Target ~58 × 38 × 13 mm rigid, ~56 × 36 × 11 mm heat-shrink wrapped.
   Needs a mic port, USB-C cutout, and switch slot.
6. **Swivel clip** — collar mount.

## 4. Architecture

```
[INMP441] --I2S--> [XIAO ESP32-S3] --burst Wi-Fi HTTP--> [Raspberry Pi]
                    PSRAM ring buffer                     receiver (FastAPI)
                    + flash overflow                        ↓ queue
                    energy VAD gate                       VAD + Whisper (local)
                    status LED                              ↓
                                                          SQLite + FTS5 + audio files
                                                            ↓
                                                          keyword search + local LLM Q&A
```

**The Pi is a separate always-on host sitting on the home network — it is not part of the wearable
and nothing about it is mounted on the device.** The wearable contains only the XIAO, mic, battery,
and switch. The Pi is reached over Wi-Fi and does all the heavy lifting (transcription, storage,
search). It is the "server" side of a two-box system.

Bind addresses matter, because only one of these is remotely reachable:

| Service | Binds to | Auth | Reachable from |
|---|---|---|---|
| Upload receiver | LAN address | shared-secret bearer token | home Wi-Fi only |
| Search / LLM Q&A | tunnel interface + localhost | the tunnel, plus a password | anywhere, via tunnel |
| Ollama | localhost only | n/a | via the search service |

Never port-forward the Pi to the open internet. Details in `docs/PLAN_REVIEW.md` §9.

1. Switch ON → boot, init I2S, mic streams PCM.
2. Capture into a **PSRAM** ring buffer; assemble 30–60 s chunks. Spill to LittleFS in flash only
   when Wi-Fi is unavailable. Wi-Fi radio off while recording.
3. On schedule / when the buffer fills: bring up Wi-Fi, POST pending chunks, wait for ACK, free
   them, drop Wi-Fi. Retry with backoff.
4. Pi receives, queues, runs VAD then Whisper.
5. Store transcript + metadata in SQLite; keep the WAV on disk, referenced by path.
6. Search: FTS5 keyword search first, then a local LLM layer over results.
7. Switch OFF → power physically cut. Un-ACKed chunks in flash survive and re-upload on next boot.

## 5. Decided technical choices

These are my recommendations, adopted unless overruled. Each has a reason; revisit if a
measurement contradicts it.

- **Firmware framework: Arduino core for ESP32 v3.x.** It sits on top of ESP-IDF 5.x, so raw IDF
  calls (`i2s_channel_read()`, `esp_wifi_stop()`, light sleep, `esp_timer`) are available from
  Arduino code. Fastest to a prototype with no corner to paint into. Use the modern
  `driver/i2s_std.h` API — **not** the deprecated `driver/i2s.h`.
- **Audio format: 16 kHz, 16-bit, mono.** Matches Whisper's native input rate, so the Pi does no
  resampling. 32 KB/s = 1.92 MB/min.
- **I2S slot width: 32-bit, stereo frame, take the left slot.** The INMP441 requires SCK between
  512 kHz and 4.096 MHz. 16 kHz × 32 bits × 2 slots = 1.024 MHz ✓. 16-bit slots would put SCK at
  exactly the 512 kHz minimum. Extract 16-bit samples by right-shifting the 32-bit word and
  applying software gain (the INMP441 is quiet; expect to tune the shift empirically and clip).
- **Primary buffer is PSRAM, not flash.** See the flash-capacity problem in §7 — this is the most
  important design decision on the device side.
- **Timestamps are assigned by the Pi.** The ESP32 has no battery-backed RTC, so wall-clock time
  is unknown at every power-on. The device sends a monotonic `boot_id` + `chunk_start_ms` (ms
  since boot); the Pi computes absolute time as `received_at − (now_ms − chunk_start_ms)`. This is
  robust even if a chunk sits in flash for 20 minutes. SNTP from the Pi is a later refinement.
- **VAD in both places, for different reasons.** Cheap RMS-energy gate with a hangover timer on
  the device (saves battery, Wi-Fi, and the scarce buffer); accurate Silero VAD on the Pi before
  Whisper (saves transcription time and stops Whisper hallucinating on silence). Device-side
  gating is ~20 lines and worth it — see the throughput problem in §7.
- **Whisper: faster-whisper, `small.en`, on the Pi.** The Pi 5 / 16 GB makes the Python ML stack a
  non-issue, and faster-whisper bundles Silero VAD (the Pi-side VAD stage we need anyway) plus
  word-level timestamps, and runs in the same venv as the FastAPI receiver. `large-v3-turbo` is a
  viable quality upgrade — benchmark first. Fall back to whisper.cpp if CTranslate2 has no working
  aarch64 wheel.
- **Local LLM: Ollama on the Pi.** 16 GB is ample; an 8B at Q4_K_M fits but is bandwidth-bound
  (single-digit tok/s), a 3–4B roughly doubles that. Retrieval-then-answer: FTS5 finds candidate
  transcript rows, the LLM only summarizes/answers over those rows. Never stuff a day of
  transcripts into a context window.
- **Audio on the 2 TB HDD, SQLite on the SD card.** The HDD holds ~6 years of raw WAV even with no
  VAD, so **keep WAV indefinitely — no retention policy, no Opus transcode.** The DB is ~55 MB/year,
  so it belongs on faster storage for search latency. WAL mode. Nightly `sqlite3 .backup` elsewhere,
  since one drive means no redundancy and the transcripts are the irreplaceable part. Volumes and
  USB-HDD caveats in `docs/PLAN_REVIEW.md` §3.3.
- **Battery + VBUS sensing: four 100 kΩ resistors**, two dividers, both into **ADC1** pins
  (A0/GPIO2 and A1/GPIO3) — ADC2 is dead while Wi-Fi is active. Full spec and charge-mode
  behaviour in `docs/PLAN_REVIEW.md` §4.
- **Charge mode**: VBUS present → don't record, and use the free mains power to flush the upload
  backlog over Wi-Fi.
- **Upload protocol: plain HTTP POST, raw binary body** (`application/octet-stream`), metadata in
  query string or headers, `Authorization: Bearer <shared secret>`, LAN-only bind. Not multipart —
  avoids encoding overhead on the device. Keep the TCP connection alive across all chunks in one
  burst. Audio is unencrypted on the wire; acceptable on a home LAN, and TLS on the ESP32 costs
  RAM, CPU, and battery.
- **Hardcoded Wi-Fi credentials** in a gitignored `firmware/secrets.h`. Agreed — it's a personal
  device with no UI. Ship a `secrets.h.example`.
- **Tailscale for the remote-access tunnel**, on the Pi only. Its coordination server sees metadata
  (device names, IPs) but never the data — WireGuard keys stay local and traffic is E2E encrypted.
  Headscale or plain WireGuard remove that third party if the metadata matters; it's a later swap
  that changes nothing else.
- **No OTA.** Saves a partition, and the USB-C cutout already exists for reflashing. This also
  means the custom partition table can give nearly all remaining flash to LittleFS.

## 6. Open questions

Resolved: Pi 5 / 16 GB · 2 TB USB HDD, no retention policy needed · sense resistors approved and on
hand · VBUS sensing approved · enclosure will be fabricated to `hardware/FORM_FACTOR.md` · mic
gasket not on hand but sourceable (deferred to step 5, not blocking).

Also resolved: the Pi 5 has an active cooler, so sustained Whisper won't throttle.

**No blockers remain.** Still open:

- **Mount position** — collar, chest, or belt. Recommendation is chest level (placket/pocket/lapel):
  ~4 dB down from collar, but carries the ~40 g far better. Belt is ~11.5 dB down plus clothing
  rustle and drape, which makes it unreliable outside a quiet room. Decidable empirically at step 1.
  Full analysis in `hardware/FORM_FACTOR.md` §2.
- **If belt mounting wins**, a beltpack + collar-mic-on-a-cable is the right topology, but a
  shielded 4-conductor cable and mic-end decoupling caps become BOM additions needing approval —
  and running I2S at 1.024 MHz over ~70 cm contradicts the short-wires rule, so it must be tested.
- Mic gasket, if the step-5 in-case audio test shows it's needed.

## 7. Known problems with the plan — read before designing

These are real holes, not hypotheticals. They shape the firmware.

**Flash is far too small to be the buffer.** 8 MB total, minus app and partition overhead, leaves
roughly 4–5 MB for LittleFS. At 32 KB/s that is **~2 minutes of audio**, not hours. Consequences:
- "Pi unreachable → buffer locally and keep retrying" only covers a couple of minutes.
- Upload cadence must be ~60–90 s, so Wi-Fi duty cycle is more like 8–12%, not the 2–5% assumed
  in the original power budget. Average draw lands nearer 85–100 mA → ~12 h theoretical, so the
  8 h target still holds but with less margin than assumed.
- Continuous LittleFS writes at 32 KB/s also cycle the whole filesystem hundreds of times per
  day, which is needless flash wear.
- **Therefore: PSRAM ring buffer as the primary store (8 MB ≈ 4 min), flash purely as overflow
  when Wi-Fi is down.** Custom `partitions.csv` required either way; the default 8 MB scheme
  gives a tiny SPIFFS.
- ADPCM (IMA, 4:1, nearly free CPU) is the obvious next lever: 4× the buffer depth and 4× shorter
  uploads, and Whisper handles ADPCM speech fine. Raw PCM first, ADPCM as a phase-2 optimization.

**The Pi must keep up with what we record, forever.** With VAD, a day of wear is only ~50–100 min
of actual speech to transcribe, which a Pi 5 clears easily — so this is no longer a constraint, but
it is *entirely* because of VAD. Without it the pipeline would be transcribing 8 h of mostly silence
per day. Benchmark the chosen model on the real Pi early; it's independent of the device hardware
and it can still invalidate the model choice.

**"Never lose audio silently" is not achievable, only "never lose it silently."** With a few
minutes of buffer, a long Pi outage *will* drop audio. Handle it explicitly: oldest-first drop, a
distinct LED pattern, monotonic per-boot sequence numbers, and Pi-side gap detection so missing
sequence numbers are recorded as gaps in the DB rather than vanishing.

**Weight and droop.** The LiPo alone is ~23 g; assembled the device is plausibly 40 g, which may
visibly sag thin collar fabric. Validate early and cheaply: tape the bare parts together in the
intended stack and wear it for an hour before fabricating anything. Chest-level mounting (placket
seam, pocket edge, lapel) carries the weight much better at a cost of only ~4 dB.

**The battery sets the size floor and it is most of the device.** At 6 × 34 × 50 mm it is 55% of the
internal volume, so nothing can be smaller than ~50 × 34 × 10 mm bare. The only compact arrangement
is the XIAO and mic stacked on the battery's top face — end-to-end needs 71 mm of length, and
rotating the XIAO doesn't help since height is the binding axis. Layout, placement rules, and
dimensions in `hardware/FORM_FACTOR.md` §1.

**Battery pouch vs. antenna.** A LiPo's metal pouch next to the U.FL antenna will detune and block
it. Place the battery at the opposite end of the case from the antenna, antenna toward the top
edge. Wi-Fi reliability at range depends on this.

**Brownouts on Wi-Fi TX peaks.** Wi-Fi bursts pull 300–500 mA. Long, thin battery/switch wiring
adds resistance and can brown out the board mid-upload. Keep those wires short and fat. If
brownouts appear, a bulk capacitor across BAT+/BAT− is the fix — that's a BOM addition, so ask.

## 8. Hardware gotchas — verify before powering anything

- **Battery polarity.** Meter the LiPo leads against the XIAO's BAT+/BAT− pads *before*
  connecting. Reversed polarity can kill the board. If the leads don't match, re-pin carefully or
  use a pigtail. Never force it.
- **INMP441 L/R → GND** for mono/left. Note that on some ESP-IDF versions the "left only" slot
  config has historically returned the right channel; verify empirically with a known signal
  rather than trusting the enum.
- **The INMP441 is a bottom-port mic** — on the usual breakout the acoustic hole goes *through the
  PCB*, on the opposite face from the chip. Confirm which side the hole is on; it determines board
  orientation in the case. A sealed-in mic hears nothing.
- Keep I2S wires short and away from the antenna area.
- The second INMP441 is a spare. Assume one may die during soldering.
- USB-C is for flashing and charging; the device runs from the battery in use.
- Half-written chunk files will exist after a mid-write power cut. On boot, scan the FS and either
  salvage or discard partials before uploading.

## 9. Legal note

This records everyone within earshot, not just the wearer. Recording-consent law varies by
jurisdiction (some places require all-party consent). Worth knowing; the user's call.

## 10. Repo layout

```
firmware/     device code (Arduino/ESP32), partitions.csv, secrets.h.example
pi/           receiver, transcription worker, DB schema, search/LLM interface
hardware/     wiring notes, pinout table, power measurements, enclosure mods
docs/         anything longer-form
BUILD_LOG.md  running log: what we tried, measured, decided
CLAUDE.md     this file
```

Companion docs:
- `docs/PLAN_REVIEW.md` — pre-build feasibility assessment: Pi-side plan, storage, sense-resistor
  spec, charge-mode behaviour, power budget, remote-access architecture.
- `hardware/FORM_FACTOR.md` — compact layout and dimensions (§1), and the mount-position acoustics
  comparing collar / chest / belt (§2).
- `hardware/WIRING.md` — pinout, sense dividers, switch wiring, pre-power checks.
- `firmware/README.md` — build/flash commands, pinout, where to start.
- `pi/README.md` — services and bind addresses, setup, storage split, transcription notes.

Current state: **Pi side implemented (step 4), firmware still a scaffold.**

- `pi/` is working code: receiver, transcription worker, keyword search, and the DB layer, with
  121 tests in `pi/tests/` (a fake Whisper model, so the suite needs no ML deps). Proven
  end-to-end over real HTTP — upload → faster-whisper + Silero VAD → SQLite → search. Also
  `pi/bench_whisper.py`, which has **not** yet been run on the actual Pi; the model choice is
  therefore still unmeasured. `schema.sql` is unchanged and validated.
- `firmware/recordme/` is still scaffold: `config.h` (real pin and audio constants),
  `partitions.csv` (verified arithmetic), and a `recordme.ino` skeleton with the state machine
  mapped and function bodies stubbed.

Note for `pi/` code: annotations use `Optional[...]` rather than `X | None` on purpose, so the
modules import on Python 3.9 dev machines as well as the Pi's 3.11. See `BUILD_LOG.md` 2026-09-23.

## 11. Build order and status

| # | Step | Status |
|---|---|---|
| 1 | Bench wiring: mic → XIAO I2S, battery (polarity checked), switch. Prove I2S capture over USB serial | not started |
| 2 | Power: measure real current draw; runtime test toward 8 h | not started |
| 3 | Firmware: PSRAM buffering + chunking + burst upload to a test endpoint | not started |
| 4 | Pi pipeline: receiver → VAD → Whisper → SQLite → keyword search | **implemented**, 121 tests, proven end-to-end on a dev machine; Whisper not yet benchmarked on the Pi |
| 5 | Enclosure: fabricate to `hardware/FORM_FACTOR.md`, mic port, USB-C and switch cutouts, clip mount; re-test audio in case | not started |
| 6 | Field test: wear at the chosen mount point, clarity at speaking distance, full-day battery and upload reliability | not started |
| 7 | Search UI: local-LLM Q&A over transcripts | not started |

Benchmark Whisper on the real Pi during step 1 or 2 — it's independent of the device and it can
invalidate the architecture, so don't leave it until step 4.

## 12. Definition of done

- Clips on at the chosen mount point (collar or chest, §6); flip on → records, flip off → stops.
- Measured 8-hour runtime.
- Audio reliably reaches the Pi, is transcribed, and is stored with correct timestamps.
- Keyword search works, and local-LLM Q&A over transcripts works.
- No screen, no companion app needed for daily use.

## 13. Assumptions

- Raspberry Pi 5, 16 GB RAM, active cooler, 2 TB USB HDD, on the home Wi-Fi.
- The user can solder and owns a multimeter.
- "Local LLM" means on hardware the user owns — the Pi or another LAN machine — never a cloud API.
