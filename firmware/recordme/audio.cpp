#include "audio.h"
#include "config.h"

#include <driver/i2s_std.h>
#include <esp_err.h>
#include <math.h>

static i2s_chan_handle_t rx_chan = NULL;
static int         g_shift = SAMPLE_SHIFT;
static int         g_slot  = AUDIO_SLOT;
static uint32_t    g_bclk_hz = 0;
static AudioStats  g_stats;

// One I2S read's worth of stereo 32-bit frames. Static and in internal RAM on purpose:
// DMA cannot write into PSRAM, so this buffer must not move there.
static int32_t scratch[AUDIO_READ_FRAMES * 2];

bool audio_init() {
  if (rx_chan) return true;

  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  // Assigned rather than set in the initializer so this survives the field churn
  // between IDF versions (auto_clear became two fields in 5.3, for instance).
  chan_cfg.dma_desc_num  = I2S_DMA_DESC_NUM;
  chan_cfg.dma_frame_num = I2S_DMA_FRAME_NUM;

  esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &rx_chan);
  if (err != ESP_OK) {
    Serial.printf("audio_init: i2s_new_channel failed: %s\n", esp_err_to_name(err));
    rx_chan = NULL;
    return false;
  }

  // 32-bit slots in a stereo frame. Two reasons, both load-bearing:
  //   * SCK lands at 16000 * 32 * 2 = 1.024 MHz, inside the INMP441's 512 kHz-4.096 MHz
  //     window. 16-bit slots would sit exactly on the 512 kHz minimum.
  //   * Capturing both slots and selecting one in software avoids trusting
  //     I2S_SLOT_MODE_MONO's slot_mask, which has returned the wrong channel on some IDF
  //     versions (CLAUDE.md section 8). The cost is 128 KB/s of DMA, which is nothing.
  i2s_std_config_t std_cfg = {
      .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE_HZ),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                      I2S_SLOT_MODE_STEREO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,        // the INMP441 makes its own clock from SCK
          .bclk = (gpio_num_t)PIN_I2S_SCK,
          .ws   = (gpio_num_t)PIN_I2S_WS,
          .dout = I2S_GPIO_UNUSED,        // RX only, the mic never listens
          .din  = (gpio_num_t)PIN_I2S_SD,
          .invert_flags = {
              .mclk_inv = false,
              .bclk_inv = false,
              .ws_inv   = false,
          },
      },
  };

  err = i2s_channel_init_std_mode(rx_chan, &std_cfg);
  if (err != ESP_OK) {
    Serial.printf("audio_init: init_std_mode failed: %s\n", esp_err_to_name(err));
    audio_deinit();
    return false;
  }

  i2s_chan_info_t info;
  if (i2s_channel_get_info(rx_chan, &info) == ESP_OK) g_bclk_hz = info.bclk_hz;

  err = i2s_channel_enable(rx_chan);
  if (err != ESP_OK) {
    Serial.printf("audio_init: enable failed: %s\n", esp_err_to_name(err));
    audio_deinit();
    return false;
  }

  audio_reset_stats();
  return true;
}

void audio_deinit() {
  if (!rx_chan) return;
  i2s_channel_disable(rx_chan);
  i2s_del_channel(rx_chan);
  rx_chan = NULL;
}

size_t audio_read(int16_t* dst, size_t max_samples) {
  if (!rx_chan || max_samples == 0) return 0;

  size_t written = 0;
  while (written < max_samples) {
    size_t want_frames = max_samples - written;
    if (want_frames > AUDIO_READ_FRAMES) want_frames = AUDIO_READ_FRAMES;

    size_t got_bytes = 0;
    esp_err_t err = i2s_channel_read(rx_chan, scratch, want_frames * 2 * sizeof(int32_t),
                                     &got_bytes, I2S_READ_TIMEOUT_MS);
    if (err == ESP_ERR_TIMEOUT) { g_stats.timeouts++; break; }
    if (err != ESP_OK)          { g_stats.read_errors++; break; }

    const size_t frames = got_bytes / (2 * sizeof(int32_t));
    if (frames == 0) break;

    for (size_t i = 0; i < frames; i++) {
      // The INMP441 puts its 24-bit sample in bits 31..8, so >> 8 recovers the signed
      // 24-bit value. Kept shift-independent so the statistics stay valid when the gain
      // is retuned mid-session. (>> on a negative int32 is arithmetic on GCC.)
      const int32_t word[2] = { scratch[2 * i], scratch[2 * i + 1] };

      for (int s = 0; s < 2; s++) {
        const int32_t raw24 = word[s] >> 8;
        const uint32_t mag  = (uint32_t)(raw24 < 0 ? -raw24 : raw24);
        if (mag > g_stats.peak_raw24[s]) g_stats.peak_raw24[s] = mag;
        g_stats.sumsq_raw24[s] += (uint64_t)((int64_t)raw24 * (int64_t)raw24);
        g_stats.sum_raw24[s]   += raw24;
      }

      // Saturate rather than wrap. A wrapped overload inverts the waveform and sounds
      // like a different, louder sound; a clipped one just sounds clipped, and the
      // counter tells us it happened.
      int32_t out = word[g_slot] >> g_shift;
      if (out > 32767)       { out = 32767;  g_stats.clips++; }
      else if (out < -32768) { out = -32768; g_stats.clips++; }
      if (dst) dst[written] = (int16_t)out;
      written++;
    }
    g_stats.frames += frames;
  }
  return written;
}

void audio_set_shift(int shift) {
  if (shift < AUDIO_SHIFT_MIN) shift = AUDIO_SHIFT_MIN;
  if (shift > AUDIO_SHIFT_MAX) shift = AUDIO_SHIFT_MAX;
  g_shift = shift;
}

int audio_shift() { return g_shift; }

void audio_set_slot(int slot) { g_slot = (slot == 1) ? 1 : 0; }
int  audio_slot()             { return g_slot; }

uint32_t audio_bclk_hz() { return g_bclk_hz; }

const AudioStats& audio_stats() { return g_stats; }

void audio_reset_stats() { g_stats = AudioStats(); }
