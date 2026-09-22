# Build log

Running record of what was tried, measured, and decided — including dead ends, which are the most
useful entries later. Newest at the top.

Format: date, what was attempted, what actually happened, what it means. Paste real numbers and real
error messages; don't summarize them away.

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
