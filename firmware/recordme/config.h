// Tunable constants. Values here encode decisions already made — see CLAUDE.md
// section 5 for the reasoning behind each. Anything marked TUNE is expected to
// change once measured on real hardware.
#pragma once

// ---------------------------------------------------------------- pins
// Both sense pins are on ADC1. This is not arbitrary: ADC2 is unusable while
// Wi-Fi is active on ESP32, which would break battery readings during uploads.
#define PIN_I2S_SCK   4    // D2  -> INMP441 SCK  (bit clock)
#define PIN_I2S_WS    5    // D3  -> INMP441 WS   (word select)
#define PIN_I2S_SD    6    // D4  -> INMP441 SD   (data out, mic -> MCU)
#define PIN_VBAT_SENSE 2   // D0/A0, ADC1_CH1 — 100k/100k divider from BAT+
#define PIN_VBUS_SENSE 3   // D1/A1, ADC1_CH2 — 100k/100k divider from the 5V pad
#define PIN_LED       21   // onboard user LED, active LOW — VERIFY on the board

// The on/off switch is a hard power cut in the battery line. It is not wired to
// any GPIO and the firmware cannot read it.

// ---------------------------------------------------------------- audio
#define SAMPLE_RATE_HZ   16000  // Whisper's native rate, so the Pi never resamples
#define SAMPLE_BITS      16     // what we store
#define I2S_SLOT_BITS    32     // what the INMP441 sends; see note below
#define AUDIO_CHANNELS   1      // mono, left slot (INMP441 L/R tied to GND)

// Why 32-bit slots for 16-bit audio: the INMP441 needs SCK between 512 kHz and
// 4.096 MHz. 16000 * 32 * 2 = 1.024 MHz, comfortably inside. 16-bit slots would
// put SCK at exactly the 512 kHz minimum.
// The mic is 24-bit and quiet, so samples need a right-shift plus gain. Start
// here and tune against real speech — expect to adjust.
// SAMPLE_SHIFT is applied to the whole 32-bit I2S word: out = word >> SAMPLE_SHIFT.
// So 16 is unity gain (the top 16 bits of the 24-bit sample) and every step down is
// +6 dB. 11 is therefore 32x. It is only a starting guess — the bench prints the
// shift it measures as correct, and `+`/`-` over serial change it live.
#define SAMPLE_SHIFT     11     // TUNE: 32-bit word -> 16-bit sample
#define BYTES_PER_SECOND (SAMPLE_RATE_HZ * (SAMPLE_BITS / 8) * AUDIO_CHANNELS)  // 32000

// Which 32-bit slot of the stereo frame carries the mic. L/R tied to GND means
// left (0), but "left only" has historically returned the right channel on some
// IDF versions, so we capture both slots and pick here — and the bench meters both
// so the answer is measured, not assumed. `l`/`r` switch it live.
#define AUDIO_SLOT       0      // 0 = left, 1 = right
#define AUDIO_READ_FRAMES 512   // scratch frames per i2s_channel_read (4 KB, internal RAM)
#define I2S_READ_TIMEOUT_MS 200
#define I2S_DMA_DESC_NUM  8     // 8 x 256 frames = 128 ms of DMA cushion
#define I2S_DMA_FRAME_NUM 256

// ---------------------------------------------------------------- buffering
// Primary buffer is PSRAM. Flash (LittleFS, ~4.875 MB) is overflow only, for when
// the Pi is unreachable. See docs/PLAN_REVIEW.md section 7.
#define CHUNK_SECONDS        30      // TUNE: 30-60 s
#define PSRAM_BUFFER_BYTES   (6 * 1024 * 1024)  // leave headroom; 8 MB total
#define UPLOAD_EVERY_SECONDS 150     // TUNE: ~2.5 min, inside the PSRAM budget

// ---------------------------------------------------------------- VAD
// Cheap energy gate with hangover. Saves battery, Wi-Fi, and buffer space; the
// accurate Silero pass happens on the Pi.
#define VAD_ENABLED          1
#define VAD_RMS_THRESHOLD    500     // TUNE: measure your own noise floor first
#define VAD_HANGOVER_MS      1500    // keep recording this long after speech stops

// ---------------------------------------------------------------- power
#define VBAT_DIVIDER_RATIO   2.0f    // 100k/100k
#define VBAT_LOW_MV          3500    // warn
#define VBAT_CRITICAL_MV     3300    // stop recording, flush, park
#define VBUS_PRESENT_MV      4000    // above this on the 5V pad -> charging

// ---------------------------------------------------------------- bench (step 1)
// With BENCH_AUDIO set, the sketch skips the state machine and runs the serial audio
// bench instead: PSRAM check, live level meter, and raw PCM dump. Set it to 0 once
// step 1 is signed off and the recording path is being built.
#define BENCH_AUDIO             1
#define SERIAL_BAUD             115200  // ignored by USB CDC, which runs at USB speed
#define BENCH_REPORT_MS         1000    // one level line per second
#define BENCH_DUMP_MAX_SECONDS  120     // auto-stop a raw dump, so it can't run away
// Start dumping raw PCM immediately at boot. Set to 1 when capturing with a plain
// `cat > dump.bin`, which gives you nowhere to type `d` — start the capture, then press
// RESET on the board and the dump begins with the capture already running.
#define BENCH_DUMP_ON_BOOT      0

// ---------------------------------------------------------------- network
#define WIFI_CONNECT_TIMEOUT_MS  15000
#define UPLOAD_RETRY_BASE_MS     2000   // exponential backoff from here
#define UPLOAD_RETRY_MAX_MS      60000
#define HTTP_TIMEOUT_MS          20000
