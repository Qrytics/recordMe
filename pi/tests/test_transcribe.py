"""Tests for the transcription worker.

Uses a fake model rather than real faster-whisper: the point here is the queue
mechanics and failure isolation, which must be correct regardless of which Whisper
build is installed. Real model behaviour is measured by bench_whisper.py on the Pi.
"""

import wave

import pytest

from recordme import db as dbmod
from recordme import transcribe

MODEL_NAME = "faster-whisper/fake"


class FakeInfo:
    def __init__(self, language="en", duration_after_vad=None):
        self.language = language
        if duration_after_vad is not None:
            self.duration_after_vad = duration_after_vad


class FakeSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class FakeModel:
    """Stands in for WhisperModel. Records how it was called."""

    def __init__(self, segments=None, info=None, raises=None):
        self._segments = segments if segments is not None else [
            FakeSegment(0.0, 1.2, " hello there"),
            FakeSegment(1.2, 2.0, " friend"),
        ]
        self._info = info if info is not None else FakeInfo()
        self._raises = raises
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self._raises:
            raise self._raises
        return iter(self._segments), self._info


@pytest.fixture()
def wav(tmp_path):
    """A real (silent) WAV on disk, so path handling is exercised for real."""
    path = tmp_path / "audio" / "chunk.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return path


@pytest.fixture()
def queued(conn, add_chunk, wav):
    """One pending chunk pointing at a real WAV."""
    return add_chunk(conn, seq=0, audio_path=str(wav))


# ---------------------------------------------------------------- transcribe_chunk


def test_transcribe_chunk_joins_segments(wav):
    result = transcribe.transcribe_chunk(FakeModel(), wav)
    assert result["text"] == "hello there friend"
    assert [s["text"] for s in result["segments"]] == ["hello there", "friend"]
    assert result["language"] == "en"


def test_vad_is_enabled_by_default(wav):
    model = FakeModel()
    transcribe.transcribe_chunk(model, wav)
    _, kwargs = model.calls[0]
    assert kwargs["vad_filter"] is True
    assert kwargs["word_timestamps"] is True


def test_vad_can_be_disabled(wav):
    model = FakeModel()
    transcribe.transcribe_chunk(model, wav, vad_enabled=False)
    assert model.calls[0][1]["vad_filter"] is False


def test_speech_seconds_prefers_the_vad_duration(wav):
    model = FakeModel(info=FakeInfo(duration_after_vad=1.75))
    assert transcribe.transcribe_chunk(model, wav)["speech_s"] == pytest.approx(1.75)


def test_speech_seconds_falls_back_to_segment_spans(wav):
    """Older faster-whisper builds don't expose duration_after_vad."""
    model = FakeModel(info=FakeInfo())
    assert transcribe.transcribe_chunk(model, wav)["speech_s"] == pytest.approx(2.0)


def test_blank_segments_are_dropped(wav):
    model = FakeModel(segments=[FakeSegment(0.0, 1.0, "   "), FakeSegment(1.0, 2.0, "real")])
    result = transcribe.transcribe_chunk(model, wav)
    assert result["text"] == "real"
    assert len(result["segments"]) == 1


def test_missing_audio_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe.transcribe_chunk(FakeModel(), tmp_path / "nope.wav")


# ---------------------------------------------------------------- process_one


def test_process_one_on_empty_queue_is_none(conn):
    assert transcribe.process_one(conn, FakeModel(), MODEL_NAME) is None


def test_process_one_stores_transcript_and_marks_done(conn, queued):
    assert transcribe.process_one(conn, FakeModel(), MODEL_NAME) == "done"

    row = conn.execute("SELECT status FROM chunks WHERE id = ?", (queued,)).fetchone()
    assert row["status"] == "done"

    t = conn.execute("SELECT * FROM transcripts WHERE chunk_id = ?", (queued,)).fetchone()
    assert t["text"] == "hello there friend"
    assert t["model"] == MODEL_NAME
    assert t["language"] == "en"

    segs = dbmod.segments_for(conn, t["id"])
    assert [s["text"] for s in segs] == ["hello there", "friend"]


def test_transcript_is_immediately_searchable(conn, queued):
    transcribe.process_one(conn, FakeModel(), MODEL_NAME)
    assert len(dbmod.search(conn, "friend")) == 1


def test_no_speech_is_silent_not_failed(conn, queued):
    """The common case for always-on recording — not an error."""
    assert transcribe.process_one(conn, FakeModel(segments=[]), MODEL_NAME) == "silent"

    row = conn.execute("SELECT status, error FROM chunks WHERE id = ?", (queued,)).fetchone()
    assert row["status"] == "silent"
    assert row["error"] is None
    assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 0


def test_model_error_marks_failed_with_the_message(conn, queued):
    model = FakeModel(raises=RuntimeError("ct2 exploded"))
    assert transcribe.process_one(conn, model, MODEL_NAME) == "failed"

    row = conn.execute("SELECT status, error FROM chunks WHERE id = ?", (queued,)).fetchone()
    assert row["status"] == "failed"
    assert "ct2 exploded" in row["error"]


def test_missing_file_marks_failed_rather_than_crashing(conn, add_chunk, tmp_path):
    cid = add_chunk(conn, seq=0, audio_path=str(tmp_path / "gone.wav"))
    assert transcribe.process_one(conn, FakeModel(), MODEL_NAME) == "failed"
    (status,) = conn.execute("SELECT status FROM chunks WHERE id = ?", (cid,)).fetchone()
    assert status == "failed"


def test_one_bad_chunk_does_not_wedge_the_queue(conn, add_chunk, wav, tmp_path):
    """The key resilience property: a poison chunk must not stop the ones behind it."""
    bad = add_chunk(conn, seq=0, audio_path=str(tmp_path / "missing.wav"))
    good = add_chunk(conn, seq=1, audio_path=str(wav))

    assert transcribe.drain(conn, FakeModel(), MODEL_NAME) == 2

    statuses = {
        r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM chunks")
    }
    assert statuses[bad] == "failed"
    assert statuses[good] == "done"


def test_failed_chunks_are_not_retried_endlessly(conn, add_chunk, tmp_path):
    add_chunk(conn, seq=0, audio_path=str(tmp_path / "missing.wav"))
    transcribe.drain(conn, FakeModel(), MODEL_NAME)
    # 'failed' is terminal, so a second drain finds nothing to do.
    assert transcribe.drain(conn, FakeModel(), MODEL_NAME) == 0


def test_drain_processes_oldest_first(conn, add_chunk, wav):
    add_chunk(conn, seq=0, audio_path=str(wav))
    add_chunk(conn, seq=1, audio_path=str(wav))
    transcribe.drain(conn, FakeModel(), MODEL_NAME)

    rows = conn.execute(
        "SELECT c.seq, t.id AS tid FROM chunks c JOIN transcripts t ON t.chunk_id = c.id"
        " ORDER BY t.id"
    ).fetchall()
    assert [r["seq"] for r in rows] == [0, 1]


def test_worker_recovers_a_crashed_claim(conn, queued):
    """Simulates a worker killed mid-chunk, then restarted."""
    dbmod.claim_pending_chunk(conn)
    assert transcribe.process_one(conn, FakeModel(), MODEL_NAME) is None  # stranded

    dbmod.requeue_stale_transcribing(conn)
    assert transcribe.process_one(conn, FakeModel(), MODEL_NAME) == "done"


def test_faster_whisper_is_not_imported_at_module_load():
    """Keeps the receiver, search service, and this suite free of the ML stack."""
    import sys

    assert "faster_whisper" not in sys.modules
