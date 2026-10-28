"""A connect that Docker Desktop's port forward drops is tried again (#760)."""

import contextlib
import math
import os
import socket
import threading
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tests import connect_retry


class Forward:
    """A port that closes its first ``drops`` connects at once, as Docker Desktop's forward does when its VM has no
    local port left, and pipes the later ones to ``target``."""

    def __init__(self, drops: float, target: tuple[str, int] | None = None):
        self.drops, self.target, self.accepted = drops, target, 0
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port = self.server.getsockname()[1]

    def __enter__(self) -> Forward:
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def __exit__(self, *_) -> None:
        self.server.close()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            self.accepted += 1
            if self.accepted <= self.drops:
                client.close()
                continue
            assert self.target
            upstream = socket.create_connection(self.target)
            for src, dst in ((client, upstream), (upstream, client)):
                threading.Thread(target=_pipe, args=(src, dst), daemon=True).start()


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    with contextlib.suppress(OSError):
        while data := src.recv(65536):
            dst.sendall(data)
    with contextlib.suppress(OSError):
        dst.shutdown(socket.SHUT_WR)


@pytest.fixture
def short_deadline(monkeypatch):
    """Waits of 0.05, 0.1 and 0.2 s fit in 0.5 s and the next one, 0.4 s, does not: four tries. The clock moves only
    when the retry waits, so a slow connect on a loaded machine does not change the count."""
    now = [0.0]

    def wait(seconds: float) -> None:
        now[0] += seconds

    async def wait_async(seconds: float) -> None:
        wait(seconds)

    monkeypatch.setattr(connect_retry, 'time', SimpleNamespace(monotonic=lambda: now[0], sleep=wait))
    monkeypatch.setattr(connect_retry, 'asyncio', SimpleNamespace(sleep=wait_async))
    monkeypatch.setattr(connect_retry, 'DEADLINE_S', 0.5)
    monkeypatch.setattr(connect_retry, '_down', False)


def _dropping_conninfo(port: int) -> str:
    # One try is one connect: with SSL or GSS allowed, libpq tries the port again without them.
    return make_conninfo(host='127.0.0.1', port=port, sslmode='disable', gssencmode='disable', connect_timeout=2)


@pytest.mark.usefixtures('short_deadline')
def test_a_connect_the_forward_keeps_dropping_fails_and_the_next_one_fails_at_once():
    with Forward(drops=math.inf) as forward:
        with pytest.raises(psycopg.OperationalError, match='server closed the connection unexpectedly'):
            psycopg.connect(_dropping_conninfo(forward.port))
        assert forward.accepted == 4
        with pytest.raises(psycopg.OperationalError, match='server closed the connection unexpectedly'):
            psycopg.connect(_dropping_conninfo(forward.port))
        assert forward.accepted == 5


@pytest.mark.usefixtures('short_deadline')
async def test_an_async_connect_the_forward_keeps_dropping_is_tried_again():
    with Forward(drops=math.inf) as forward:
        with pytest.raises(psycopg.OperationalError, match='server closed the connection unexpectedly'):
            await psycopg.AsyncConnection.connect(_dropping_conninfo(forward.port))
        assert forward.accepted == 4


def test_a_connect_the_forward_drops_twice_reaches_the_database():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL not set: SQL tests NOT run')
    parts = conninfo_to_dict(url)
    with Forward(drops=2, target=(str(parts.get('host', 'localhost')), int(parts.get('port', 5432)))) as forward:
        conninfo = make_conninfo(url, host='127.0.0.1', port=forward.port, sslmode='disable', gssencmode='disable')
        with psycopg.connect(conninfo) as conn:
            assert conn.execute('SELECT 1').fetchone() == (1,)
        assert forward.accepted == 3
