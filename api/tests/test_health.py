from contextlib import contextmanager

from fastapi.testclient import TestClient

from app import main
from app.main import Health, app


class _Redis:
    def __init__(self, up: bool):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError


def _stack(monkeypatch, *, db: bool, redis: bool):
    @contextmanager
    def conn(**_):
        if not db:
            raise OSError
        yield type('Conn', (), {'execute': staticmethod(lambda sql: None)})

    monkeypatch.setattr(main, 'get_conn', conn)
    monkeypatch.setattr(main, '_redis', lambda: _Redis(redis))


def test_health_is_200_when_every_check_passes(monkeypatch):
    _stack(monkeypatch, db=True, redis=True)
    res = TestClient(app).get('/health')
    assert res.status_code == 200
    assert Health.model_validate(res.json()) == Health(status='ok', db='ok', redis='ok')


def test_health_is_503_and_names_the_failed_check(monkeypatch):
    _stack(monkeypatch, db=False, redis=True)
    res = TestClient(app).get('/health')
    assert res.status_code == 503
    assert Health.model_validate(res.json()) == Health(status='degraded', db='error: OSError', redis='ok')


def test_openapi_documents_both_health_statuses():
    responses = app.openapi()['paths']['/health']['get']['responses']
    for status in ('200', '503'):
        assert responses[status]['content']['application/json']['schema'] == {'$ref': '#/components/schemas/Health'}
