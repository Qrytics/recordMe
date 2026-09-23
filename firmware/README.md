# Firmware

Device code for the XIAO ESP32-S3. **Step 1 (I2S capture) is written and compiles; everything past
it is still scaffold.** `audio_init()`/`audio_read()` are real, and there's a serial bench that
proves them and measures the gain shift. Buffering, upload, and power sensing are still stubs.

Nothing here has run on hardware yet — no numbers have been measured.

## Framework

Arduino core for ESP32 **v3.x**, which is built on ESP-IDF 5.x. That means raw IDF calls
(`i2s_channel_read()`, `esp_wifi_stop()`, light sleep) are available directly from this code, so
using Arduino for speed doesn't close any doors. Use `driver/i2s_std.h`, **not** the deprecated
`driver/i2s.h`.

## Layout

```
recordme/
  recordme.ino        state machine and lifecycle
  audio.h/.cpp        I2S capture from the INMP441 — implemented
  bench.h/.cpp        step-1 serial bench: PSRAM check, level meter, PCM dump
  config.h            pins, audio format, buffer sizes, thresholds
  secrets.h.example   copy to secrets.h (gitignored) and fill in
  partitions.csv      8 MB layout: 3 MB app, 4.875 MB LittleFS, no OTA
tools/
  serial_to_wav.py    turn a captured serial PCM dump into a WAV (stdlib only)
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

`cp recordme/secrets.h.example recordme/secrets.h` first or the compile fails on the include, even
though the bench never uses the Wi-Fi settings.

Verified compiling with **arduino-cli 1.5.2 and esp32 core 3.3.11**. There's no Homebrew on this
machine, so arduino-cli came from the official tarball into `~/.local/bin`:

```bash
curl -fsSL -o /tmp/arduino-cli.tar.gz \
  https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_macOS_ARM64.tar.gz
tar -xzf /tmp/arduino-cli.tar.gz -C ~/.local/bin arduino-cli
```

PSRAM must be enabled or the buffer design doesn't work — the bench prints `ESP.getPsramSize()` at
boot and pattern-tests a real 6 MB allocation, so check that line before trusting anything else. If
the board doesn't enumerate, hold BOOT while plugging in to enter download mode.

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

## Step 1: the audio bench

`BENCH_AUDIO` in `config.h` is `1`, so the sketch skips the state machine and runs the bench:
PSRAM check at boot, then one level line per second. Wire only the mic, power from USB — no
battery, no case (`hardware/WIRING.md`).

```
t=  12.0s slot=L shift=11  rms L=   512 R=     2  peak= -14.2 dBFS  clips=0  dc=   -13  [#########...............]  suggest shift=13
```

| key | effect |
|---|---|
| `+` / `-` | louder / quieter — `SAMPLE_SHIFT` down / up, 6 dB a step |
| `l` / `r` | take the left / right slot of the I2S frame |
| `d` | toggle raw PCM dump (auto-stops after `BENCH_DUMP_MAX_SECONDS`) |
| `z` | reset stats · `s` status · `?` help |

### Finding SAMPLE_SHIFT

This is the whole point of step 1, and the one firmware value that can't be derived — it depends on
the mic's real sensitivity and on how far your mouth ends up from it.

1. Tap the mic body. The bar should jump. If nothing moves, check the `s` line's pins, 3V3, and GND.
2. Check which slot the audio is on. `rms L` and `rms R` are both metered; the mic should be on
   **L** with `L/R` tied to GND, but the driver has lied about this before, so the bench says
   `<- signal is on R, press r` if it sees otherwise. Whichever one wins goes in `AUDIO_SLOT`.
3. Speak normally at the distance you'll actually wear it. Read `suggest shift` — it's computed
   from the loudest sample seen and targets peaks at −6 dBFS, leaving headroom for a laugh.
4. Set it with `+`/`-`, then talk for a while and confirm `peak` sits near −6 dBFS with `clips=0`.
   Speak up deliberately; a few clips on a shout is fine, clips during normal speech is not.
5. Put the value you settled on into `SAMPLE_SHIFT` in `config.h` and log it in `BUILD_LOG.md`.

Reference points: `shift=16` is unity gain (the top 16 bits of the mic's 24-bit sample), lower is
louder. `dc` should be small; a large constant offset means a wiring or clock problem, not speech.

### Listening to it

Levels tell you the gain is right; only your ears tell you it sounds like a voice.

The awkward part is that the capture has to be running *before* the dump starts, so there's nowhere
to type `d`. Two ways round it — the second one always works:

```bash
# A: capture in one terminal, trigger from another (use /dev/cu.*, not /dev/tty.*)
cat /dev/cu.usbmodem* > dump.bin
printf d > /dev/cu.usbmodem*           # again to stop, or it auto-stops

# B: set BENCH_DUMP_ON_BOOT to 1 in config.h, reflash, then
cat /dev/cu.usbmodem* > dump.bin       # ...and press RESET on the board

python3 tools/serial_to_wav.py dump.bin out.wav   # Ctrl-C the cat first
```

Neither path has been tried on hardware yet. If A resets the board when the second process opens
the port (USB-Serial-JTAG does reset on some DTR/RTS sequences), use B. Don't pipe the dump through
`arduino-cli monitor` — it isn't a binary-safe pipe.

The dump is 32 KB/s over USB CDC. If the host can't keep up, the converter's WAV gets dropouts and
the firmware says so at `d`-stop (`ran at 0.94x realtime`) — those clicks are USB, not the mic. The
level meter is the authoritative measurement; the WAV is for judging whether it sounds right.
