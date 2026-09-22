"""Transcription worker: pending chunks -> VAD -> Whisper -> DB.

SCAFFOLD — structure and contracts only.

Runs as a long-lived service (systemd), polling for pending chunks. Load the model
once at startup, never per chunk.

Throughput context: a day of wear is 8 h of wall time but only ~50-100 min of
actual speech once VAD has cut the silence, which a Pi 5 clears comfortably. That
headroom exists *because* of VAD — without it this would be transcribing hours of
near-silence daily. See docs/PLAN_REVIEW.md section 3.1.
"""


def load_model(model_name: str, compute_type: str, threads: int):
    """Load faster-whisper once, at startup.

    int8 on CPU for a Pi 5. The Pi has an active cooler, so sustained multi-core
    load won't throttle.
    """
    raise NotImplementedError  # TODO


def transcribe_chunk(model, audio_path, vad_enabled: bool) -> dict:
    """Transcribe one chunk. Returns text, segments, language, and speech seconds.

    Use faster-whisper's built-in Silero VAD (`vad_filter=True`). Beyond saving
    time, it stops Whisper hallucinating text over silence — a real failure mode
    for always-on recording, where most audio has no speech in it.

    A chunk with no speech at all should be marked 'silent', not 'failed'.
    """
    raise NotImplementedError  # TODO


def run_forever():
    """Claim pending chunks and transcribe them, oldest first.

    On failure: mark 'failed' with the error text and move on. One bad chunk must
    never wedge the queue.
    """
    raise NotImplementedError  # TODO


if __name__ == "__main__":
    run_forever()
