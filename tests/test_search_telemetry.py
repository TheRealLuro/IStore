"""Unit tests for search-score telemetry consent gating (Sprint I D3).

We don't exercise the live DB here — just verify the consent gate:
no row is added when the user hasn't opted in, and a correctly
shaped row IS added when they have. The session is a lightweight
stub that records `.add()` calls.
"""

import pytest

import backend.api.search as search_mod


class _StubImage:
    def __init__(self, id_):
        self.id = id_


class _StubSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_no_log_when_not_opted_in(monkeypatch):
    async def fake_scope(session, user_id, kind):
        assert kind == "bandit_compression_telemetry"
        return False

    monkeypatch.setattr(
        "backend.consent.is_scope_active", fake_scope
    )
    session = _StubSession()
    ranked = [(_StubImage("a"), 0.9), (_StubImage("b"), 0.5)]
    await search_mod._log_search_telemetry(session, "user-1", "beach", ranked)
    assert session.added == []  # nothing logged without consent


@pytest.mark.asyncio
async def test_logs_top_10_when_opted_in(monkeypatch):
    async def fake_scope(session, user_id, kind):
        return True

    monkeypatch.setattr(
        "backend.consent.is_scope_active", fake_scope
    )
    session = _StubSession()
    # 15 results — only top 10 should be recorded.
    ranked = [(_StubImage(f"img{i}"), 1.0 - i * 0.05) for i in range(15)]
    await search_mod._log_search_telemetry(
        session, "user-1", "snowy hike", ranked
    )
    assert len(session.added) == 1
    row = session.added[0]
    assert row.query == "snowy hike"
    assert row.result_count == 15
    assert len(row.top_results) == 10
    assert row.top_results[0] == {"image_id": "img0", "score": 1.0}
    assert row.weights == {
        "clip": search_mod._W_CLIP,
        "text": search_mod._W_TEXT,
    }


@pytest.mark.asyncio
async def test_query_truncated_to_200(monkeypatch):
    async def fake_scope(session, user_id, kind):
        return True

    monkeypatch.setattr("backend.consent.is_scope_active", fake_scope)
    session = _StubSession()
    long_q = "x" * 500
    await search_mod._log_search_telemetry(session, "u", long_q, [])
    assert len(session.added[0].query) == 200
