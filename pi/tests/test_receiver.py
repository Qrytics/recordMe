"""Tests for the upload receiver.

The interesting assertions are about the ACK contract: the device deletes its only
copy of the audio when we return 200, so a 200 must mean the bytes are on disk and
the row is committed, and a replayed chunk must not become a duplicate.
"""

import struct
import wave
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from recordme import db as dbmod
from recordme import receiver
from recordme.config import Config

TOKEN = "unit-test-token"


def make_pcm(seconds=1.0, sample_rate=16000, freq=440.0, amplitude=8000):
    """A sine tone as raw 16-bit mono PCM — the same shape the firmware sends."""
    import math

    n = int(seconds * sample_rate)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate)))
        for i in range(n)
    )


@pytest.fixture()
def cfg(tmp_path):
    return Config(
        upload_token=TOKEN,
        db_path=tmp_path / "test.db",
        audio_dir=tmp_path / "audio",
        receiver_host="127.0.0.1",
        receiver_port=8000,
        search_host="127.0.0.1",
        search_port=8001,
        search_password="pw",
        whisper_model="small.en",
        whisper_compute="int8",
        whisper_threads=4,
        vad_enabled=True,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="llama3.1:8b",
    )


@pytest.fixture()
def client(cfg, conn):
    receiver.app.dependency_overrides[receiver.get_config] = lambda: cfg
    receiver.app.dependency_overrides[receiver.db_conn] = lambda: conn
    yield TestClient(receiver.app)
    receiver.app.dependency_overrides.clear()


def post(client, body, boot_id="abc123", seq=0, chunk_start_ms=0, device_now_ms=1000,
         sample_rate=16000, token=TOKEN):
    headers = {"Content-Type": "application/octet-stream"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post(
        "/upload",
        params={
            "boot_id": boot_id,
            "seq": seq,
            "chunk_start_ms": chunk_start_ms,
            "device_now_ms": device_now_ms,
            "sample_rate": sample_rate,
        },
        content=body,
        headers=headers,
    )


# ---------------------------------------------------------------- auth


def test_missing_token_is_rejected(client):
    assert post(client, make_pcm(0.1), token=None).status_code == 401


def test_wrong_token_is_rejected(client):
    assert post(client, make_pcm(0.1), token="nope").status_code == 401


def test_token_prefix_is_not_accepted(client):
    assert post(client, make_pcm(0.1), token=TOKEN[:-1]).status_code == 401


def test_rejected_upload_stores_nothing(client, conn, cfg):
    post(client, make_pcm(0.1), token="nope")
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert not list(cfg.audio_dir.rglob("*.wav"))


# ---------------------------------------------------------------- happy path


def test_upload_stores_wav_and_row(client, conn, cfg):
    pcm = make_pcm(1.0)
    r = post(client, pcm)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is False
    assert body["chunk_id"] > 0

    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (body["chunk_id"],)).fetchone()
    assert row["status"] == "pending"
    assert row["bytes"] == len(pcm)
    assert row["duration_s"] == pytest.approx(1.0)
    assert row["sample_rate"] == 16000

    wavs = list(cfg.audio_dir.rglob("*.wav"))
    assert len(wavs) == 1
    assert str(wavs[0]) == row["audio_path"]


def test_stored_wav_is_valid_and_roundtrips(client, cfg):
    pcm = make_pcm(0.5)
    post(client, pcm)
    path = next(cfg.audio_dir.rglob("*.wav"))
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == len(pcm) // 2
        assert w.readframes(w.getnframes()) == pcm


def test_no_part_file_is_left_behind(client, cfg):
    post(client, make_pcm(0.2))
    assert not list(cfg.audio_dir.rglob("*.part"))


def test_audio_path_is_date_sharded(client, cfg):
    post(client, make_pcm(0.1))
    path = next(cfg.audio_dir.rglob("*.wav"))
    # audio_dir/YYYY/MM/DD/<boot_id>-<seq>.wav
    rel = path.relative_to(cfg.audio_dir).parts
    assert len(rel) == 4
    assert all(p.isdigit() for p in rel[:3])
    assert rel[3] == "abc123-000000.wav"


def test_health_reports_queue_depth(client):
    post(client, make_pcm(0.1), seq=0)
    post(client, make_pcm(0.1), seq=1)
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["queue"]["pending"] == 2
    assert body["pending"] == 2


# ---------------------------------------------------------------- the ACK contract


def test_replayed_chunk_is_idempotent(client, conn, cfg):
    """The device retries when an ACK is lost. That must not duplicate anything."""
    pcm = make_pcm(0.5)
    first = post(client, pcm)
    second = post(client, pcm)

    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] is True
    assert first.json()["chunk_id"] == second.json()["chunk_id"]
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert len(list(cfg.audio_dir.rglob("*.wav"))) == 1


def test_replay_does_not_disturb_the_queue(client, conn):
    """A retry arriving after transcription must not reset the chunk to pending."""
    pcm = make_pcm(0.2)
    cid = post(client, pcm).json()["chunk_id"]
    dbmod.claim_pending_chunk(conn)
    dbmod.save_transcript(conn, cid, "already done", model="fake")

    post(client, pcm)
    (status,) = conn.execute("SELECT status FROM chunks WHERE id = ?", (cid,)).fetchone()
    assert status == "done"


# ---------------------------------------------------------------- timestamps


def test_started_at_is_derived_from_the_device_clock():
    """The device has no RTC; the Pi reconstructs absolute time."""
    received = datetime(2026, 9, 23, 12, 0, 30, tzinfo=timezone.utc)
    # Chunk began at 5 s after boot; device sent it at 35 s after boot -> 30 s old.
    started = receiver.chunk_started_at(received, chunk_start_ms=5_000, device_now_ms=35_000)
    assert started == received - timedelta(seconds=30)


def test_a_chunk_buffered_in_flash_for_20_minutes_still_dates_correctly():
    """The whole reason for this scheme: audio can sit in flash waiting for Wi-Fi."""
    received = datetime(2026, 9, 23, 12, 20, 0, tzinfo=timezone.utc)
    started = receiver.chunk_started_at(
        received, chunk_start_ms=60_000, device_now_ms=60_000 + 20 * 60 * 1000
    )
    assert started == received - timedelta(minutes=20)


def test_negative_age_is_clamped_not_projected_into_the_future():
    received = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    started = receiver.chunk_started_at(received, chunk_start_ms=9_000, device_now_ms=1_000)
    assert started == received


def test_started_at_precedes_received_at_in_the_row(client, conn):
    post(client, make_pcm(1.0), chunk_start_ms=0, device_now_ms=60_000)
    row = conn.execute("SELECT started_at, received_at FROM chunks").fetchone()
    assert row["started_at"] < row["received_at"]


# ---------------------------------------------------------------- gap detection


def test_skipped_seq_is_reported_as_a_gap(client, conn):
    post(client, make_pcm(0.1), seq=0)
    r = post(client, make_pcm(0.1), seq=2)
    assert r.json()["gaps"] == [[1, 1]]
    assert [(g["seq_from"], g["seq_to"]) for g in dbmod.list_gaps(conn)] == [(1, 1)]


def test_gap_disappears_once_the_missing_chunk_arrives(client, conn):
    post(client, make_pcm(0.1), seq=0)
    post(client, make_pcm(0.1), seq=2)
    r = post(client, make_pcm(0.1), seq=1)
    assert r.json()["gaps"] == []
    assert dbmod.list_gaps(conn) == []


def test_health_surfaces_gaps(client):
    post(client, make_pcm(0.1), seq=0)
    post(client, make_pcm(0.1), seq=3)
    body = client.get("/health").json()
    assert body["recent_gaps"] == [
        {"boot_id": "abc123", "seq_from": 1, "seq_to": 2,
         "detected_at": body["recent_gaps"][0]["detected_at"]}
    ]


# ---------------------------------------------------------------- validation


def test_empty_body_is_rejected(client):
    assert post(client, b"").status_code == 400


def test_odd_byte_count_is_rejected(client):
    """Not a whole number of 16-bit samples means something is wrong upstream."""
    assert post(client, b"\x01\x02\x03").status_code == 400


def test_oversized_body_is_rejected(client):
    assert post(client, b"\x00" * (receiver.MAX_BODY_BYTES + 2)).status_code == 413


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "has space", "x" * 33, "semi;colon"])
def test_unsafe_boot_id_is_rejected(client, bad):
    """boot_id becomes a path component, so it must not escape the audio dir."""
    assert post(client, make_pcm(0.1), boot_id=bad).status_code in (400, 422)


def test_negative_seq_is_rejected(client):
    assert post(client, make_pcm(0.1), seq=-1).status_code == 400


def test_implausible_sample_rate_is_rejected(client):
    assert post(client, make_pcm(0.1), sample_rate=100).status_code == 400


def test_missing_required_params_is_422(client):
    r = client.post(
        "/upload",
        params={"boot_id": "abc123"},
        content=make_pcm(0.1),
        headers={"Authorization": "Bearer " + TOKEN},
    )
    assert r.status_code == 422
