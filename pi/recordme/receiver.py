"""Upload receiver. LAN-only — the device never uses the remote-access tunnel.

Protocol: raw binary POST (not multipart, to keep the ESP32 side cheap).

    POST /upload?boot_id=<hex>&seq=<n>&chunk_start_ms=<n>&device_now_ms=<n>&sample_rate=16000
    Authorization: Bearer <shared secret>
    Content-Type: application/octet-stream
    <raw PCM body>

Responds 200 with the stored chunk id. The device deletes its local copy ONLY on
that 200 — ACK first, delete second, never the reverse.
"""

import os
import secrets as pysecrets
import sqlite3
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import db as dbmod
from .config import Config

app = FastAPI(title="recordMe receiver")

# A chunk is 30-60 s of 16 kHz 16-bit mono, i.e. ~1-2 MB. The cap is a sanity
# bound so a malformed request can't ask us to buffer something enormous.
MAX_BODY_BYTES = 16 * 1024 * 1024
BYTES_PER_SAMPLE = 2  # 16-bit mono; see SAMPLE_BITS in firmware/recordme/config.h

_cfg: Optional[Config] = None


def get_config() -> Config:
    """Load .env lazily, so importing this module (tests, tooling) doesn't require
    a populated environment."""
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg


def db_conn(cfg: Config = Depends(get_config)):
    """One SQLite connection per request.

    Cheaper than it looks and avoids sharing a connection between concurrent
    requests. Uploads arrive every 30-150 s, so per-request connect cost is
    irrelevant here, and WAL mode means this never blocks the transcription worker.
    """
    conn = dbmod.connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_token(
    authorization: Optional[str] = Header(None),
    cfg: Config = Depends(get_config),
) -> None:
    """Shared-secret bearer auth, compared in constant time.

    The token is the only thing standing between the LAN and the audio store, so
    the comparison must not leak its length or prefix through timing.
    """
    expected = cfg.upload_token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not pysecrets.compare_digest(authorization[len("Bearer ") :], expected):
        raise HTTPException(status_code=401, detail="bad token")


# ---------------------------------------------------------------- storage


def chunk_started_at(received_at: datetime, chunk_start_ms: int, device_now_ms: int) -> datetime:
    """Reconstruct the chunk's wall-clock start time.

    The ESP32 has no battery-backed RTC, so it has no idea what time it is — it only
    reports a monotonic ms-since-boot clock. The Pi is therefore the sole source of
    absolute time: the chunk began (device_now_ms - chunk_start_ms) milliseconds
    before we received it. This stays correct even if the chunk sat in the device's
    flash for twenty minutes waiting for Wi-Fi, which is the whole point.
    """
    age_ms = device_now_ms - chunk_start_ms
    if age_ms < 0:
        # Shouldn't happen — both come from the same monotonic clock. Clamp rather
        # than invent a future timestamp.
        age_ms = 0
    return received_at - timedelta(milliseconds=age_ms)


def audio_path_for(audio_dir: Path, started_at: datetime, boot_id: str, seq: int) -> Path:
    """Date-sharded, deterministic path.

    Sharded so no directory grows to a day-of-chunks x years. Deterministic in
    (boot_id, seq) so a re-sent chunk rewrites the same file with identical bytes
    instead of leaving an orphan behind.
    """
    return (
        audio_dir
        / "{:04d}".format(started_at.year)
        / "{:02d}".format(started_at.month)
        / "{:02d}".format(started_at.day)
        / "{}-{:06d}.wav".format(boot_id, seq)
    )


def write_wav_durably(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Write PCM as a WAV file that is on the platter before we return.

    Write to a temp file, fsync it, rename into place, then fsync the directory. The
    rename is atomic, so a power cut can leave a `.part` file but never a truncated
    `.wav` that the DB claims is complete. The directory fsync is the part people
    skip: without it the rename itself can be lost.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as fh:
        writer = wave.open(fh, "wb")
        writer.setnchannels(1)
        writer.setsampwidth(BYTES_PER_SAMPLE)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
        writer.close()  # patches the RIFF header; does not close fh
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def store_chunk(
    conn: sqlite3.Connection,
    audio_dir: Path,
    *,
    boot_id: str,
    seq: int,
    chunk_start_ms: int,
    device_now_ms: int,
    sample_rate: int,
    body: bytes,
    received_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Durably store one chunk and queue it for transcription.

    Ordering is the contract: bytes durable on disk -> row committed -> only then may
    the caller ACK. A premature ACK makes the device delete audio that was never
    stored, and the device is explicitly designed to trust our 200.
    """
    if received_at is None:
        received_at = datetime.now(timezone.utc)

    existing = dbmod.find_chunk(conn, boot_id, seq)
    if existing is not None:
        # Lost-ACK replay. The bytes are already stored; re-ACK so the device can
        # free its copy, and don't touch the file or the row.
        return {
            "chunk_id": existing["id"],
            "duplicate": True,
            "started_at": existing["started_at"],
            "gaps": [],
        }

    started_at = chunk_started_at(received_at, chunk_start_ms, device_now_ms)
    path = audio_path_for(audio_dir, started_at, boot_id, seq)
    write_wav_durably(path, body, sample_rate)

    chunk_id = dbmod.insert_chunk(
        conn,
        boot_id=boot_id,
        seq=seq,
        chunk_start_ms=chunk_start_ms,
        started_at=_iso(started_at),
        duration_s=len(body) / float(sample_rate * BYTES_PER_SAMPLE),
        sample_rate=sample_rate,
        audio_path=str(path),
        bytes=len(body),
        received_at=_iso(received_at),
    )
    gaps = dbmod.detect_gaps(conn, boot_id)
    return {
        "chunk_id": chunk_id,
        "duplicate": False,
        "started_at": _iso(started_at),
        "gaps": [list(g) for g in gaps],
    }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------- routes


@app.post("/upload", dependencies=[Depends(require_token)])
async def upload(
    request: Request,
    boot_id: str,
    seq: int,
    chunk_start_ms: int,
    device_now_ms: int,
    sample_rate: int = 16000,
    conn: sqlite3.Connection = Depends(db_conn),
    cfg: Config = Depends(get_config),
):
    """Accept one audio chunk, write the WAV to the HDD, queue it for transcription."""
    if seq < 0 or chunk_start_ms < 0 or device_now_ms < 0:
        raise HTTPException(status_code=400, detail="seq and ms values must be non-negative")
    if not boot_id or len(boot_id) > 32 or not boot_id.isalnum():
        # boot_id becomes part of a filesystem path, so keep it to a safe alphabet.
        raise HTTPException(status_code=400, detail="boot_id must be 1-32 alphanumeric chars")
    if not 8000 <= sample_rate <= 48000:
        raise HTTPException(status_code=400, detail="implausible sample_rate")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="chunk too large")
    if len(body) % BYTES_PER_SAMPLE:
        raise HTTPException(status_code=400, detail="body is not whole 16-bit samples")

    result = await run_in_threadpool(
        store_chunk,
        conn,
        cfg.audio_dir,
        boot_id=boot_id,
        seq=seq,
        chunk_start_ms=chunk_start_ms,
        device_now_ms=device_now_ms,
        sample_rate=sample_rate,
        body=body,
    )
    return result


@app.get("/health")
async def health(conn: sqlite3.Connection = Depends(db_conn)):
    """Liveness plus queue depth, so a growing backlog is visible."""
    depth = await run_in_threadpool(dbmod.queue_depth, conn)
    gaps = await run_in_threadpool(dbmod.list_gaps, conn, 10)
    return {
        "ok": True,
        "queue": depth,
        "pending": depth.get("pending", 0) + depth.get("transcribing", 0),
        "recent_gaps": [
            {
                "boot_id": g["boot_id"],
                "seq_from": g["seq_from"],
                "seq_to": g["seq_to"],
                "detected_at": g["detected_at"],
            }
            for g in gaps
        ],
    }
