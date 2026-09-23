"""Shared fixtures. Everything runs against a tmp SQLite file and tmp audio dir,
so the suite never touches the real DB or the HDD."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recordme import db as dbmod  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture()
def add_chunk(tmp_path):
    """Insert a chunk row with sane defaults; override any field via kwargs."""

    def _add(conn, boot_id="boot0", seq=0, **over):
        fields = dict(
            boot_id=boot_id,
            seq=seq,
            chunk_start_ms=seq * 30_000,
            # Lexicographic order matches seq order, which is what the worker's
            # "oldest first" claim relies on.
            started_at="2026-09-23T10:{:02d}:00.000Z".format(seq),
            duration_s=30.0,
            sample_rate=16000,
            audio_path=str(tmp_path / "{}-{:06d}.wav".format(boot_id, seq)),
            bytes=960_000,
            received_at="2026-09-23T10:{:02d}:05.000Z".format(seq),
        )
        fields.update(over)
        return dbmod.insert_chunk(conn, **fields)

    return _add
