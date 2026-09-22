# Plan review — feasibility assessment

Status: **plan is sound. No blockers remain.** The enclosure concern is resolved (custom-fabricated,
layout spec in `hardware/FORM_FACTOR.md`) and storage is resolved (2 TB HDD removes the retention
problem entirely). Nothing has been built yet; this document is the pre-build soundness check.

Date: 2026-09-22. Inputs resolved:

| Question | Answer |
|---|---|
| Pi model | Raspberry Pi 5, 16 GB RAM |
| Pi storage | 2 TB USB HDD attached |
| Zulkit box dimensions | 64 × 38 × 12 mm — superseded, enclosure will be makeshift |
| Battery voltage sensing resistors | Approved, and on hand |
| Charging vs. switch | Add VBUS sensing |
| Mic gasket / foam tape | Not on hand; can potentially source |
| Pi cooling | Active cooler fitted — sustained Whisper won't throttle |
| Mounting position | Open — belt mounting asked about; see `hardware/FORM_FACTOR.md` §2 |

---

## 1. Verdict by subsystem

| Subsystem | Verdict |
|---|---|
| Mic → XIAO I2S capture | Sound. No concerns beyond the documented gain/slot-width gotchas. |
| PSRAM buffering + burst upload | Sound. Replaces the original flash-buffer design (flash is ~2 min, PSRAM is ~4 min). |
| Power budget / 8 h target | Sound, with margin. Now better than first estimated — see §5. |
| Battery + VBUS sensing | Sound. Exact spec in §4. Needs 4 resistors we may not have on hand. |
| Pi transcription pipeline | Sound, and comfortably over-specified. Pi 5 removes the throughput worry — see §3. |
| Pi storage | Resolved. 2 TB HDD is ample — no retention policy needed at all. See §3.3. |
| Local LLM search | Sound. 16 GB is ample. |
| Enclosure | Resolved — custom-fabricated. Target ~58 × 38 × 13 mm; layout in `hardware/FORM_FACTOR.md`. |
| Mount position / weight | Open question, not a blocker. Testable for free at step 1 — see `hardware/FORM_FACTOR.md` §2. |
| Mic gasket | Not blocking. Deferred to enclosure stage — see §6. |

---

## 2. Enclosure — resolved: fabricate, don't adapt

**Resolved:** the Zulkit box is set aside; the enclosure will be makeshift, built to the compact
layout in **`hardware/FORM_FACTOR.md`**. Target ~58 × 38 × 13 mm rigid, or ~56 × 36 × 11 mm
heat-shrink wrapped. The arithmetic below is retained because it explains why the stock box was
rejected and what sets the size floor.

Given 64 × 38 × 12 mm exterior and typical ABS project-box walls of 1.5–2.0 mm:

```
interior (1.5 mm walls):  61 × 35 × 9 mm   = 19.2 cm³
interior (2.0 mm walls):  60 × 34 × 8 mm   = 16.3 cm³
```

The 603450 LiPo is, by definition of its part code, **6.0 × 34 × 50 mm = 10.2 cm³** — roughly 55%
of the entire cavity. Two axes fail:

**Height fails by ~2.5 mm.** The required stack is:

```
battery                      6.0 mm
XIAO ESP32-S3 (incl. USB-C)  3.5 mm
solder joints + wire routing 2.0 mm
                            -------
                            11.5 mm  needed
                             8–9 mm  available
```

**Width has zero clearance.** The battery is 34 mm wide and the interior is 34–35 mm. There is no
room to route wires down either side, and LiPo pouches swell slightly over their life. The
battery's lead end with its protection PCB and folded wires typically adds 3–8 mm of effective
length as well.

That is before finding room for the INMP441 breakout (~18 × 15 mm), the U.FL antenna, the switch,
the four sense resistors, and the clip's mounting screws.

### 2.1 What sets the floor

The battery is 55% of the internal volume, so nothing can be smaller than the battery plus one
board thickness: **50 × 34 × 10 mm bare stack**. Arrangements already checked and rejected —
mounting the XIAO end-to-end with the battery needs 71 mm of length, and rotating the XIAO doesn't
help because height is the failing axis, not footprint. The only compact arrangement is the XIAO
and mic stacked on the battery's top face, which is what `hardware/FORM_FACTOR.md` specifies.

Note that the stock box's *footprint* was never the problem — it's 1 mm short on height and 6 mm
longer than needed. Worth a caliper check before fabricating; it may simply close.

### 2.2 Related unvalidated risk

At roughly 40 g, this is a substantial thing to hang on thin collar fabric. Before fabricating
anything: tape the bare battery, board, and mic into the intended stack and wear it for an hour.
Chest-level mounting (placket seam, shirt pocket edge, lapel) carries the weight far better than a
collar and costs only ~4 dB of audio — see `hardware/FORM_FACTOR.md` §2 for the full comparison,
including the belt-mounting analysis.

---

## 3. Pi 5 / 16 GB changes the server-side plan for the better

### 3.1 Transcription throughput is no longer a constraint

My earlier framing was too pessimistic because it ignored what VAD does to the workload. Real
numbers:

- 8 h of wear, with speech at a realistic 10–20% of wall time → **roughly 50–100 min of audio to
  actually transcribe per day**, not 8 hours.
- A Pi 5 (Cortex-A76 @ 2.4 GHz) should run `small.en` quantized at meaningfully faster than
  realtime, so a full day of wear clears in well under an hour of compute.

This means we can afford accuracy rather than scraping by. Recommendation: start with **`small.en`**,
benchmark on the actual Pi, and treat **`large-v3-turbo`** as a live upgrade option — even at
below-realtime speed it would clear a day's audio in a couple of hours overnight. Benchmark before
committing; these are estimates, not measurements.

### 3.2 Revised Whisper pick: faster-whisper, not whisper.cpp

I originally picked whisper.cpp to avoid fighting the Python ML stack on a weak ARM board. A Pi 5
with 16 GB removes that motivation, and faster-whisper now wins on integration:

- Bundled Silero VAD — exactly the Pi-side VAD stage the plan calls for, no separate component.
- Word-level timestamps, which make transcript search results far more useful.
- Runs in the same Python venv as the FastAPI receiver: one process tree, one dependency set.

Keep whisper.cpp as the fallback if CTranslate2 has no working aarch64 wheel for the Pi's Python
version — verify that at install time rather than assuming.

### 3.3 Storage — resolved: the 2 TB HDD makes retention a non-problem

At 16 kHz 16-bit mono against 2 TB:

| Scenario | Per day | 2 TB lasts |
|---|---|---|
| Raw WAV, no VAD | ~920 MB | **6.0 years** |
| Raw WAV, VAD at ~15% speech | ~140 MB | **39 years** |
| FLAC, VAD | ~75 MB | 73 years |

Transcripts are negligible either way — ~150 KB/day, about **55 MB/year**.

So: **keep raw WAV indefinitely, no retention policy, no Opus transcode.** That deletes a whole
subsystem from the plan. Just monitor free space and age out oldest-first if it ever reaches ~85%,
which on these numbers is years away. FLAC stays available as a free halving if ever wanted.

Four notes specific to a USB HDD rather than an SSD:

- **Split by role: audio on the HDD, SQLite on the SD card.** The DB is tiny (~55 MB/year) so SD
  write volume is a non-issue, and keeping the DB and its FTS index off a spinning USB disk keeps
  search latency low. Use WAL mode either way.
- **The disk will stay spun up all day.** Uploads arriving every 2–3 minutes mean it never idles.
  Fine for a drive, just expected. If spin-down is wanted, stage chunks on the SD card and flush to
  the HDD every ~30 min — probably not worth the complexity.
- **If it's a bus-powered 2.5" drive**, make sure the Pi 5 is on the official 27 W PSU, and set
  `usb_max_current_enable=1` if the drive misbehaves under load.
- **One drive means no redundancy.** Audio is replaceable-ish, but transcripts are not — losing the
  DB loses everything the project is for. A nightly `sqlite3 .backup` to a second location is cheap
  insurance at 55 MB/year.

Thermals are covered — the Pi 5 has an active cooler fitted, so sustained multi-core Whisper won't
throttle.

### 3.4 Local LLM

16 GB is ample. An 8B-class model at Q4_K_M fits easily but is memory-bandwidth bound on a Pi 5 —
expect single-digit tokens/sec. That's fine for this workload: retrieval does the heavy lifting,
so the model only reads ~20 short transcript rows and writes a few sentences. A ~100-token answer
in ~25 s is acceptable for "what did I say about X on Tuesday." A 3–4B model roughly doubles the
speed if that trade feels better. Benchmark both.

---

## 4. Battery and VBUS sensing — approved, here's the spec

Four 100 kΩ resistors (1% or better), forming two dividers:

```
BAT+ ──[100k]──┬──[100k]── GND     →  A0 / GPIO2  (ADC1_CH1)
               │
             sense

5V pad ─[100k]─┬──[100k]── GND     →  A1 / GPIO3  (ADC1_CH2)
               │
             sense
```

Design notes:

- **Both pins must be on ADC1.** ADC2 is unusable while Wi-Fi is active on ESP32 — a classic trap
  that would make battery readings fail precisely during uploads. A0/GPIO2 and A1/GPIO3 are
  ADC1_CH1 and ADC1_CH2 on the XIAO ESP32-S3. ✓
- Scaling checks out: 4.2 V battery → 2.1 V at the pin; 5.25 V VBUS → 2.6 V. Both inside the
  ESP32-S3 ADC's usable range with 12 dB attenuation.
- Quiescent drain is 4.2 V / 200 kΩ ≈ **21 µA** per divider — negligible against ~80 mA.
- The XIAO's `5V` pad is tied to USB VBUS, so it reads ~0 V on battery power. That gives a clean
  charge-detect. VBUS can be read as a plain digital HIGH/LOW if we don't want a second ADC channel.
- ESP32-S3 ADC accuracy is mediocre. Use the curve-fitting calibration scheme and trim against a
  multimeter reading once.
- A 100 nF cap across each lower resistor would steady the readings. Optional, and another part —
  flagging rather than assuming.

Resistors are confirmed on hand, so this work is unblocked.

### 4.1 Charge-mode behaviour (VBUS sensing approved)

With the switch in the BAT+ line, charging requires the switch ON, so the firmware must handle
being powered up while charging:

- VBUS present at boot → **charge mode**: do not record, do not buffer.
- VBUS appears mid-recording → finish the current chunk cleanly, then enter charge mode.
- Charge mode is the ideal time to **flush any upload backlog** — mains power is free, so bring up
  Wi-Fi and drain every pending chunk rather than rationing the radio.
- Distinct LED pattern so charging is visually distinguishable from recording.
- VBUS disappears → resume normal recording.

This turns the switch/charger conflict from a wart into the backlog-recovery path.

---

## 5. Revised power and throughput budget

The PSRAM-buffer decision improves the picture over the original estimate:

- Recording (CPU + I2S active, Wi-Fi off): ~50–80 mA.
- Wi-Fi burst: ~200–300 mA, seconds per cycle.
- PSRAM gives ~4 min of buffer at 32 KB/s (vs. ~2 min for flash), so uploads can run on a ~2–3 min
  cadence instead of 60–90 s. Wi-Fi duty cycle lands nearer 5–8%.
- Average draw therefore ~75–95 mA → **1200 mAh ÷ ~85 mA ≈ 14 h theoretical**.
- Device-side VAD cuts this further by skipping silent chunks entirely — fewer bytes buffered,
  fewer uploads, less radio time.

The 8-hour target has real margin. Regulator losses, the LED, and poor Wi-Fi conditions eat into
it, so this stays a claim to be measured, not trusted.

---

## 6. Mic gasket — not blocking

The gasket only matters once the mic is mounted in a case, i.e. build step 5. It is not needed for
steps 1–4. Its job is to stop sound leaking around the acoustic port and to decouple case-borne
handling and wind noise; a mic pressed firmly against the case wall with a well-aligned, small port
performs acceptably without one.

If it turns out to be needed, cheap substitutes: adhesive weatherstrip foam, a slice of neoprene,
a punched disc from a sticky-foam mounting pad, or a dab of soft silicone. Decide at step 5 with a
real audio comparison rather than buying in advance.

---

## 7. Still open

No blockers. Remaining items:

1. **Mount position** — collar, chest, or belt. Analysed in `hardware/FORM_FACTOR.md` §2; my
   recommendation is chest level. Decidable empirically at step 1, so it doesn't gate anything.
2. **If belt mounting wins**, a shielded 4-conductor cable and mic-end decoupling caps become BOM
   additions needing approval.
3. Mic gasket, if the enclosure audio test at step 5 shows it's needed (§6).
4. Tunnel implementation: Tailscale to start, or straight to Headscale/WireGuard to avoid the
   third-party coordination server? Not blocking — it's the last thing built (§9.2).

Resolved: Pi model, RAM, cooling, storage and retention policy, sense resistors, VBUS/charge-mode
behaviour, enclosure approach and target dimensions.

---

## 8. What could start today, blocked on nothing

None of the open items gate the first moves. Available now with parts in hand:

- **Step 1 — bench wiring.** Mic → XIAO over I2S, powered from USB, no battery, no case, no
  switch. Proves I2S capture, slot width, and the software-gain shift over serial. The riskiest
  unknown in the whole firmware, resolvable in an afternoon.
- **Pi-side Whisper benchmark.** Entirely independent of the device. Install faster-whisper, time
  `small.en` and `large-v3-turbo` against a sample recording, confirm the throughput assumptions in
  §3.1 with real numbers. This can invalidate the model choice, so it's worth doing early.
- **The wear mockup.** Tape the bare parts into the intended stack and wear it for an hour at collar
  and at chest height. Costs nothing and de-risks the form factor before anything is fabricated.
- **The mount-position A/B.** Record the same speech at collar, sternum, and waist height, quiet and
  noisy, and compare Whisper output. Settles the belt question with real data — see
  `hardware/FORM_FACTOR.md` §2.5.

Recommended order: mockup (10 minutes, highest information per minute), then step 1 plus the
mount-position A/B on the same bench rig, with the Whisper benchmark in parallel since it touches
different hardware.

---

## 9. "Cloud" means self-hosted with private remote access

**Resolved.** The Pi is a separate always-on host on the home network — not mounted on, attached
to, or carried with the wearable. The wearable holds only the XIAO, mic, battery, and switch. Data
lives solely on the Pi; no third party ever holds it. On top of that, a private tunnel makes the
search interface reachable from outside the house.

The earlier `README.md` wording about an "external cloud storage server" was wrong and has been
corrected.

### 9.1 The device never uses the tunnel

This is the important structural consequence. **Only the search/query interface is remotely
reachable. The XIAO uploads over the LAN only.** Two separate listeners with different bind
addresses:

| Service | Binds to | Auth | Reachable from |
|---|---|---|---|
| Upload receiver | LAN address | shared-secret bearer token | home Wi-Fi only |
| Search / LLM Q&A | tunnel interface (+ localhost) | the tunnel itself, plus a password | anywhere, via tunnel |
| Ollama | localhost only | n/a | via the search service |

Keeping the device off the tunnel matters because a VPN client and TLS stack on the ESP32 would
cost RAM, CPU, battery, and a lot of firmware complexity for zero benefit — the device only ever
talks to the Pi from inside the house. Plain HTTP on the LAN stays the right call.

### 9.2 Tunnel choice

- **Tailscale** is the pragmatic pick: NAT traversal with no port forwarding, no static IP or DDNS,
  device-level identity, works from a phone. Caveat worth naming for a privacy-motivated project —
  Tailscale's coordination server is a third party that sees metadata (device names, IPs) but
  **never the data**: WireGuard keys stay on the devices and traffic is end-to-end encrypted.
- **Headscale** (self-hosted Tailscale control server) or **plain WireGuard** with port forwarding
  and DDNS remove that third party entirely, at the cost of more setup and maintenance.
- Recommendation: start with Tailscale; it's a swap to Headscale later if the metadata bothers you.

### 9.3 Non-negotiable

**Do not port-forward the Pi to the open internet.** The tunnel is what provides access; an exposed
port is not. The tunnel also does most of the authentication work, so the search UI doesn't need a
login system — but add a simple password anyway as defense in depth, since anything on the tunnel
can reach it.
