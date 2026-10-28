"""Request body cap, enforced before any route reads the body (#419).

FastAPI reads a route's whole body before its auth dependencies run, so without a cap an anonymous
request of any size is buffered and parsed in the API process.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors_http import api_error

MAX_BODY_BYTES = 1_000_000
_TOO_LARGE = {'code': 'REQUEST.TOO_LARGE', 'message': 'payload too large'}
_INVALID = {'code': 'REQUEST.INVALID_INPUT', 'message': 'invalid Content-Length'}


class BodyLimit:
    """413 for a ``Content-Length`` over the cap without reading the body; a streamed body stops at the cap.

    ``limits`` maps an exact path to its own cap (the CSV upload takes more than the default).
    """

    def __init__(self, app: ASGIApp, limits: dict[str, int] | None = None) -> None:
        self.app = app
        self.limits = limits or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        cap = self.limits.get(scope['path'], MAX_BODY_BYTES)
        length = dict(scope['headers']).get(b'content-length', b'')
        if length and not length.isdigit():
            await JSONResponse({'detail': _INVALID}, status_code=400)(scope, receive, send)
            return
        if length and int(length) > cap:
            await JSONResponse({'detail': _TOO_LARGE}, status_code=413)(scope, receive, send)
            return
        read, started = 0, False

        async def capped() -> Message:
            nonlocal read
            message = await receive()
            if message['type'] == 'http.request':
                read += len(message.get('body', b''))
                if read > cap and started:
                    # A streaming response (SSE) reads receive() for the disconnect: end it instead of raising.
                    return {'type': 'http.disconnect'}
                if read > cap:
                    # FastAPI re-raises an HTTPException from its body read, so this answers 413 on every route.
                    raise api_error(413, _TOO_LARGE['code'], _TOO_LARGE['message'])
            return message

        async def tracked(message: Message) -> None:
            nonlocal started
            started = started or message['type'] == 'http.response.start'
            await send(message)

        await self.app(scope, capped, tracked)
