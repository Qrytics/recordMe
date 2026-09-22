"""SQLite access. Schema is in pi/schema.sql.

SCAFFOLD — signatures and contracts only.
"""

import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB, applying the schema if it's new.

    WAL mode matters here: the receiver writes chunk rows while the transcription
    worker updates them, so readers must not block writers.
    """
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())  # all statements are IF NOT EXISTS
    return conn


def insert_chunk(conn: sqlite3.Connection, **fields) -> int:
    """Record a received chunk. Returns its id.

    UNIQUE(boot_id, seq) makes this idempotent, which matters: the device retries
    uploads and may re-send a chunk whose ACK was lost in transit.
    """
    raise NotImplementedError  # TODO


def detect_gaps(conn: sqlite3.Connection, boot_id: str) -> list[tuple[int, int]]:
    """Find missing sequence numbers for a boot session and record them in `gaps`.

    A gap means the device dropped audio because both PSRAM and flash overflowed
    while the Pi was unreachable. The spec allows losing audio but not losing it
    silently, so these must be surfaced, not swallowed.
    """
    raise NotImplementedError  # TODO


def claim_pending_chunk(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically take the oldest pending chunk and mark it 'transcribing'.

    Must be a single UPDATE ... RETURNING (or a transaction) so two workers can
    never claim the same chunk.
    """
    raise NotImplementedError  # TODO


def save_transcript(conn: sqlite3.Connection, chunk_id: int, text: str, **meta) -> int:
    """Store a transcript and its segments, then mark the chunk 'done'.

    The FTS index updates via triggers — do not write transcripts_fts directly.
    """
    raise NotImplementedError  # TODO


def search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Full-text search over transcripts, newest first.

    Uses FTS5 MATCH against transcripts_fts joined back to chunks for timestamps.
    Callers pass raw user input, so escape FTS5 syntax before interpolating.
    """
    raise NotImplementedError  # TODO
