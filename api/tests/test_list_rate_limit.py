import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.middleware import CurrentUser
from app.core import rate_limit as core_limit
from app.lists import rate_limit


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expirations = {}

    def eval(self, script, numkeys, key, seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] == 1:
            self.expirations[key] = int(seconds)
        return self.counts[key]


def _request(path):
    return Request({'type': 'http', 'method': 'GET', 'path': path, 'headers': [], 'query_string': b''})


def test_list_reads_are_limited_per_user_and_window(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(core_limit, '_redis', lambda: redis)
    user = CurrentUser(id='user-1', org_id='org-1')
    request = _request('/v1/lists/id/rows')

    for _ in range(60):
        rate_limit.limit_list_reads(request, user)
    with pytest.raises(HTTPException) as error:
        rate_limit.limit_list_reads(request, user)

    assert error.value.status_code == 429
    assert error.value.headers['Retry-After'] == '60'
    assert next(iter(redis.expirations.values())) == 60


def test_csv_export_has_a_separate_hourly_limit(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(core_limit, '_redis', lambda: redis)
    user = CurrentUser(id='user-1', org_id='org-1')
    request = _request('/v1/lists/id/export.csv')

    for _ in range(10):
        rate_limit.limit_list_reads(request, user)
    with pytest.raises(HTTPException) as error:
        rate_limit.limit_list_reads(request, user)

    assert error.value.status_code == 429
    assert error.value.headers['Retry-After'] == '3600'
    assert next(iter(redis.expirations.values())) == 3600


def test_the_window_counts_each_key_alone(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(core_limit, '_redis', lambda: redis)

    assert [core_limit.over_limit('a', 'x', 2, 60) for _ in range(3)] == [False, False, True]
    assert not core_limit.over_limit('b', 'x', 2, 60)


def test_a_reply_that_is_no_count_fails_open(monkeypatch):
    class NoScripts:
        def eval(self, *args):
            return None

    monkeypatch.setattr(core_limit, '_redis', NoScripts)

    assert not core_limit.over_limit('a', 'x', 1, 60)
