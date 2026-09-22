"""Search interface: FTS5 keyword search, plus local-LLM Q&A over the results.

SCAFFOLD — routes and contracts only.

This is the only service reachable from outside the LAN, over the Tailscale
tunnel. It binds to localhost/tunnel, not the LAN. Never port-forward it.

Build keyword search first and make it genuinely good — it does the heavy lifting,
and the LLM layer is only useful on top of solid retrieval.
"""

from fastapi import FastAPI

app = FastAPI(title="recordMe search")


@app.get("/search")
async def search(q: str, since: str | None = None, until: str | None = None):
    """Keyword search over transcripts, newest first.

    Returns timestamp, matching text with FTS5 snippet() highlighting, and the
    audio path. Segment timestamps let a hit point at the exact moment rather
    than just the chunk.
    """
    raise NotImplementedError  # TODO


@app.post("/ask")
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
    """
    raise NotImplementedError  # TODO
