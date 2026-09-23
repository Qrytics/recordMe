// recordMe — wearable voice recorder firmware.
//
// Step 1 (I2S capture) is implemented: audio.cpp has audio_init()/audio_read(), and
// bench.cpp has the serial bench that proves them and measures SAMPLE_SHIFT. With
// BENCH_AUDIO set in config.h the sketch runs that bench instead of the state machine.
//
// Everything below is still SCAFFOLD: the state machine is laid out but buffering,
// upload, power sensing and the LED are stubs (steps 2-3, CLAUDE.md section 11).
//
// State machine:
//
//   boot
//    |
//    +-- VBUS present? --yes--> CHARGING: no recording, flush backlog over Wi-Fi
//    |                          (mains power is free, so drain everything)
//    no
//    |
//    v
//   RECORDING: I2S -> energy VAD -> PSRAM ring buffer -> chunks
//    |         Wi-Fi radio OFF
//    |
//    +-- buffer full or UPLOAD_EVERY_SECONDS elapsed --> UPLOADING
//    +-- VBUS appears --> finish current chunk, then CHARGING
//    +-- battery critical --> flush, then PARKED
//
//   UPLOADING: Wi-Fi up, POST chunks, wait for ACK, free them, Wi-Fi down.
//              On failure: back off, spill to LittleFS, stay in RECORDING.
//
//   Switch OFF cuts power outright. Un-ACKed chunks in flash survive and
//   re-upload on next boot.

#include "config.h"
#include "secrets.h"
#include "audio.h"
#include "bench.h"

enum State { BOOTING, RECORDING, UPLOADING, CHARGING, PARKED };
static State state = BOOTING;

// Identifies this power-on. The device has no RTC, so absolute time is unknown;
// the Pi assigns wall-clock timestamps from boot_id + ms-since-boot at receipt.
static char     boot_id[17];
static uint32_t chunk_seq = 0;

// ---------------------------------------------------------------- stubs

void led_init()    { /* TODO */ }
void led_pattern(State s) { (void)s; /* TODO: dim, cheap — power matters */ }

// audio_init() and audio_read() are implemented in audio.cpp. psram_check() and the
// step-1 serial bench live in bench.cpp.

bool  psram_buffer_init() { return false; }  // TODO step 3: the ring buffer itself
bool  vad_is_speech(const int16_t* samples, size_t n) { (void)samples; (void)n; return true; }  // TODO

// TODO: scan LittleFS on boot for half-written chunks left by a mid-write power
// cut, and either salvage or discard them before uploading anything.
bool  storage_init() { return false; }
bool  spill_chunk_to_flash() { return false; }

bool  wifi_up()   { return false; }  // TODO
void  wifi_down() { /* TODO: fully off, not just idle — this is the power budget */ }
bool  upload_pending_chunks() { return false; }  // TODO: ACK-then-delete, never delete first

uint32_t read_vbat_mv() { return 0; }  // TODO: ADC1, curve-fit calibration, trim vs multimeter
uint32_t read_vbus_mv() { return 0; }  // TODO: same, on the 5V pad divider
bool  is_charging()     { return read_vbus_mv() > VBUS_PRESENT_MV; }

void generate_boot_id(char* out) { (void)out; /* TODO: random hex, unique per power-on */ }

// ---------------------------------------------------------------- lifecycle

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);  // USB CDC needs a moment before the first line is visible
  led_init();
  generate_boot_id(boot_id);

  Serial.printf("\nrecordMe boot: %s, %d MHz, flash %u MB\n",
                ESP.getChipModel(), (int)getCpuFrequencyMhz(),
                (unsigned)(ESP.getFlashChipSize() / 1048576));
  psram_check();  // the buffering design assumes ~8 MB; confirm it rather than assume it

#if BENCH_AUDIO
  // Step 1: no state machine, no battery, no Wi-Fi — just prove I2S capture and find
  // SAMPLE_SHIFT. Set BENCH_AUDIO to 0 in config.h to run the real firmware.
  bench_setup();
  return;
#endif

  // TODO: bail loudly (distinct LED pattern) if any of these fail — silent
  // failure here means silently losing audio, which the spec forbids.
  psram_buffer_init();
  storage_init();
  audio_init();

  state = is_charging() ? CHARGING : RECORDING;
}

void loop() {
#if BENCH_AUDIO
  bench_loop();
  return;
#endif

  led_pattern(state);

  switch (state) {
    case RECORDING:
      // TODO: read I2S -> VAD gate -> append to PSRAM ring -> close chunks at
      // CHUNK_SECONDS. Transition to UPLOADING when the buffer fills or
      // UPLOAD_EVERY_SECONDS elapses.
      break;

    case UPLOADING:
      // TODO: wifi_up(), upload_pending_chunks(), wifi_down(). On failure back
      // off and spill to flash. When flash is also full: drop oldest first and
      // signal it — the Pi detects the sequence-number gap and records it.
      break;

    case CHARGING:
      // TODO: don't record. Use the free power to flush the entire backlog.
      break;

    case PARKED:
      // TODO: battery critical. Everything flushed; wait for the switch.
      break;

    case BOOTING:
    default:
      break;
  }
}
