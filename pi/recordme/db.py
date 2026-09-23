"""SQLite access. Schema is in pi/schema.sql.

Typing note: annotations here avoid PEP 604 (`X | None`) on purpose. The Pi runs
Python 3.11 where it would be fine, but dev machines on 3.9 evaluate annotations at
definition time and would fail to import. `Optional[...]` costs nothing and works on
both. Same reason `search.py` uses it in the route signatures.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"

# Columns the receiver must supply for every chunk. Listed explicitly so a caller
# that forgets one gets a clear error instead of a SQL NOT NULL failure.
_CHUNK_COLS = (
    "boot_id",
    "seq",
    "chunk_start_ms",
    "started_at",
    "duration_s",
    "sample_rate",
    "audio_path",
    "bytes",
    "received_at",
)


def utcnow() -> str:
    """ISO8601 UTC with a Z suffix.

    Every timestamp in the DB uses this format because it sorts lexicographically,
    which is what lets the date-range filters in search() be plain string
    comparisons against an indexed TEXT column.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB, applying the schema if it's new.

    WAL mode matters here: the receiver writes chunk rows while the transcription
    worker updates them, so readers must not block writers.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because FastAPI runs a sync dependency and a
    # run_in_threadpool call on different worker threads, so a per-request
    # connection legitimately crosses threads. Safe here: callers open one
    # connection per request and never share one between concurrent requests.
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())  # all statements are IF NOT EXISTS
    # journal_mode is persistent in the file, but foreign_keys is per-connection
    # and is silently ignored inside a transaction — set it after the script.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------- chunks


def find_chunk(conn: sqlite3.Connection, boot_id: str, seq: int) -> Optional[sqlite3.Row]:
    """Look up a chunk by its device-side identity, or None.

    The receiver calls this before doing any work, so a re-sent chunk costs a single
    indexed lookup rather than a rewritten WAV file.
    """
    return conn.execute(
        "SELECT * FROM chunks WHERE boot_id = ? AND seq = ?", (boot_id, seq)
    ).fetchone()


def insert_chunk(conn: sqlite3.Connection, **fields: Any) -> int:
    """Record a received chunk. Returns its id.

    UNIQUE(boot_id, seq) makes this idempotent, which matters: the device retries
    uploads and may re-send a chunk whose ACK was lost in transit.
    """
    missing = [c for c in _CHUNK_COLS if c not in fields]
    if missing:
        raise ValueError(f"insert_chunk missing required field(s): {', '.join(missing)}")

    cols = ", ".join(_CHUNK_COLS)
    holes = ", ".join("?" * len(_CHUNK_COLS))
    values = [fields[c] for c in _CHUNK_COLS]

    with conn:
        row = conn.execute(
            f"INSERT INTO chunks ({cols}) VALUES ({holes})"
            f" ON CONFLICT (boot_id, seq) DO NOTHING"
            f" RETURNING id",
            values,
        ).fetchone()

    if row is not None:
        return row["id"]

    # Lost-ACK replay: the row was already here. Return the original id rather than
    # erroring, so the device's retry succeeds and it can free its copy.
    existing = find_chunk(conn, fields["boot_id"], fields["seq"])
    return existing["id"]


def detect_gaps(conn: sqlite3.Connection, boot_id: str) -> List[Tuple[int, int]]:
    """Find missing sequence numbers for a boot session and record them in `gaps`.

    A gap means the device dropped audio because both PSRAM and flash overflowed
    while the Pi was unreachable. The spec allows losing audio but not losing it
    silently, so these must be surfaced, not swallowed.

    Two subtleties, both deliberate:

    - Counting starts at seq 0, not at the lowest seq present. The firmware resets
      chunk_seq to 0 on every boot, so if seq 0 itself was lost, anchoring to
      min(seq) would hide the loss completely.
    - Nothing above max(seq) is a gap. Those chunks aren't lost, they just haven't
      been uploaded yet.

    Self-healing: a missing seq may only be a retry still in flight, so recorded
    gaps that have since been filled are deleted. Safe to call on every upload.
    """
    seqs = [
        r["seq"]
        for r in conn.execute(
            "SELECT seq FROM chunks WHERE boot_id = ? ORDER BY seq", (boot_id,)
        )
    ]
    ranges: List[Tuple[int, int]] = []
    if seqs:
        present = set(seqs)
        run_start: Optional[int] = None
        for s in range(0, max(seqs) + 1):
            if s not in present:
                if run_start is None:
                    run_start = s
            elif run_start is not None:
                ranges.append((run_start, s - 1))
                run_start = None

    live = set(ranges)
    now = utcnow()
    with conn:
        recorded = conn.execute(
            "SELECT id, seq_from, seq_to FROM gaps WHERE boot_id = ?", (boot_id,)
        ).fetchall()
        for row in recorded:
            if (row["seq_from"], row["seq_to"]) not in live:
                conn.execute("DELETE FROM gaps WHERE id = ?", (row["id"],))
        for seq_from, seq_to in ranges:
            conn.execute(
                "INSERT INTO gaps (boot_id, seq_from, seq_to, detected_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (boot_id, seq_from, seq_to) DO NOTHING",
                (boot_id, seq_from, seq_to, now),
            )
    return ranges


def queue_depth(conn: sqlite3.Connection) -> Dict[str, int]:
    """Chunk counts by status, so a growing transcription backlog is visible."""
    return {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM chunks GROUP BY status")
    }


# ---------------------------------------------------------------- worker queue


def claim_pending_chunk(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Atomically take the oldest pending chunk and mark it 'transcribing'.

    Must be a single UPDATE ... RETURNING (or a transaction) so two workers can
    never claim the same chunk.
    """
    with conn:
        return conn.execute(
            "UPDATE chunks SET status = 'transcribing'"
            " WHERE id = ("
            "   SELECT id FROM chunks WHERE status = 'pending'"
            "   ORDER BY started_at, seq LIMIT 1"
            " )"
            " RETURNING *"
        ).fetchone()


def requeue_stale_transcribing(conn: sqlite3.Connection) -> int:
    """Return chunks stuck in 'transcribing' to 'pending'. Returns the count.

    A worker killed mid-chunk (crash, power cut, systemd restart) leaves its claim
    behind, and nothing else would ever pick that chunk up again — audio silently
    never transcribed, which is exactly the failure mode the spec forbids. Call this
    at worker startup, where it is safe because a single worker is assumed.
    """
    with conn:
        cur = conn.execute(
            "UPDATE chunks SET status = 'pending' WHERE status = 'transcribing'"
        )
    return cur.rowcount


def mark_failed(conn: sqlite3.Connection, chunk_id: int, error: str) -> None:
    """Record a transcription failure. One bad chunk must not wedge the queue."""
    with conn:
        conn.execute(
            "UPDATE chunks SET status = 'failed', error = ? WHERE id = ?",
            (error[:2000], chunk_id),
        )


def mark_silent(conn: sqlite3.Connection, chunk_id: int) -> None:
    """No speech found. Distinct from 'failed' — nothing went wrong, VAD just
    found nothing, which is the common case for always-on recording."""
    with conn:
        conn.execute(
            "UPDATE chunks SET status = 'silent', error = NULL WHERE id = ?", (chunk_id,)
        )


# ---------------------------------------------------------------- transcripts


def save_transcript(conn: sqlite3.Connection, chunk_id: int, text: str, **meta: Any) -> int:
    """Store a transcript and its segments, then mark the chunk 'done'.

    The FTS index updates via triggers — do not write transcripts_fts directly.

    Upsert rather than insert, so re-transcribing a chunk (different model, retry
    after a fix) replaces the old result instead of tripping UNIQUE(chunk_id). The
    AFTER UPDATE trigger keeps FTS in step.
    """
    segments = meta.get("segments") or []
    with conn:
        tid = conn.execute(
            "INSERT INTO transcripts (chunk_id, text, language, model, speech_s, transcribed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (chunk_id) DO UPDATE SET"
            "   text = excluded.text, language = excluded.language,"
            "   model = excluded.model, speech_s = excluded.speech_s,"
            "   transcribed_at = excluded.transcribed_at"
            " RETURNING id",
            (
                chunk_id,
                text,
                meta.get("language"),
                meta["model"],
                meta.get("speech_s"),
                meta.get("transcribed_at") or utcnow(),
            ),
        ).fetchone()["id"]

        conn.execute("DELETE FROM segments WHERE transcript_id = ?", (tid,))
        conn.executemany(
            "INSERT INTO segments (transcript_id, start_s, end_s, text) VALUES (?, ?, ?, ?)",
            [(tid, s["start"], s["end"], s["text"]) for s in segments],
        )
        conn.execute(
            "UPDATE chunks SET status = 'done', error = NULL WHERE id = ?", (chunk_id,)
        )
    return tid


def segments_for(conn: sqlite3.Connection, transcript_id: int) -> List[sqlite3.Row]:
    """Segment timestamps for one transcript, so a search hit can point at the
    exact moment in the audio rather than just the chunk."""
    return conn.execute(
        "SELECT start_s, end_s, text FROM segments WHERE transcript_id = ? ORDER BY start_s",
        (transcript_id,),
    ).fetchall()


# ---------------------------------------------------------------- search


def escape_fts(query: str) -> str:
    """Turn raw user input into a safe FTS5 MATCH expression.

    Callers pass whatever was typed, and FTS5 treats characters like `*`, `:`, `^`,
    `(`, and `-` as query syntax — an unbalanced one raises OperationalError and
    would 500 the search endpoint. Quoting each whitespace-separated term makes
    every term a literal (embedded quotes doubled, per FTS5 string rules), giving
    an implicit AND across terms.
    """
    terms = [t.replace('"', '""') for t in query.split() if t.strip()]
    return " ".join('"{}"'.format(t) for t in terms)


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 50,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Full-text search over transcripts, newest first.

    Uses FTS5 MATCH against transcripts_fts joined back to chunks for timestamps.
    Callers pass raw user input, so escape FTS5 syntax before interpolating.
    """
    match = escape_fts(query)
    if not match:
        return []

    sql = [
        "SELECT t.id AS transcript_id, c.id AS chunk_id, c.started_at, c.duration_s,",
        "       c.audio_path, t.text, t.model,",
        "       snippet(transcripts_fts, 0, '[', ']', ' … ', 24) AS snippet",
        "FROM transcripts_fts",
        "JOIN transcripts t ON t.id = transcripts_fts.rowid",
        "JOIN chunks c ON c.id = t.chunk_id",
        "WHERE transcripts_fts MATCH ?",
    ]
    params: List[Any] = [match]
    if since:
        sql.append("AND c.started_at >= ?")
        params.append(since)
    if until:
        sql.append("AND c.started_at <= ?")
        params.append(until)
    sql.append("ORDER BY c.started_at DESC LIMIT ?")
    params.append(limit)

    return conn.execute("\n".join(sql), params).fetchall()


def list_gaps(conn: sqlite3.Connection, limit: int = 100) -> List[sqlite3.Row]:
    """Recorded gaps, newest first — the audit trail for dropped audio."""
    return conn.execute(
        "SELECT boot_id, seq_from, seq_to, detected_at FROM gaps"
        " ORDER BY detected_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
