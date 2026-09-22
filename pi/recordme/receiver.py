"""Upload receiver. LAN-only — the device never uses the remote-access tunnel.

SCAFFOLD — routes and contracts only.

Protocol: raw binary POST (not multipart, to keep the ESP32 side cheap).

    POST /upload?boot_id=<hex>&seq=<n>&chunk_start_ms=<n>&device_now_ms=<n>&sample_rate=16000
    Authorization: Bearer <shared secret>
    Content-Type: application/octet-stream
    <raw PCM body>

Responds 200 with the stored chunk id. The device deletes its local copy ONLY on
that 200 — ACK first, delete second, never the reverse.
"""

from fastapi import FastAPI

app = FastAPI(title="recordMe receiver")


@app.post("/upload")
async def upload():
    """Accept one audio chunk, write the WAV to the HDD, queue it for transcription.

    Steps:
      1. Check the bearer token (constant-time compare).
      2. Compute wall-clock start: received_at - (device_now_ms - chunk_start_ms).
         The device has no RTC, so the Pi is the only source of absolute time.
      3. Write the WAV under audio_dir, sharded by date so directories stay small.
      4. Insert the chunk row as 'pending'. UNIQUE(boot_id, seq) makes a re-sent
         chunk a no-op rather than a duplicate — the device retries when an ACK is
         lost, and that must be safe.
      5. Run gap detection for this boot_id.
      6. Return the chunk id.

    Only return 200 once the bytes are durably on disk AND the row is committed.
    A premature ACK makes the device delete audio that was never stored.
    """
    raise NotImplementedError  # TODO


@app.get("/health")
async def health():
    """Liveness plus queue depth, so a growing backlog is visible."""
    raise NotImplementedError  # TODO
