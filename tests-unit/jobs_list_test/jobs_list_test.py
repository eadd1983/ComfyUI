"""Tests for the ``ids`` batch filter on the jobs listing endpoint.

Covers both layers:

* the pure ``comfy_execution.jobs.get_all_jobs`` filtering logic (the ``ids``
  argument narrows the result, composes with ``status_filter``, and silently
  ignores ids that match nothing), and

* the HTTP contract of ``GET /api/jobs`` for the ``ids`` query parameter
  (a valid set narrows the response, an oversized set or a malformed id is
  rejected with 400).

As in ``jobs_cancel_test``, the HTTP layer is exercised against a small
aiohttp app whose handler is a faithful copy of the ``ids``-parsing wiring in
``server.py``, driven by a fake queue. This keeps the test free of the heavy
ComfyUI runtime (torch, nodes, ...) while still testing the real contract.
"""

import pytest
from aiohttp import web

from comfy_execution.jobs import (
    JobStatus,
    MAX_JOB_IDS_FILTER,
    get_all_jobs,
    validate_job_id,
)

# Canonical UUID ids (the endpoint validates UUID format).
_UUID_A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
_UUID_B = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
_UUID_C = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
_UUID_MISSING = "ffffffff-ffff-4fff-ffff-ffffffffffff"


def make_queue_item(prompt_id, priority=0):
    """Build a queue tuple shaped like the real ones (5 elements, id at index 1)."""
    return (priority, prompt_id, {}, {}, [])


def make_history_item(status_str="success"):
    """Build a history item dict shaped like the real ones."""
    return {
        "prompt": (0, "", {}, {}, []),
        "status": {"status_str": status_str, "messages": []},
        "outputs": {},
    }


# ---------------------------------------------------------------------------
# Pure get_all_jobs filtering logic
# ---------------------------------------------------------------------------


def test_ids_filter_returns_only_requested():
    running = [make_queue_item(_UUID_A)]
    queued = [make_queue_item(_UUID_B)]
    history = {_UUID_C: make_history_item()}

    jobs, total = get_all_jobs(running, queued, history, ids=[_UUID_A, _UUID_C])

    returned = {j["id"] for j in jobs}
    assert returned == {_UUID_A, _UUID_C}
    assert total == 2
    assert _UUID_B not in returned


def test_ids_filter_absent_returns_all():
    running = [make_queue_item(_UUID_A)]
    queued = [make_queue_item(_UUID_B)]
    history = {_UUID_C: make_history_item()}

    jobs, total = get_all_jobs(running, queued, history)

    assert {j["id"] for j in jobs} == {_UUID_A, _UUID_B, _UUID_C}
    assert total == 3


def test_ids_filter_empty_list_returns_all():
    """An empty list behaves like no filter (matches how status/workflow_id behave)."""
    running = [make_queue_item(_UUID_A)]
    queued = [make_queue_item(_UUID_B)]

    jobs, _ = get_all_jobs(running, queued, {}, ids=[])

    assert {j["id"] for j in jobs} == {_UUID_A, _UUID_B}


def test_ids_filter_unknown_id_silently_absent():
    """An id that matches nothing is simply not present (no error)."""
    running = [make_queue_item(_UUID_A)]

    jobs, total = get_all_jobs(running, [], {}, ids=[_UUID_A, _UUID_MISSING])

    assert {j["id"] for j in jobs} == {_UUID_A}
    assert total == 1


def test_ids_filter_composes_with_status():
    """ids only narrows; it composes with the status filter."""
    running = [make_queue_item(_UUID_A)]
    queued = [make_queue_item(_UUID_B)]
    history = {_UUID_C: make_history_item()}

    # Request A and C by id, but restrict to in_progress only -> just A.
    jobs, total = get_all_jobs(
        running, queued, history,
        status_filter=[JobStatus.IN_PROGRESS],
        ids=[_UUID_A, _UUID_C],
    )

    assert {j["id"] for j in jobs} == {_UUID_A}
    assert total == 1


# ---------------------------------------------------------------------------
# HTTP contract for the ids query parameter
# ---------------------------------------------------------------------------


class FakePromptQueue:
    """Minimal stand-in exposing the accessors get_jobs uses."""

    def __init__(self, running=None, queued=None, history=None):
        self._running = list(running or [])
        self._queued = list(queued or [])
        self._history = dict(history or {})

    def get_current_queue_volatile(self):
        return (list(self._running), list(self._queued))

    def get_history(self):
        return dict(self._history)


def make_app(prompt_queue):
    """Build an aiohttp app whose handler mirrors server.py's get_jobs ids wiring."""

    async def get_jobs(request):
        query = request.rel_url.query

        ids_param = query.get('ids')

        ids_filter = None
        if ids_param:
            ids_filter = [i.strip() for i in ids_param.split(',') if i.strip()]
            if len(ids_filter) > MAX_JOB_IDS_FILTER:
                return web.json_response(
                    {"error": f"ids must contain at most {MAX_JOB_IDS_FILTER} values"},
                    status=400
                )
            invalid_ids = []
            for jid in ids_filter:
                try:
                    validate_job_id(jid)
                except (ValueError, AttributeError):
                    invalid_ids.append(jid)
            if invalid_ids:
                return web.json_response(
                    {"error": "ids contains invalid id(s)", "invalid_ids": invalid_ids},
                    status=400
                )

        running, queued = prompt_queue.get_current_queue_volatile()
        history = prompt_queue.get_history()

        jobs, total = get_all_jobs(running, queued, history, ids=ids_filter)

        return web.json_response({
            'jobs': jobs,
            'pagination': {'total': total},
        })

    app = web.Application()
    app.router.add_get('/api/jobs', get_jobs)
    return app


@pytest.fixture
def queue():
    return FakePromptQueue(
        running=[make_queue_item(_UUID_A)],
        queued=[make_queue_item(_UUID_B)],
        history={_UUID_C: make_history_item()},
    )


@pytest.mark.asyncio
async def test_http_ids_filter_narrows(aiohttp_client, queue):
    client = await aiohttp_client(make_app(queue))

    resp = await client.get(f"/api/jobs?ids={_UUID_A},{_UUID_C}")
    assert resp.status == 200
    body = await resp.json()
    assert {j["id"] for j in body["jobs"]} == {_UUID_A, _UUID_C}


@pytest.mark.asyncio
async def test_http_ids_unknown_id_is_not_an_error(aiohttp_client, queue):
    client = await aiohttp_client(make_app(queue))

    resp = await client.get(f"/api/jobs?ids={_UUID_A},{_UUID_MISSING}")
    assert resp.status == 200
    body = await resp.json()
    assert {j["id"] for j in body["jobs"]} == {_UUID_A}


@pytest.mark.asyncio
async def test_http_ids_over_limit_returns_400(aiohttp_client, queue):
    client = await aiohttp_client(make_app(queue))

    too_many = ",".join([_UUID_A] * (MAX_JOB_IDS_FILTER + 1))
    resp = await client.get(f"/api/jobs?ids={too_many}")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_http_ids_invalid_id_returns_400(aiohttp_client, queue):
    client = await aiohttp_client(make_app(queue))

    resp = await client.get(f"/api/jobs?ids={_UUID_A},not-a-uuid")
    assert resp.status == 400
    body = await resp.json()
    assert "not-a-uuid" in body["invalid_ids"]


@pytest.mark.asyncio
async def test_http_ids_absent_returns_all(aiohttp_client, queue):
    client = await aiohttp_client(make_app(queue))

    resp = await client.get("/api/jobs")
    assert resp.status == 200
    body = await resp.json()
    assert {j["id"] for j in body["jobs"]} == {_UUID_A, _UUID_B, _UUID_C}
