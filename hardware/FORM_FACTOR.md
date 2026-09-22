# Form factor: compact layout and where to wear it

Two questions answered here: how small can the device actually be, and does belt mounting work
acoustically. Short answers: **~58 × 38 × 13 mm in a rigid shell, ~56 × 36 × 11 mm heat-shrink
wrapped** — and **belt mounting is a real quality regression; chest level is the sweet spot.**

---

## 1. How compact can it get

The battery dominates everything. At 6 × 34 × 50 mm it is 55% of the internal volume, and its
34 mm width and 6 mm thickness set the floor for the whole device. Nothing can be smaller than the
battery plus one board thickness.

### 1.1 Layout

Everything stacks on top of the battery — the XIAO (21 × 17.5 mm) and the mic breakout
(~18.5 × 14 mm) both fit entirely within the battery's 34 × 50 mm top face, using 38 mm of its
50 mm length. Orientation assumes the device is worn vertically with the top edge pointing up
toward the head.

```
 front view (as worn)                        side view (the stack)

 ┌────────────────────────────┐   ← TOP EDGE   front shell ──────────────────  1.2 mm
 │   ▓▓▓▓▓ INMP441 ▓▓▓▓▓      │   mic port     antenna FPC  ░░░░              0.2 mm
 │                            │   drilled     XIAO         ▓▓▓▓▓▓▓▓▓▓▓       3.5 mm
 │    ┌────────────────┐      │   here        tape/isolation ─────────────    0.5 mm
 │    │ XIAO ESP32-S3  │      │               battery      ███████████████   6.0 mm
 │    │                │      │               back shell   ──────────────    1.2 mm
 │    └─────[USB-C]────┘      │                                            ≈ 12.6 mm
 │  ○○○○      [==switch==]    │   ← BOTTOM:
 └────────────────────────────┘   USB-C cutout        ↑ swivel clip mounts to the back shell
   ◄─────── 36 mm ────────►       + switch slot
```

| Zone | Contents |
|---|---|
| Top edge | INMP441 breakout, acoustic port aligned to a drilled hole |
| Middle | XIAO on top of the battery; antenna on the inner surface of the front shell above it |
| Bottom end zone (34 × 6 × 6 mm) | Battery leads, switch, the four sense resistors (inline on wires, heat-shrunk) |
| Back shell | Swivel clip mounting point |

### 1.2 Dimensions

```
internal:  56 (50 battery + 6 end zone) × 36 (34 + 1 clearance/side) × 10.5 mm
rigid shell @1.2 mm walls:   ~58 × 38 × 13 mm
heat-shrink / tape wrapped:  ~56 × 36 × 11 mm
absolute floor (bare stack):  50 × 34 × 10 mm
```

Worth noting: the Zulkit box you already have is 64 × 38 × 12 mm. The **footprint is fine** — it's
only ~1 mm short on height and 6 mm longer than needed. If you'd rather not fabricate, filing the
internal bosses or accepting a slightly bowed lid may well get it to close. Heat-shrink wrapping is
the genuinely more compact option, at ~11 mm.

### 1.3 Placement rules that matter

- **Antenna on the inner surface of the front shell, directly over the XIAO.** The battery pouch is
  metal and sits under everything, so there is nowhere to get truly clear of it. Putting the antenna
  on the outward-facing side with the XIAO between it and the pouch turns the pouch into a crude
  reflector aiming radiation away from your body and toward the room — which is where the access
  point is. Verify whether your board shipped with the flexible adhesive FPC antenna (ideal for
  this) or a rigid one.
- **Mic at the top edge, port aligned to the hole, with a short signal run to the XIAO.**
- **USB-C and switch at the bottom edge**, both reachable without opening the case.
- **Insulate the battery from the XIAO's underside** with tape or thin foam. The XIAO has solder
  joints and exposed pads on the back; a punctured or shorted LiPo pouch is a fire.
- The clip mounts to the back shell, so nothing internal may need access from that face.

---

## 2. Belt vs. collar: will the mic still hear you?

It will hear you. It will hear you noticeably worse, and in any room with background noise the
transcription quality will fall off a cliff. Here's the actual arithmetic.

### 2.1 Distance cost

Sound pressure drops ~6 dB per doubling of distance:

| Mount position | Mouth-to-mic | Level vs. collar |
|---|---|---|
| Collar | ~18 cm | reference |
| Sternum / shirt placket | ~28 cm | **−3.8 dB** |
| Shirt pocket | ~35 cm | −5.8 dB |
| Belt, seated | ~50 cm | −8.9 dB |
| Belt, standing | ~68 cm | **−11.5 dB** |

### 2.2 What that does to signal-to-noise

Taking normal conversational speech as ~65 dB SPL at the collar:

| Position | Quiet room (38 dBA) | Office (50 dBA) | Cafe (65 dBA) |
|---|---|---|---|
| Collar | +27 dB | +15 dB | 0 dB |
| Sternum | +23 dB | +11 dB | −4 dB |
| **Belt, standing** | **+16 dB** | **+3.5 dB** | **−11.5 dB** |

Whisper is robust but not magic; accuracy degrades hard below roughly 10 dB SNR. So belt mounting
is usable in a quiet room, marginal in an office, and hopeless anywhere with real background noise.
Collar and sternum both stay comfortably usable in an office.

### 2.3 The problems the decibels don't capture

The distance figure actually **understates** how much worse the belt is:

- **Clothing rustle.** The waistband is the single most motion-active part of an outfit — it flexes
  with every step, every time you sit. Professional lavaliers are placed at the sternum precisely
  because it's a motion-stable spot. A mic at the belt is in the worst possible place for this.
- **Drape.** An untucked shirt or any jacket will fall over a belt-mounted mic and muffle or
  completely kill it. At the collar nothing can cover it.
- **Chin and chest shadow.** A waist mic looks up into your own torso. High-frequency consonants —
  where intelligibility actually lives — attenuate the most off-axis, so speech gets muddier faster
  than the broadband dB number suggests.
- **Footfall.** Vibration couples through the belt into the case and straight into the mic.
- **Wi-Fi gets worse too.** At the hip, your torso is between the device and the access point far
  more of the time, and 2.4 GHz attenuates well in tissue. Chest and collar are better RF positions.

### 2.4 Recommendations, in order

**1. Chest level — shirt placket, pocket edge, or lapel. This is what I'd do.** Only ~4–6 dB down
from the collar, essentially all of the acoustic quality, and it solves the actual problem with
collar mounting: ~40 g hanging off thin collar fabric will droop. A placket seam or shirt-pocket
edge is structurally much better at carrying the weight, and it's still a single clip-on object
with no cable. If the collar bothers you, this is the answer rather than the belt.

**2. If the mass really must be at the waist: beltpack + collar mic on a cable.** This is the
professional lavalier topology — battery and board at the belt, just the mic clipped at the collar.
You keep collar-quality audio and move all the weight off your shirt. Costs:

- A 4-conductor cable (VDD, GND, SCK, WS, SD — so 5, or 4 plus shield as ground) running ~60–80 cm
  up inside your shirt. **This is a BOM addition** — a salvaged USB cable has 4 conductors plus a
  shield and would work. Needs your approval.
- It directly contradicts the "keep I2S wires short" rule. SCK is 1.024 MHz at 32-bit slots, which
  over 60–80 cm of *shielded* twisted cable is plausible but must be tested, not assumed. Ground
  the shield at the XIAO end only. Dropping to 16-bit slots would halve the clock to 512 kHz, but
  that is exactly the INMP441's minimum SCK — trading one risk for another, so keep 32-bit.
- The mic end will want local decoupling (100 nF + 1 µF) since VDD now runs up a long wire. Also a
  BOM addition.
- Wearability cost: it's a cable up your shirt. Every podcaster lives with it, but it's no longer
  "clip one thing on and flip a switch."

**3. Belt-only, no cable.** Free to try, genuinely fine in quiet rooms, unreliable elsewhere. Not
recommended as the primary design.

### 2.5 You can test all three for free at step 1

The bench rig — mic wired to a USB-powered XIAO, no battery, no case — answers this empirically in
about twenty minutes. Record the same speech with the mic held at collar, sternum, and waist
height, in a quiet room and with a fan or music running, then run all six through Whisper and
compare the transcripts. That replaces every estimate above with your voice, your rooms, your
chosen Whisper model. Do this before committing to a mount, and before fabricating anything.

It's also the cheapest way to test the long-cable idea: solder a 70 cm cable onto the spare
INMP441 and see whether the audio is clean. The second mic exists for exactly this kind of
experiment.
