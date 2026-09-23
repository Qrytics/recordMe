#include "bench.h"
#include "audio.h"
#include "config.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <math.h>

// ---------------------------------------------------------------- PSRAM

bool psram_check() {
  const size_t total = ESP.getPsramSize();
  const size_t freeb = ESP.getFreePsram();
  Serial.printf("psram: total=%u bytes (%.2f MB)  free=%u bytes (%.2f MB)\n",
                (unsigned)total, total / 1048576.0f, (unsigned)freeb, freeb / 1048576.0f);

  if (total == 0) {
    Serial.println("psram: NOT DETECTED. The PSRAM ring buffer is the whole buffering");
    Serial.println("psram: design (CLAUDE.md section 7) - flash holds only ~2 min. Build with");
    Serial.println("psram: --build-property build.psram_type=opi and check the board choice.");
    return false;
  }

  // Allocate the real design figure, not a token block: the question is whether 6 MB
  // can actually be had, and whether it survives a write/read round trip.
  const size_t want = PSRAM_BUFFER_BYTES;
  uint32_t* buf = (uint32_t*)heap_caps_malloc(want, MALLOC_CAP_SPIRAM);
  if (!buf) {
    Serial.printf("psram: FAILED to allocate PSRAM_BUFFER_BYTES=%u (%.2f MB)\n",
                  (unsigned)want, want / 1048576.0f);
    return false;
  }

  const size_t words = want / sizeof(uint32_t);
  const uint32_t seed = 0x9E3779B9u;  // any odd constant; pattern just has to vary

  const uint32_t t0 = millis();
  for (size_t i = 0; i < words; i++) buf[i] = (uint32_t)i * seed;
  const uint32_t t1 = millis();

  size_t bad = 0;
  for (size_t i = 0; i < words; i++) {
    if (buf[i] != (uint32_t)i * seed && ++bad <= 3) {
      Serial.printf("psram: mismatch at word %u\n", (unsigned)i);
    }
  }
  const uint32_t t2 = millis();
  heap_caps_free(buf);

  const float mb = want / 1048576.0f;
  Serial.printf("psram: wrote %.2f MB in %u ms (%.1f MB/s), verified in %u ms, %u bad words\n",
                mb, (unsigned)(t1 - t0), (t1 > t0) ? mb * 1000.0f / (t1 - t0) : 0.0f,
                (unsigned)(t2 - t1), (unsigned)bad);

  // ~6 MB at 32 KB/s. This is the number that decides upload cadence.
  Serial.printf("psram: %.2f MB of buffer = %.1f s of 16k/16-bit mono audio\n",
                mb, (float)want / BYTES_PER_SECOND);
  return bad == 0;
}

// ---------------------------------------------------------------- state

static bool     dump_mode   = false;
static uint32_t dump_start_ms = 0;
static uint32_t dump_samples  = 0;
static uint32_t last_report_ms = 0;
static bool     audio_ok    = false;
static uint32_t last_retry_ms = 0;

static int16_t  pcm[AUDIO_READ_FRAMES];

static const char* SLOT_NAME[2] = { "L", "R" };

static int bitlen(uint32_t v) { return v ? 32 - __builtin_clz(v) : 0; }

// The shift that would put the loudest sample seen near -6 dBFS.
//   out = raw24 >> (shift - 8), target out = 2^14  =>  shift = bitlen(peak) - 7
static int suggested_shift(uint32_t peak_raw24) {
  if (peak_raw24 == 0) return -1;
  int s = bitlen(peak_raw24) - 7;
  if (s < AUDIO_SHIFT_MIN) s = AUDIO_SHIFT_MIN;
  if (s > AUDIO_SHIFT_MAX) s = AUDIO_SHIFT_MAX;
  return s;
}

// raw 24-bit magnitude -> the 16-bit value it becomes at the current shift
static float to_out(float raw24, int shift) { return ldexpf(raw24, 8 - shift); }

static void print_help() {
  Serial.println();
  Serial.println("keys:  + louder (shift down, +6 dB)   - quieter (shift up, -6 dB)");
  Serial.println("       l use left slot   r use right slot");
  Serial.println("       d toggle raw PCM dump    z reset stats    s status    ? help");
  Serial.println();
  Serial.println("tuning SAMPLE_SHIFT: speak at your real mouth-to-mic distance, watch");
  Serial.println("'suggest', set it with +/-, then confirm peak sits near -6 dBFS with");
  Serial.println("clips=0 when you speak up. Put the winning value in config.h.");
  Serial.println();
}

static void print_status() {
  Serial.printf("status: slot=%s shift=%d  sck=%d ws=%d sd=%d  bclk=%u Hz  rate=%d Hz\n",
                SLOT_NAME[audio_slot()], audio_shift(), PIN_I2S_SCK, PIN_I2S_WS, PIN_I2S_SD,
                (unsigned)audio_bclk_hz(), SAMPLE_RATE_HZ);
  const AudioStats& s = audio_stats();
  Serial.printf("status: frames=%u clips=%u read_errors=%u timeouts=%u  heap=%u\n",
                (unsigned)s.frames, (unsigned)s.clips, (unsigned)s.read_errors,
                (unsigned)s.timeouts, (unsigned)ESP.getFreeHeap());
}

// ---------------------------------------------------------------- dump mode

// 16-byte header so firmware/tools/serial_to_wav.py can find the audio in a capture
// that also contains boot messages: "RECPCM16", rate, bits, channels, shift.
static void dump_begin() {
  uint8_t hdr[16] = { 'R', 'E', 'C', 'P', 'C', 'M', '1', '6' };
  const uint32_t rate = SAMPLE_RATE_HZ;
  hdr[8]  = rate & 0xFF;  hdr[9]  = (rate >> 8) & 0xFF;
  hdr[10] = (rate >> 16) & 0xFF; hdr[11] = (rate >> 24) & 0xFF;
  hdr[12] = SAMPLE_BITS & 0xFF; hdr[13] = (SAMPLE_BITS >> 8) & 0xFF;
  hdr[14] = AUDIO_CHANNELS;
  hdr[15] = (uint8_t)audio_shift();

  audio_reset_stats();
  dump_samples  = 0;
  dump_start_ms = millis();
  dump_mode     = true;
  Serial.write(hdr, sizeof(hdr));
}

static void dump_end() {
  dump_mode = false;
  Serial.write((const uint8_t*)"ENDPCM16", 8);
  Serial.flush();

  const uint32_t ms = millis() - dump_start_ms;
  const float expected_ms = dump_samples * 1000.0f / SAMPLE_RATE_HZ;
  Serial.println();
  Serial.printf("dump: %u samples (%.2f s of audio) in %.2f s wall clock\n",
                (unsigned)dump_samples, expected_ms / 1000.0f, ms / 1000.0f);
  // Falling behind realtime means the host could not drain USB fast enough, so the
  // capture has gaps. The level meter, not the WAV, is the reliable measurement.
  if (ms > 0 && expected_ms < ms * 0.99f) {
    Serial.printf("dump: ran at %.2fx realtime - the WAV likely has dropouts. Clicks in\n",
                  expected_ms / ms);
    Serial.println("dump: the file are probably USB stalls, not the mic.");
  }
  audio_reset_stats();
  last_report_ms = millis();
}

// ---------------------------------------------------------------- lifecycle

void bench_setup() {
  Serial.println();
  Serial.println("=== recordMe step-1 audio bench ===");
  Serial.printf("audio: %d Hz, %d-bit samples in %d-bit slots, %d ch, %d bytes/s\n",
                SAMPLE_RATE_HZ, SAMPLE_BITS, I2S_SLOT_BITS, AUDIO_CHANNELS, BYTES_PER_SECOND);

  audio_ok = audio_init();
  if (audio_ok) {
    Serial.printf("audio: I2S up. sck=%d ws=%d sd=%d, bclk=%u Hz (INMP441 wants 0.512-4.096 MHz)\n",
                  PIN_I2S_SCK, PIN_I2S_WS, PIN_I2S_SD, (unsigned)audio_bclk_hz());
    Serial.printf("audio: starting shift=%d, slot=%s\n", audio_shift(), SLOT_NAME[audio_slot()]);
  } else {
    Serial.println("audio: I2S FAILED to start - retrying every 5 s.");
  }
  print_help();
  last_report_ms = millis();

#if BENCH_DUMP_ON_BOOT
  if (audio_ok) {
    Serial.println("dump: BENCH_DUMP_ON_BOOT is set - dumping raw PCM now.");
    Serial.flush();
    delay(50);
    dump_begin();
  }
#endif
}

static void handle_key(int c) {
  switch (c) {
    case '+': case '=':
      audio_set_shift(audio_shift() - 1);
      Serial.printf("\nshift=%d (louder)\n", audio_shift());
      audio_reset_stats();
      break;
    case '-': case '_':
      audio_set_shift(audio_shift() + 1);
      Serial.printf("\nshift=%d (quieter)\n", audio_shift());
      audio_reset_stats();
      break;
    case 'l': case 'L':
      audio_set_slot(0); audio_reset_stats();
      Serial.println("\nslot=L");
      break;
    case 'r': case 'R':
      audio_set_slot(1); audio_reset_stats();
      Serial.println("\nslot=R");
      break;
    case 'd': case 'D':
      if (dump_mode) dump_end();
      else {
        Serial.printf("\ndump: raw PCM for up to %d s. Capture it, then:\n",
                      BENCH_DUMP_MAX_SECONDS);
        Serial.println("dump:   python3 firmware/tools/serial_to_wav.py dump.bin out.wav");
        Serial.flush();
        delay(50);  // let the text leave before binary starts
        dump_begin();
      }
      break;
    case 'z': case 'Z':
      audio_reset_stats();
      Serial.println("\nstats reset");
      break;
    case 's': case 'S': Serial.println(); print_status(); break;
    case '?': case 'h': case 'H': print_help(); break;
    default: break;
  }
}

static void report_levels() {
  const AudioStats& s = audio_stats();
  const int slot  = audio_slot();
  const int other = slot ^ 1;
  const int shift = audio_shift();

  if (s.frames == 0) {
    Serial.printf("t=%6.1fs NO DATA: %u timeouts, %u read errors. Check 3V3/GND and that\n",
                  millis() / 1000.0f, (unsigned)s.timeouts, (unsigned)s.read_errors);
    Serial.println("         SCK/WS/SD go to the pins in the status line ('s').");
    audio_reset_stats();
    return;
  }

  // Measure in the raw 24-bit domain, display in the output domain. Everything on the
  // line is then in the same units as the 16-bit samples that reach the WAV, while the
  // comparisons below stay independent of the current shift.
  const float raw_sel   = sqrtf((float)((double)s.sumsq_raw24[slot] / s.frames));
  const float raw_other = sqrtf((float)((double)s.sumsq_raw24[other] / s.frames));
  const float rms_sel   = to_out(raw_sel, shift);
  const float rms_other = to_out(raw_other, shift);
  const float peak      = to_out((float)s.peak_raw24[slot], shift);
  const float dbfs      = peak > 0.0f ? 20.0f * log10f(peak / 32768.0f) : -120.0f;
  const long  dc        = (long)to_out((float)(s.sum_raw24[slot] / (int64_t)s.frames), shift);

  // -60..0 dBFS across 24 characters
  char bar[25];
  int filled = (int)((dbfs + 60.0f) * 24.0f / 60.0f + 0.5f);
  if (filled < 0) filled = 0;
  if (filled > 24) filled = 24;
  for (int i = 0; i < 24; i++) bar[i] = (i < filled) ? '#' : '.';
  bar[24] = '\0';

  Serial.printf("t=%6.1fs slot=%s shift=%2d  rms %s=%6.0f %s=%6.0f  peak=%6.1f dBFS  clips=%u  dc=%6ld  [%s]",
                millis() / 1000.0f, SLOT_NAME[slot], shift,
                SLOT_NAME[slot], rms_sel, SLOT_NAME[other], rms_other,
                dbfs, (unsigned)s.clips, dc, bar);

  const int suggest = suggested_shift(s.peak_raw24[slot]);
  if (suggest >= 0) Serial.printf("  suggest shift=%d", suggest);
  if (s.clips > 0)  Serial.print("  CLIPPING");
  // The documented gotcha, caught automatically: if the quiet slot is the one we are
  // recording, the mono slot_mask lied and the mic is on the other one.
  if (raw_other > raw_sel * 4.0f + 2000.0f) {
    Serial.printf("  <- signal is on %s, press %c", SLOT_NAME[other], other ? 'r' : 'l');
  } else if (s.peak_raw24[0] == 0 && s.peak_raw24[1] == 0) {
    Serial.print("  both slots dead - SD pin or mic power?");
  }
  Serial.println();

  audio_reset_stats();
}

void bench_loop() {
  while (Serial.available()) handle_key(Serial.read());

  if (!audio_ok) {
    if (millis() - last_retry_ms > 5000) {
      last_retry_ms = millis();
      audio_ok = audio_init();
      if (audio_ok) Serial.println("audio: I2S up.");
    }
    return;
  }

  if (dump_mode) {
    const size_t n = audio_read(pcm, AUDIO_READ_FRAMES);
    if (n) {
      Serial.write((const uint8_t*)pcm, n * sizeof(int16_t));
      dump_samples += n;
    }
    if (millis() - dump_start_ms > (uint32_t)BENCH_DUMP_MAX_SECONDS * 1000) dump_end();
    return;
  }

  // dst = NULL: meter only. This still drains the DMA buffers, which is what keeps the
  // statistics continuous rather than a sample of whatever was stale in the ring.
  audio_read(NULL, AUDIO_READ_FRAMES);

  if (millis() - last_report_ms >= BENCH_REPORT_MS) {
    last_report_ms += BENCH_REPORT_MS;
    report_levels();
  }
}
