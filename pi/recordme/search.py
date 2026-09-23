"""Search interface: FTS5 keyword search, plus local-LLM Q&A over the results.

This is the only service reachable from outside the LAN, over the Tailscale
tunnel. It binds to localhost/tunnel, not the LAN. Never port-forward it.

Build keyword search first and make it genuinely good — it does the heavy lifting,
and the LLM layer is only useful on top of solid retrieval.

Typing note: `Optional[...]` rather than `str | None` in the route signatures, since
FastAPI resolves annotations at import and dev machines may be on Python 3.9. See
the note at the top of db.py.
"""

import re
import secrets as pysecrets
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import db as dbmod
from .config import Config

app = FastAPI(title="recordMe search")
security = HTTPBasic()

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_cfg: Optional[Config] = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg


def db_conn(cfg: Config = Depends(get_config)):
    """One connection per request. See the same note in receiver.py."""
    conn = dbmod.connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_password(
    credentials: HTTPBasicCredentials = Depends(security),
    cfg: Config = Depends(get_config),
) -> None:
    """Defence in depth behind the tunnel.

    The tunnel does the real authentication work, but anything already on the
    tailnet can reach this service, so a password still buys something. Basic auth
    because it needs no session handling and browsers speak it natively. The
    username is not checked — there is only one user.
    """
    if not pysecrets.compare_digest(credentials.password, cfg.search_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bad password",
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------------------------------------------------------------- helpers


def normalize_since(value: Optional[str]) -> Optional[str]:
    """Accept a bare date as the start of that day, so `since=2026-09-22` works."""
    if value and _DATE_ONLY.match(value):
        return value + "T00:00:00.000Z"
    return value


def normalize_until(value: Optional[str]) -> Optional[str]:
    """Accept a bare date as the *end* of that day.

    Without this, `until=2026-09-22` would compare against midnight and silently
    exclude everything said that day — the opposite of what the caller meant.
    """
    if value and _DATE_ONLY.match(value):
        return value + "T23:59:59.999Z"
    return value


def matching_segment_offsets(segments: List[sqlite3.Row], query: str) -> List[float]:
    """Start offsets of segments that appear to contain a query term.

    A convenience for jumping to the right moment in the audio, not a second search
    engine: this is plain case-insensitive substring matching, so it can miss a
    stemmed FTS match (FTS finds "calling" for "call"; substring catches that case
    but not e.g. "ran" for "run"). The FTS hit is authoritative; this just narrows.
    """
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    return [
        float(s["start_s"])
        for s in segments
        if any(t in (s["text"] or "").lower() for t in terms)
    ]


def build_hits(conn: sqlite3.Connection, rows: List[sqlite3.Row], q: str) -> List[Dict[str, Any]]:
    hits = []
    for r in rows:
        segments = dbmod.segments_for(conn, r["transcript_id"])
        hits.append(
            {
                "chunk_id": r["chunk_id"],
                "transcript_id": r["transcript_id"],
                "started_at": r["started_at"],
                "duration_s": r["duration_s"],
                "snippet": r["snippet"],
                "text": r["text"],
                "model": r["model"],
                "audio_path": r["audio_path"],
                "match_offsets_s": matching_segment_offsets(segments, q),
                "segments": [
                    {"start": s["start_s"], "end": s["end_s"], "text": s["text"]}
                    for s in segments
                ],
            }
        )
    return hits


def run_search(
    conn: sqlite3.Connection,
    q: str,
    since: Optional[str],
    until: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    rows = dbmod.search(
        conn, q, limit=limit, since=normalize_since(since), until=normalize_until(until)
    )
    return {"query": q, "count": len(rows), "hits": build_hits(conn, rows, q)}


# ---------------------------------------------------------------- routes


@app.get("/search", dependencies=[Depends(require_password)])
async def search(
    q: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(db_conn),
):
    """Keyword search over transcripts, newest first.

    Returns timestamp, matching text with FTS5 snippet() highlighting, and the
    audio path. Segment timestamps let a hit point at the exact moment rather
    than just the chunk.
    """
    return await run_in_threadpool(run_search, conn, q, since, until, limit)


@app.get("/gaps", dependencies=[Depends(require_password)])
async def gaps(conn: sqlite3.Connection = Depends(db_conn)):
    """Recorded gaps — audio the device dropped.

    Exposed because "never lose audio silently" needs somewhere the loss is actually
    visible; a row in a table nobody reads is still silent.
    """
    rows = await run_in_threadpool(dbmod.list_gaps, conn, 100)
    return {
        "count": len(rows),
        "gaps": [
            {
                "boot_id": g["boot_id"],
                "seq_from": g["seq_from"],
                "seq_to": g["seq_to"],
                "detected_at": g["detected_at"],
            }
            for g in rows
        ],
    }


@app.get("/health", dependencies=[Depends(require_password)])
async def health(conn: sqlite3.Connection = Depends(db_conn)):
    depth = await run_in_threadpool(dbmod.queue_depth, conn)
    return {"ok": True, "queue": depth}


@app.post("/ask", dependencies=[Depends(require_password)])
async def ask(question: str):
    """Answer a question over the transcripts via Ollama.

    Retrieval-then-answer, not stuff-everything:
      1. Parse any time expression in the question ("on Tuesday") into a range.
      2. FTS5 search for candidate rows within that range.
      3. Pass only those rows to the LLM, each tagged with its timestamp.
      4. Return the answer alongside the source rows, so claims are checkable.

    Never feed a whole day of transcripts into the context window — retrieval is
    what makes this work on a Pi, where an 8B model runs at single-digit tokens
    per second.

    Deferred to build step 7 (CLAUDE.md section 11). Returns 501 rather than raising,
    so probing the API doesn't look like a server fault.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="LLM Q&A is build step 7; keyword search via /search is step 4",
    )
