"""Transcription worker: pending chunks -> VAD -> Whisper -> DB.

Runs as a long-lived service (systemd), polling for pending chunks. Load the model
once at startup, never per chunk.

Throughput context: a day of wear is 8 h of wall time but only ~50-100 min of
actual speech once VAD has cut the silence, which a Pi 5 clears comfortably. That
headroom exists *because* of VAD — without it this would be transcribing hours of
near-silence daily. See docs/PLAN_REVIEW.md section 3.1.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import db as dbmod
from .config import Config

log = logging.getLogger("recordme.transcribe")

POLL_INTERVAL_S = 5.0


def load_model(model_name: str, compute_type: str, threads: int):
    """Load faster-whisper once, at startup.

    int8 on CPU for a Pi 5. The Pi has an active cooler, so sustained multi-core
    load won't throttle.

    Imported lazily on purpose: faster-whisper pulls in CTranslate2 and onnxruntime,
    and the receiver, the search service, and the test suite all have no business
    requiring them.
    """
    from faster_whisper import WhisperModel

    log.info("loading %s (compute=%s, threads=%d)", model_name, compute_type, threads)
    return WhisperModel(
        model_name, device="cpu", compute_type=compute_type, cpu_threads=threads
    )


def transcribe_chunk(model, audio_path, vad_enabled: bool = True) -> Dict[str, Any]:
    """Transcribe one chunk. Returns text, segments, language, and speech seconds.

    Uses faster-whisper's built-in Silero VAD (`vad_filter=True`). Beyond saving
    time, it stops Whisper hallucinating text over silence — a real failure mode
    for always-on recording, where most audio has no speech in it.

    A chunk with no speech at all comes back with empty text; the caller marks it
    'silent' rather than 'failed'.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError("audio file missing: {}".format(path))

    segments, info = model.transcribe(
        str(path),
        vad_filter=vad_enabled,
        word_timestamps=True,
        beam_size=5,
    )

    # segments is a generator — consuming it is what actually runs the model.
    out = []
    for s in segments:
        text = (s.text or "").strip()
        if text:
            out.append({"start": float(s.start), "end": float(s.end), "text": text})

    speech_s = getattr(info, "duration_after_vad", None)
    if speech_s is None:
        speech_s = sum(s["end"] - s["start"] for s in out)

    return {
        "text": " ".join(s["text"] for s in out).strip(),
        "segments": out,
        "language": getattr(info, "language", None),
        "speech_s": float(speech_s),
    }


def process_one(
    conn: sqlite3.Connection,
    model,
    model_name: str,
    vad_enabled: bool = True,
) -> Optional[str]:
    """Claim and transcribe the oldest pending chunk.

    Returns the resulting status, or None if the queue was empty.

    Every failure path must leave the chunk in a terminal state. If an exception
    escaped here the chunk would stay 'transcribing' forever and the queue behind it
    would still drain, so the loss would be silent — which the spec forbids.
    """
    row = dbmod.claim_pending_chunk(conn)
    if row is None:
        return None

    chunk_id = row["id"]
    try:
        result = transcribe_chunk(model, row["audio_path"], vad_enabled)
    except Exception as exc:  # noqa: BLE001 - one bad chunk must not wedge the queue
        log.exception("chunk %s failed", chunk_id)
        dbmod.mark_failed(conn, chunk_id, "{}: {}".format(type(exc).__name__, exc))
        return "failed"

    if not result["text"]:
        log.info("chunk %s has no speech, marking silent", chunk_id)
        dbmod.mark_silent(conn, chunk_id)
        return "silent"

    dbmod.save_transcript(
        conn,
        chunk_id,
        result["text"],
        model=model_name,
        language=result.get("language"),
        speech_s=result.get("speech_s"),
        segments=result.get("segments") or [],
    )
    log.info(
        "chunk %s done: %d chars, %.1f s speech",
        chunk_id,
        len(result["text"]),
        result.get("speech_s") or 0.0,
    )
    return "done"


def drain(conn: sqlite3.Connection, model, model_name: str, vad_enabled: bool = True) -> int:
    """Process everything currently pending. Returns how many chunks were handled."""
    n = 0
    while process_one(conn, model, model_name, vad_enabled) is not None:
        n += 1
    return n


def run_forever(poll_interval: float = POLL_INTERVAL_S) -> None:
    """Claim pending chunks and transcribe them, oldest first.

    On failure: mark 'failed' with the error text and move on. One bad chunk must
    never wedge the queue.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = Config.load()
    conn = dbmod.connect(cfg.db_path)

    # A previous worker killed mid-chunk leaves its claim behind, and nothing else
    # would ever pick that chunk up again. Reclaim before doing anything else.
    requeued = dbmod.requeue_stale_transcribing(conn)
    if requeued:
        log.warning("requeued %d chunk(s) stranded by a previous worker", requeued)

    model_name = "faster-whisper/{}".format(cfg.whisper_model)
    model = load_model(cfg.whisper_model, cfg.whisper_compute, cfg.whisper_threads)
    log.info("worker ready (vad=%s)", cfg.vad_enabled)

    while True:
        if process_one(conn, model, model_name, cfg.vad_enabled) is None:
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
