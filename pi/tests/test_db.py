"""Tests for the DB layer.

Weighted towards the "never lose audio silently" guarantees — replay idempotency,
gap detection/healing, and the atomic claim — rather than plain CRUD.
"""

import sqlite3

import pytest

from recordme import db as dbmod


# ---------------------------------------------------------------- chunks


def test_insert_chunk_returns_id(conn, add_chunk):
    assert add_chunk(conn, seq=0) > 0


def test_missing_field_is_a_clear_error(conn):
    with pytest.raises(ValueError, match="missing required field"):
        dbmod.insert_chunk(conn, boot_id="b", seq=1)


def test_replay_is_idempotent(conn, add_chunk):
    """A retry whose ACK was lost must be a no-op, not a duplicate or an error."""
    first = add_chunk(conn, seq=7)
    again = add_chunk(conn, seq=7)
    assert first == again
    (n,) = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    assert n == 1


def test_same_seq_different_boot_is_a_different_chunk(conn, add_chunk):
    a = add_chunk(conn, boot_id="bootA", seq=0)
    b = add_chunk(conn, boot_id="bootB", seq=0)
    assert a != b


# ---------------------------------------------------------------- gaps


def test_gap_in_the_middle_is_detected(conn, add_chunk):
    for s in (0, 1, 3, 4):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == [(2, 2)]


def test_multi_seq_gap_collapses_to_one_range(conn, add_chunk):
    for s in (0, 5):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == [(1, 4)]


def test_missing_seq_zero_is_detected(conn, add_chunk):
    """Anchoring to min(seq) instead of 0 would hide a lost first chunk entirely."""
    for s in (1, 2):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == [(0, 0)]


def test_nothing_above_max_seq_counts_as_a_gap(conn, add_chunk):
    """Un-uploaded chunks are not lost chunks."""
    for s in (0, 1, 2):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == []


def test_gap_is_recorded_in_the_table(conn, add_chunk):
    for s in (0, 2):
        add_chunk(conn, seq=s)
    dbmod.detect_gaps(conn, "boot0")
    rows = dbmod.list_gaps(conn)
    assert [(r["seq_from"], r["seq_to"]) for r in rows] == [(1, 1)]


def test_gap_heals_when_the_retry_lands(conn, add_chunk):
    """The key self-healing case: a 'missing' seq is often just a retry in flight."""
    for s in (0, 2):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == [(1, 1)]

    add_chunk(conn, seq=1)
    assert dbmod.detect_gaps(conn, "boot0") == []
    assert dbmod.list_gaps(conn) == []


def test_partially_filled_gap_is_resplit(conn, add_chunk):
    for s in (0, 6):
        add_chunk(conn, seq=s)
    assert dbmod.detect_gaps(conn, "boot0") == [(1, 5)]

    add_chunk(conn, seq=3)
    assert dbmod.detect_gaps(conn, "boot0") == [(1, 2), (4, 5)]
    rows = dbmod.list_gaps(conn)
    assert sorted((r["seq_from"], r["seq_to"]) for r in rows) == [(1, 2), (4, 5)]


def test_detect_gaps_is_repeatable(conn, add_chunk):
    for s in (0, 2):
        add_chunk(conn, seq=s)
    for _ in range(3):
        assert dbmod.detect_gaps(conn, "boot0") == [(1, 1)]
    assert len(dbmod.list_gaps(conn)) == 1


def test_gaps_are_per_boot(conn, add_chunk):
    add_chunk(conn, boot_id="bootA", seq=0)
    add_chunk(conn, boot_id="bootA", seq=2)
    add_chunk(conn, boot_id="bootB", seq=0)
    assert dbmod.detect_gaps(conn, "bootA") == [(1, 1)]
    assert dbmod.detect_gaps(conn, "bootB") == []


# ---------------------------------------------------------------- worker queue


def test_claim_takes_oldest_and_marks_transcribing(conn, add_chunk):
    add_chunk(conn, seq=0)
    add_chunk(conn, seq=1)

    row = dbmod.claim_pending_chunk(conn)
    assert row["seq"] == 0
    assert row["status"] == "transcribing"


def test_claim_never_returns_the_same_chunk_twice(conn, add_chunk):
    add_chunk(conn, seq=0)
    add_chunk(conn, seq=1)

    first = dbmod.claim_pending_chunk(conn)
    second = dbmod.claim_pending_chunk(conn)
    assert {first["seq"], second["seq"]} == {0, 1}
    assert dbmod.claim_pending_chunk(conn) is None


def test_claim_on_empty_queue_is_none(conn):
    assert dbmod.claim_pending_chunk(conn) is None


def test_requeue_rescues_a_crashed_workers_claim(conn, add_chunk):
    """Without this, a worker killed mid-chunk strands that audio forever."""
    add_chunk(conn, seq=0)
    dbmod.claim_pending_chunk(conn)
    assert dbmod.claim_pending_chunk(conn) is None  # stranded

    assert dbmod.requeue_stale_transcribing(conn) == 1
    assert dbmod.claim_pending_chunk(conn)["seq"] == 0


def test_requeue_leaves_done_chunks_alone(conn, add_chunk):
    cid = add_chunk(conn, seq=0)
    dbmod.claim_pending_chunk(conn)
    dbmod.save_transcript(conn, cid, "hello", model="fake")
    assert dbmod.requeue_stale_transcribing(conn) == 0


def test_mark_failed_and_silent(conn, add_chunk):
    a = add_chunk(conn, seq=0)
    b = add_chunk(conn, seq=1)
    dbmod.mark_failed(conn, a, "boom")
    dbmod.mark_silent(conn, b)

    rows = {r["id"]: r for r in conn.execute("SELECT id, status, error FROM chunks")}
    assert rows[a]["status"] == "failed" and rows[a]["error"] == "boom"
    assert rows[b]["status"] == "silent" and rows[b]["error"] is None


def test_queue_depth_counts_by_status(conn, add_chunk):
    add_chunk(conn, seq=0)
    add_chunk(conn, seq=1)
    dbmod.mark_silent(conn, add_chunk(conn, seq=2))
    assert dbmod.queue_depth(conn) == {"pending": 2, "silent": 1}


# ---------------------------------------------------------------- transcripts


def test_save_transcript_marks_chunk_done_and_stores_segments(conn, add_chunk):
    cid = add_chunk(conn, seq=0)
    tid = dbmod.save_transcript(
        conn,
        cid,
        "the quick brown fox",
        model="fake",
        language="en",
        speech_s=2.5,
        segments=[
            {"start": 0.0, "end": 1.0, "text": "the quick"},
            {"start": 1.0, "end": 2.5, "text": "brown fox"},
        ],
    )
    assert tid > 0
    (status,) = conn.execute("SELECT status FROM chunks WHERE id = ?", (cid,)).fetchone()
    assert status == "done"

    segs = dbmod.segments_for(conn, tid)
    assert [s["text"] for s in segs] == ["the quick", "brown fox"]


def test_resaving_a_transcript_replaces_it(conn, add_chunk):
    """Re-transcribing (better model, retry after a fix) must not trip UNIQUE."""
    cid = add_chunk(conn, seq=0)
    dbmod.save_transcript(conn, cid, "first pass", model="small.en", segments=[
        {"start": 0.0, "end": 1.0, "text": "first pass"}
    ])
    dbmod.save_transcript(conn, cid, "second pass", model="large-v3-turbo", segments=[
        {"start": 0.0, "end": 1.0, "text": "second pass"}
    ])

    (n,) = conn.execute("SELECT COUNT(*) FROM transcripts WHERE chunk_id = ?", (cid,)).fetchone()
    assert n == 1
    (n,) = conn.execute("SELECT COUNT(*) FROM segments").fetchone()
    assert n == 1
    # FTS must follow the update, not keep indexing the stale text.
    assert [r["text"] for r in dbmod.search(conn, "second")] == ["second pass"]
    assert dbmod.search(conn, "first") == []


# ---------------------------------------------------------------- search


def test_search_finds_a_transcript(conn, add_chunk):
    cid = add_chunk(conn, seq=0)
    dbmod.save_transcript(conn, cid, "remember to call the dentist", model="fake")
    hits = dbmod.search(conn, "dentist")
    assert len(hits) == 1
    assert "dentist" in hits[0]["text"]
    assert "[dentist]" in hits[0]["snippet"]


def test_search_is_stemmed(conn, add_chunk):
    """schema.sql uses the porter tokenizer, so this should match."""
    cid = add_chunk(conn, seq=0)
    dbmod.save_transcript(conn, cid, "I was calling the plumber", model="fake")
    assert len(dbmod.search(conn, "call")) == 1


def test_multiple_terms_are_anded(conn, add_chunk):
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), "alpha beta", model="fake")
    dbmod.save_transcript(conn, add_chunk(conn, seq=1), "alpha gamma", model="fake")
    assert len(dbmod.search(conn, "alpha")) == 2
    assert len(dbmod.search(conn, "alpha beta")) == 1


def test_search_is_newest_first(conn, add_chunk):
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), "shared word early", model="fake")
    dbmod.save_transcript(conn, add_chunk(conn, seq=1), "shared word later", model="fake")
    hits = dbmod.search(conn, "shared")
    assert hits[0]["started_at"] > hits[1]["started_at"]


def test_date_range_filters(conn, add_chunk):
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), "needle one", model="fake")
    dbmod.save_transcript(conn, add_chunk(conn, seq=5), "needle two", model="fake")

    assert len(dbmod.search(conn, "needle")) == 2
    assert len(dbmod.search(conn, "needle", since="2026-09-23T10:03:00.000Z")) == 1
    assert len(dbmod.search(conn, "needle", until="2026-09-23T10:03:00.000Z")) == 1
    assert dbmod.search(conn, "needle", since="2026-09-24T00:00:00.000Z") == []


def test_limit_is_respected(conn, add_chunk):
    for s in range(5):
        dbmod.save_transcript(conn, add_chunk(conn, seq=s), "common", model="fake")
    assert len(dbmod.search(conn, "common", limit=2)) == 2


@pytest.mark.parametrize(
    "nasty",
    ['"', '""', "a*", "*", "NEAR(", "foo:bar", "^x", "-a", "(", ")", "a OR", "AND", 'say "hi"'],
)
def test_fts_syntax_in_user_input_does_not_raise(conn, add_chunk, nasty):
    """Raw user input reaches FTS5; an unescaped operator would 500 the endpoint."""
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), 'he said "hi" to me', model="fake")
    try:
        dbmod.search(conn, nasty)
    except sqlite3.OperationalError as e:
        pytest.fail("FTS5 syntax leaked through for {!r}: {}".format(nasty, e))


def test_quoted_phrase_in_input_still_matches_literally(conn, add_chunk):
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), 'he said "hi" to me', model="fake")
    assert len(dbmod.search(conn, '"hi"')) == 1


def test_empty_query_returns_nothing(conn, add_chunk):
    dbmod.save_transcript(conn, add_chunk(conn, seq=0), "something", model="fake")
    assert dbmod.search(conn, "") == []
    assert dbmod.search(conn, "   ") == []


# ---------------------------------------------------------------- misc


def test_utcnow_sorts_lexicographically(conn):
    """Date-range filtering is string comparison, so this format must sort."""
    ts = dbmod.utcnow()
    assert ts.endswith("Z")
    assert ts < "2099-01-01T00:00:00.000Z"
    assert ts > "2000-01-01T00:00:00.000Z"


def test_foreign_keys_are_enforced(conn):
    """Without PRAGMA foreign_keys the ON DELETE CASCADE in the schema is inert."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transcripts (chunk_id, text, model, transcribed_at)"
            " VALUES (99999, 'orphan', 'fake', '2026-01-01T00:00:00.000Z')"
        )
        conn.commit()


def test_deleting_a_chunk_cascades(conn, add_chunk):
    cid = add_chunk(conn, seq=0)
    tid = dbmod.save_transcript(conn, cid, "gone soon", model="fake", segments=[
        {"start": 0.0, "end": 1.0, "text": "gone soon"}
    ])
    with conn:
        conn.execute("DELETE FROM chunks WHERE id = ?", (cid,))

    (n,) = conn.execute("SELECT COUNT(*) FROM transcripts WHERE id = ?", (tid,)).fetchone()
    assert n == 0
    (n,) = conn.execute("SELECT COUNT(*) FROM segments WHERE transcript_id = ?", (tid,)).fetchone()
    assert n == 0
    # The FTS delete trigger must have fired too, or search would return a ghost row.
    assert dbmod.search(conn, "gone") == []
