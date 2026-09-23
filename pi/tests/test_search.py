"""Tests for the search service."""

import pytest
from fastapi.testclient import TestClient

from recordme import db as dbmod
from recordme import search as searchmod
from recordme.config import Config

PASSWORD = "search-pw"
AUTH = ("user", PASSWORD)


@pytest.fixture()
def cfg(tmp_path):
    return Config(
        upload_token="tok",
        db_path=tmp_path / "test.db",
        audio_dir=tmp_path / "audio",
        receiver_host="127.0.0.1",
        receiver_port=8000,
        search_host="127.0.0.1",
        search_port=8001,
        search_password=PASSWORD,
        whisper_model="small.en",
        whisper_compute="int8",
        whisper_threads=4,
        vad_enabled=True,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="llama3.1:8b",
    )


@pytest.fixture()
def client(cfg, conn):
    searchmod.app.dependency_overrides[searchmod.get_config] = lambda: cfg
    searchmod.app.dependency_overrides[searchmod.db_conn] = lambda: conn
    yield TestClient(searchmod.app)
    searchmod.app.dependency_overrides.clear()


@pytest.fixture()
def seeded(conn, add_chunk):
    """Two transcripts on different days, with segment timestamps."""
    a = add_chunk(conn, seq=0, started_at="2026-09-22T09:15:00.000Z")
    dbmod.save_transcript(
        conn, a, "remember to call the dentist about the appointment",
        model="faster-whisper/small.en",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "remember to call the dentist"},
            {"start": 2.0, "end": 4.0, "text": "about the appointment"},
        ],
    )
    b = add_chunk(conn, seq=1, started_at="2026-09-23T14:30:00.000Z")
    dbmod.save_transcript(
        conn, b, "the dentist rescheduled us to Friday",
        model="faster-whisper/small.en",
        segments=[{"start": 0.0, "end": 3.0, "text": "the dentist rescheduled us to Friday"}],
    )
    return a, b


# ---------------------------------------------------------------- auth


def test_search_requires_a_password(client, seeded):
    assert client.get("/search", params={"q": "dentist"}).status_code == 401


def test_wrong_password_is_rejected(client, seeded):
    r = client.get("/search", params={"q": "dentist"}, auth=("user", "wrong"))
    assert r.status_code == 401


def test_gaps_and_health_are_also_protected(client):
    assert client.get("/gaps").status_code == 401
    assert client.get("/health").status_code == 401


# ---------------------------------------------------------------- search


def test_search_returns_hits_newest_first(client, seeded):
    body = client.get("/search", params={"q": "dentist"}, auth=AUTH).json()
    assert body["count"] == 2
    assert body["hits"][0]["started_at"] > body["hits"][1]["started_at"]


def test_hit_includes_snippet_highlighting(client, seeded):
    body = client.get("/search", params={"q": "dentist"}, auth=AUTH).json()
    assert "[dentist]" in body["hits"][0]["snippet"]


def test_hit_includes_audio_path_and_segments(client, seeded):
    body = client.get("/search", params={"q": "appointment"}, auth=AUTH).json()
    hit = body["hits"][0]
    assert hit["audio_path"].endswith(".wav")
    assert len(hit["segments"]) == 2
    assert hit["model"] == "faster-whisper/small.en"


def test_match_offsets_point_at_the_moment(client, seeded):
    """The reason segments are stored at all: jump to the second, not the chunk."""
    body = client.get("/search", params={"q": "appointment"}, auth=AUTH).json()
    assert body["hits"][0]["match_offsets_s"] == [2.0]


def test_no_results_is_an_empty_list_not_an_error(client, seeded):
    body = client.get("/search", params={"q": "helicopter"}, auth=AUTH).json()
    assert body["count"] == 0
    assert body["hits"] == []


def test_multiple_terms_narrow_the_result(client, seeded):
    body = client.get("/search", params={"q": "dentist Friday"}, auth=AUTH).json()
    assert body["count"] == 1


def test_limit_is_applied(client, seeded):
    body = client.get("/search", params={"q": "dentist", "limit": 1}, auth=AUTH).json()
    assert body["count"] == 1


def test_absurd_limit_is_rejected(client, seeded):
    assert client.get(
        "/search", params={"q": "dentist", "limit": 99999}, auth=AUTH
    ).status_code == 422


def test_missing_q_is_422(client):
    assert client.get("/search", auth=AUTH).status_code == 422


@pytest.mark.parametrize("nasty", ['"', "a*", "NEAR(", "foo:bar", "^x", "-a", "(", "a OR"])
def test_fts_syntax_in_the_query_does_not_500(client, seeded, nasty):
    assert client.get("/search", params={"q": nasty}, auth=AUTH).status_code == 200


# ---------------------------------------------------------------- date filters


def test_bare_date_since_filters_by_day(client, seeded):
    body = client.get(
        "/search", params={"q": "dentist", "since": "2026-09-23"}, auth=AUTH
    ).json()
    assert body["count"] == 1
    assert body["hits"][0]["started_at"].startswith("2026-09-23")


def test_bare_date_until_includes_that_whole_day(client, seeded):
    """until=<date> must mean end-of-day, or it excludes everything said that day."""
    body = client.get(
        "/search", params={"q": "dentist", "until": "2026-09-22"}, auth=AUTH
    ).json()
    assert body["count"] == 1
    assert body["hits"][0]["started_at"].startswith("2026-09-22")


def test_single_day_window(client, seeded):
    body = client.get(
        "/search",
        params={"q": "dentist", "since": "2026-09-22", "until": "2026-09-22"},
        auth=AUTH,
    ).json()
    assert body["count"] == 1


def test_full_timestamps_still_work(client, seeded):
    body = client.get(
        "/search",
        params={"q": "dentist", "since": "2026-09-23T00:00:00.000Z"},
        auth=AUTH,
    ).json()
    assert body["count"] == 1


def test_normalizers_leave_full_timestamps_alone():
    assert searchmod.normalize_since("2026-09-22") == "2026-09-22T00:00:00.000Z"
    assert searchmod.normalize_until("2026-09-22") == "2026-09-22T23:59:59.999Z"
    assert searchmod.normalize_since(None) is None
    full = "2026-09-22T12:00:00.000Z"
    assert searchmod.normalize_since(full) == full
    assert searchmod.normalize_until(full) == full


# ---------------------------------------------------------------- gaps and /ask


def test_gaps_endpoint_surfaces_dropped_audio(client, conn, add_chunk):
    add_chunk(conn, seq=0)
    add_chunk(conn, seq=3)
    dbmod.detect_gaps(conn, "boot0")

    body = client.get("/gaps", auth=AUTH).json()
    assert body["count"] == 1
    assert (body["gaps"][0]["seq_from"], body["gaps"][0]["seq_to"]) == (1, 2)


def test_ask_is_a_clean_501_not_a_500(client):
    """It's build step 7. Probing it shouldn't look like a server fault."""
    r = client.post("/ask", params={"question": "what did I say about the dentist"}, auth=AUTH)
    assert r.status_code == 501
    assert "step 7" in r.json()["detail"]
