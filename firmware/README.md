# Firmware

Device code for the XIAO ESP32-S3. **Scaffold only so far** — structure and constants are in place,
the implementation isn't.

## Framework

Arduino core for ESP32 **v3.x**, which is built on ESP-IDF 5.x. That means raw IDF calls
(`i2s_channel_read()`, `esp_wifi_stop()`, light sleep) are available directly from this code, so
using Arduino for speed doesn't close any doors. Use `driver/i2s_std.h`, **not** the deprecated
`driver/i2s.h`.

## Layout

```
recordme/
  recordme.ino        state machine and lifecycle
  config.h            pins, audio format, buffer sizes, thresholds
  secrets.h.example   copy to secrets.h (gitignored) and fill in
  partitions.csv      8 MB layout: 3 MB app, 4.875 MB LittleFS, no OTA
```

## Build and flash

```bash
arduino-cli core install esp32:esp32
arduino-cli compile -b esp32:esp32:XIAO_ESP32S3 \
  --build-property "build.partitions=partitions" \
  --build-property "build.psram_type=opi" \
  recordme
arduino-cli upload -b esp32:esp32:XIAO_ESP32S3 -p /dev/tty.usbmodem* recordme
arduino-cli monitor -p /dev/tty.usbmodem* -c baudrate=115200
```

PSRAM must be enabled or the buffer design doesn't work — confirm `ESP.getPsramSize()` returns ~8 MB
before trusting anything else. If the board doesn't enumerate, hold BOOT while plugging in to enter
download mode.

## Pinout

| Signal | GPIO | XIAO label | Notes |
|---|---|---|---|
| I2S SCK (bit clock) | 4 | D2 | to INMP441 SCK |
| I2S WS (word select) | 5 | D3 | to INMP441 WS |
| I2S SD (data in) | 6 | D4 | from INMP441 SD |
| VBAT sense | 2 | D0 / A0 | ADC1_CH1, 100k/100k from BAT+ |
| VBUS sense | 3 | D1 / A1 | ADC1_CH2, 100k/100k from the 5V pad |
| Status LED | 21 | — | onboard, active LOW — verify |

Both sense pins are deliberately on **ADC1**: ADC2 is unusable while Wi-Fi is active, which would
break battery readings during uploads.

The INMP441's `L/R` pin ties to **GND** (mono, left slot). The on/off switch is a hard power cut in
the battery line and is not wired to any GPIO.

## Before powering anything

- **Meter the LiPo leads against the BAT+/BAT− pads first.** Reversed polarity can destroy the board.
- Insulate the battery from the XIAO's underside — it has exposed pads and solder joints, and a
  punctured pouch is a fire.
- Keep I2S wires short and away from the antenna.

Full gotcha list in `CLAUDE.md` section 8.

## Where to start

Step 1 is proving I2S capture over USB serial, with no battery and no case — `audio_init()` and
`audio_read()`. The riskiest unknown is the `SAMPLE_SHIFT` gain value in `config.h`; the INMP441 is
quiet and 24-bit, so expect to tune it against real speech.
