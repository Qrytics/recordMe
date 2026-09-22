# Wiring and pinout

Bench wiring for step 1 is just the mic and the XIAO, USB-powered — **no battery, no switch, no
case.** Add power only once I2S capture is proven over serial.

## Connections

### INMP441 → XIAO ESP32-S3

| INMP441 | XIAO | GPIO | Notes |
|---|---|---|---|
| VDD | 3V3 | — | 3.3 V only |
| GND | GND | — | |
| SCK | D2 | 4 | bit clock, 1.024 MHz |
| WS | D3 | 5 | word select |
| SD | D4 | 6 | data, mic → MCU |
| L/R | GND | — | **must be grounded** for mono / left slot |

### Sense dividers (step 3, after I2S works)

Four 100 kΩ resistors, 1% or better:

```
BAT+ ──[100k]──┬──[100k]── GND     →  D0 / GPIO2  (ADC1_CH1)
             sense

5V pad ─[100k]─┬──[100k]── GND     →  D1 / GPIO3  (ADC1_CH2)
             sense
```

**Both sense pins must be on ADC1.** ADC2 is unusable while Wi-Fi is active, which would break
battery readings during exactly the moments we care about. D0/GPIO2 and D1/GPIO3 are ADC1_CH1 and
ADC1_CH2. ✓

Scaling: 4.2 V battery → 2.1 V at the pin; 5.25 V VBUS → 2.6 V. Both inside the ADC's usable range.
Quiescent drain is ~21 µA per divider, negligible against ~80 mA. A 100 nF cap across each lower
resistor would steady readings if they're noisy.

The XIAO's `5V` pad is tied to USB VBUS, so it reads ~0 V on battery power — a clean charge-detect.

### Switch

Hard power cut in the **BAT+ line** between battery and board. Not wired to any GPIO; the firmware
cannot read it. Off means zero standby drain.

Consequence: the battery only charges with the switch ON, so the device boots while charging. That's
what VBUS sensing is for — the firmware detects it and enters charge mode (no recording, flush the
upload backlog instead).

Keep the battery and switch wires **short and thick**. Wi-Fi bursts pull 300–500 mA, and thin or
long wiring adds resistance that can brown out the board mid-upload. If brownouts appear, a bulk cap
across BAT+/BAT− is the fix — that's a BOM addition, so ask first.

## Before applying power

- **Meter the LiPo leads against the BAT+/BAT− pads.** Reversed polarity can destroy the board. If
  the leads don't match the pads, re-pin carefully or use a pigtail. Never force it.
- **Insulate the battery from the XIAO's underside.** The back of the board has exposed pads and
  solder joints; a punctured or shorted LiPo pouch is a fire.
- Confirm the LiPo has an integrated protection circuit.
- Keep I2S wires short and routed away from the antenna.

## Gotchas

- **32-bit I2S slots, not 16.** The INMP441 needs SCK between 512 kHz and 4.096 MHz. At 16 kHz with
  32-bit slots, SCK is 1.024 MHz ✓. 16-bit slots would sit exactly on the 512 kHz minimum.
- **The mic is quiet.** It's a 24-bit part at −26 dBFS sensitivity, so samples need a right-shift
  plus software gain. `SAMPLE_SHIFT` in `config.h` starts at 11 — tune it against real speech.
- **Verify which slot you actually get.** Some ESP-IDF versions have returned the right channel when
  configured for left-only. Test with a known signal instead of trusting the enum.
- **The INMP441 is a bottom-port mic** — on the usual breakout, the acoustic hole goes *through the
  PCB*, on the opposite face from the chip. Confirm which side before deciding board orientation in
  the case. A sealed-in mic hears nothing.
- The second INMP441 is a spare. Assume one may die during soldering — and it's also the one to use
  for the long-cable experiment in `FORM_FACTOR.md` §2.4.
