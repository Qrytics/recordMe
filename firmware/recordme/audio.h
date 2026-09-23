// I2S capture from the INMP441. Step 1 of the build order (CLAUDE.md section 11).
//
// The mic is a 24-bit part that sits in a 32-bit I2S slot, left-justified, and it is
// quiet: samples need a right shift plus gain before they are usable 16-bit PCM. That
// shift (SAMPLE_SHIFT in config.h) is the riskiest unknown in the firmware, so this
// layer keeps running statistics in the *raw* 24-bit domain — independent of the shift —
// which lets the bench compute the correct shift instead of guessing at it.
#pragma once

#include <Arduino.h>
#include <stdint.h>
#include <stddef.h>

// Statistics accumulated by audio_read(), covering both slots of the stereo frame.
// Index 0 is the left slot, 1 is the right. Metering both is how we settle empirically
// which slot the mic is actually on (CLAUDE.md section 8 warns the enum has lied).
struct AudioStats {
  uint32_t frames;          // frames accumulated since the last reset
  uint32_t peak_raw24[2];   // largest |sample| seen, 24-bit domain (0 .. 8388607)
  uint64_t sumsq_raw24[2];  // sum of squares, for RMS. Wraps after ~16 s of full-scale
                            // audio, so reset it at least that often (the bench does it
                            // every BENCH_REPORT_MS).
  int64_t  sum_raw24[2];    // sum, for DC offset
  uint32_t clips;           // samples that saturated int16 after the shift
  uint32_t read_errors;     // i2s_channel_read() returned an error
  uint32_t timeouts;        // i2s_channel_read() returned no data in time
};

// Brings up I2S in standard (Philips) mode: 32-bit slots, stereo frame, master, RX
// only, pins from config.h. Prints the reason and returns false on failure — a silent
// failure here means silently recording nothing.
bool audio_init();
void audio_deinit();

// Reads up to max_samples of the selected slot into dst as 16-bit PCM, applying the
// current shift and saturating rather than wrapping. Returns samples written.
//
// dst may be NULL to meter without storing — that is how the bench's level mode drains
// the DMA buffers while printing numbers.
size_t audio_read(int16_t* dst, size_t max_samples);

// Live controls, so the bench can tune gain and swap slots without a reflash.
void     audio_set_shift(int shift);   // clamped to [AUDIO_SHIFT_MIN, AUDIO_SHIFT_MAX]
int      audio_shift();
void     audio_set_slot(int slot);     // 0 = left, 1 = right
int      audio_slot();
uint32_t audio_bclk_hz();              // what the driver actually programmed

const AudioStats& audio_stats();
void              audio_reset_stats();

// out = word32 >> shift, so 16 is unity and each step down is +6 dB. Below 4 the
// 24-bit sample cannot fit in int16 at all and everything clips; above 24 the signal
// is shifted into nothing.
#define AUDIO_SHIFT_MIN 4
#define AUDIO_SHIFT_MAX 24
