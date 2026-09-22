-- recordMe transcript store.
--
-- Lives on the SD card, not the HDD: it's only ~55 MB/year and keeping the FTS
-- index off a spinning USB disk keeps search fast. Audio files live on the HDD
-- and are referenced by path.
--
-- Apply with:  sqlite3 recordme.db < schema.sql

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- chunks
-- One row per audio chunk uploaded by the device.
--
-- The device has no RTC, so it cannot know wall-clock time. It sends boot_id
-- (unique per power-on) and chunk_start_ms (ms since that boot); the Pi computes
-- started_at = received_at - (device_now_ms - chunk_start_ms). This stays correct
-- even if a chunk sat in the device's flash for twenty minutes waiting for Wi-Fi.
CREATE TABLE IF NOT EXISTS chunks (
    id             INTEGER PRIMARY KEY,
    boot_id        TEXT    NOT NULL,
    seq            INTEGER NOT NULL,         -- monotonic within a boot; gaps mean lost audio
    chunk_start_ms INTEGER NOT NULL,         -- device clock, ms since boot
    started_at     TEXT    NOT NULL,         -- ISO8601 UTC, computed by the Pi
    duration_s     REAL    NOT NULL,
    sample_rate    INTEGER NOT NULL,
    audio_path     TEXT    NOT NULL,         -- absolute path on the HDD
    bytes          INTEGER NOT NULL,
    received_at    TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'pending',
                   -- pending | transcribing | done | failed | silent
    error          TEXT,
    UNIQUE (boot_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chunks_started  ON chunks (started_at);
CREATE INDEX IF NOT EXISTS idx_chunks_status   ON chunks (status) WHERE status != 'done';

-- ---------------------------------------------------------------- transcripts
CREATE TABLE IF NOT EXISTS transcripts (
    id              INTEGER PRIMARY KEY,
    chunk_id        INTEGER NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    text            TEXT    NOT NULL,
    language        TEXT,
    model           TEXT    NOT NULL,        -- e.g. 'faster-whisper/small.en'
    speech_s        REAL,                    -- seconds of speech after VAD
    transcribed_at  TEXT    NOT NULL,
    UNIQUE (chunk_id)
);

-- Word/segment timestamps from faster-whisper, so a search hit can point at the
-- exact moment in the audio rather than just the chunk.
CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE CASCADE,
    start_s       REAL    NOT NULL,          -- offset within the chunk
    end_s         REAL    NOT NULL,
    text          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_transcript ON segments (transcript_id);

-- ---------------------------------------------------------------- gaps
-- Missing sequence numbers, i.e. audio the device dropped because the Pi was
-- unreachable long enough to overflow both PSRAM and flash. Recorded explicitly:
-- the requirement is never to lose audio *silently*.
CREATE TABLE IF NOT EXISTS gaps (
    id          INTEGER PRIMARY KEY,
    boot_id     TEXT    NOT NULL,
    seq_from    INTEGER NOT NULL,
    seq_to      INTEGER NOT NULL,
    detected_at TEXT    NOT NULL,
    UNIQUE (boot_id, seq_from, seq_to)
);

-- ---------------------------------------------------------------- full-text search
-- External-content FTS5 index over transcripts.text. 'content=' means FTS5 stores
-- only the index and reads the text from transcripts, which avoids duplicating
-- every transcript on disk — but it also means SQLite will NOT keep the index in
-- sync by itself. The triggers below are required, not optional.
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5 (
    text,
    content       = 'transcripts',
    content_rowid = 'id',
    tokenize      = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts (transcripts_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
    INSERT INTO transcripts_fts (transcripts_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
    INSERT INTO transcripts_fts (rowid, text) VALUES (new.id, new.text);
END;

-- Convenience view: transcripts with their wall-clock time, newest first.
CREATE VIEW IF NOT EXISTS v_transcripts AS
SELECT t.id, t.chunk_id, c.started_at, c.duration_s, t.text, t.model, c.audio_path
FROM transcripts t
JOIN chunks c ON c.id = t.chunk_id
ORDER BY c.started_at DESC;
