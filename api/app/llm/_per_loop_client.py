"""Per-event-loop client caching, shared by every LLM provider whose SDK
client ties its connection pool to the asyncio loop it first runs on
(``anthropic.AsyncAnthropic``, ``httpx.AsyncClient``).

Extracted (backlog A4, 2026-07-16) after the identical implementation lived
twice — ``AnthropicProvider`` and ``OpenAICompatibleProvider`` each hand-wrote
the same ``_client_loop``/``_offloop_clients``/``_active_client`` triad, which
could silently drift (one gets a fix, the other doesn't) since nothing forced
them to stay in sync.
"""

from __future__ import annotations

import asyncio
import threading
import weakref


class PerLoopClientMixin[ClientT]:
    """``_active_client()``, bound to the running asyncio loop.

    A subclass must call :meth:`_init_per_loop_client` once in its own
    ``__init__`` (after everything :meth:`_build_client` depends on is set)
    and implement :meth:`_build_client`.
    """

    _client: ClientT
    _client_loop: asyncio.AbstractEventLoop | None
    _offloop_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ClientT]
    _offloop_lock: threading.Lock

    def _init_per_loop_client(self) -> None:
        self._client = self._build_client()
        # The loop the primary ``_client`` is bound to (adopted on first use).
        self._client_loop = None
        # Per-loop clients for off-loop callers, keyed weakly so an entry is
        # dropped when its loop is garbage-collected.
        self._offloop_clients = weakref.WeakKeyDictionary()
        self._offloop_lock = threading.Lock()

    def _build_client(self) -> ClientT:
        raise NotImplementedError

    def _active_client(self) -> ClientT:
        """Return a client bound to the running loop.

        The underlying SDK client wraps a connection pool tied to the loop it
        first runs on; reusing it from another loop raises "Event loop is
        closed" (e.g. the KG resolution ingestion path drives one
        ``asyncio.run`` per extracted entity — ``ResolveEntity.execute_sync``
        — so a process-lifetime client would break on the second ambiguous
        entity in a task). The primary ``_client`` serves the loop it is
        first used on (the hot chat path, and the loop test mocks patch); any
        other loop gets its own client so the primary one is never rebound
        out from under the main loop.
        """
        loop = asyncio.get_running_loop()
        if self._client_loop is None or self._client_loop is loop:
            self._client_loop = loop
            return self._client
        with self._offloop_lock:
            client = self._offloop_clients.get(loop)
            if client is None:
                client = self._build_client()
                self._offloop_clients[loop] = client
            return client
