// Step-1 bench harness: prove I2S capture over USB serial and measure SAMPLE_SHIFT.
//
// The point of doing this over serial with runtime controls is that the gain shift is
// the firmware's riskiest unknown (docs/PLAN_REVIEW.md section 8.1, hardware/WIRING.md).
// Reflashing once per guess is a slow way to find it; this way you wire it up, flash
// once, and read numbers off a level meter that tells you which shift to use.
//
// Enabled by BENCH_AUDIO in config.h. Usage is in firmware/README.md.
#pragma once

// Reports PSRAM size and free space, then allocates PSRAM_BUFFER_BYTES for real and
// pattern-tests it, because "PSRAM is enabled" is an assumption the whole buffering
// design rests on. Returns false if PSRAM is missing or fails the test.
bool psram_check();

void bench_setup();
void bench_loop();
