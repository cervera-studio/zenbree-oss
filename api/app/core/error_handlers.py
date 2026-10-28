"""The errors FastAPI and Starlette answer on their own, in the shape every route uses: ``{detail: {code, message}}``."""

from collections.abc import Awaitable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.scope import ActorGone, ScopeChangeWhileOpen, ScopeMissing

_HTTP_CODES = {404: 'REQUEST.NOT_FOUND', 405: 'REQUEST.METHOD_NOT_ALLOWED'}
_INTERNAL = {'code': 'INTERNAL.UNEXPECTED', 'message': 'internal error'}
_NOT_A_MEMBER = {'code': 'AUTH.NOT_A_MEMBER', 'message': 'Not a member of this organization'}


class InvalidField(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class InvalidInputDetail(BaseModel):
    code: str = Field(description='REQUEST.INVALID_INPUT for request validation; a route may send its own 422 code.')
    message: str
    fields: list[InvalidField] | None = Field(default=None, description='Only on REQUEST.INVALID_INPUT.')


class InvalidInputError(BaseModel):
    detail: InvalidInputDetail


async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    # No `input`: it echoes the request body, which carries prospects' PII.
    fields = [{'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]
    detail = {'code': 'REQUEST.INVALID_INPUT', 'message': 'invalid request', 'fields': fields}
    return JSONResponse({'detail': detail}, status_code=422)


async def _http(request: Request, exc: HTTPException) -> JSONResponse:
    detail: object = exc.detail
    if not isinstance(detail, dict):  # Starlette's own: an unknown route, a wrong method
        detail = {'code': _HTTP_CODES.get(exc.status_code, f'REQUEST.HTTP_{exc.status_code}'), 'message': detail}
    return JSONResponse({'detail': detail}, status_code=exc.status_code, headers=exc.headers)


async def _scope_bugs_as_errors(call: Awaitable[None]) -> None:
    """The two scope bugs are BaseExceptions, so no ``except Exception`` in the app hides them. Error reporting and
    the server's own 500 catch only ``Exception``, so they go on as one, with the bug as its cause. ``ActorGone`` is
    no bug: ``ActorGoneIs401``, outside ``InternalError``, answers it."""
    try:
        await call
    except (ScopeMissing, ScopeChangeWhileOpen) as bug:
        raise RuntimeError(f'{type(bug).__name__}: {bug}') from bug


class InternalError:
    """A 500 in the shared shape. Added inside CORS, unlike Starlette's ServerErrorMiddleware, so the browser can
    read it. Re-raises, so the server still logs the traceback and error reporting still sees it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            started = started or message['type'] == 'http.response.start'
            await send(message)

        try:
            await _scope_bugs_as_errors(self.app(scope, receive, tracked))
        except Exception:
            # A streaming response that already started cannot change its status: only the server log has it.
            if scope['type'] == 'http' and not started:
                await JSONResponse({'detail': _INTERNAL}, status_code=500)(scope, receive, send)
            raise


class ActorGoneIs401:
    """A member removed or banned mid-request: ``ActorGone`` raised at a later checkout becomes the 401
    ``current_user`` gives at the first one. A middleware, since an exception handler for a ``BaseException`` never
    fires: Starlette's handlers catch ``Exception``. A stream that already started ends its body instead, and the
    client's reconnect gets the 401."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            started = started or message['type'] == 'http.response.start'
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except ActorGone:
            if scope['type'] != 'http':
                raise
            if started:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            else:
                await JSONResponse({'detail': _NOT_A_MEMBER}, status_code=401, headers={'WWW-Authenticate': 'Bearer'})(
                    scope, receive, send
                )


def _document_invalid_input(app: FastAPI) -> None:
    """FastAPI documents its own 422 body, which ``_validation`` replaces: swap in the real model."""
    default = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = default()
        schemas = schema['components']['schemas']
        for name in ('HTTPValidationError', 'ValidationError'):
            schemas.pop(name, None)
        model = InvalidInputError.model_json_schema(ref_template='#/components/schemas/{model}')
        for name, definition in {**model.pop('$defs'), InvalidInputError.__name__: model}.items():
            schemas[name] = definition
        ref = {'$ref': f'#/components/schemas/{InvalidInputError.__name__}'}
        for operations in schema['paths'].values():
            for operation in operations.values():
                response = operation.get('responses', {}).get('422')
                if response:
                    response['content'] = {'application/json': {'schema': ref}}
        return schema

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's documented way to customize the schema


def install(app: FastAPI) -> None:
    """Call before CORS is added, so ``InternalError`` runs inside it."""
    # Starlette types a handler's exception as Exception, never the class it is registered for.
    app.add_exception_handler(RequestValidationError, _validation)  # type: ignore[arg-type]  # see above
    app.add_exception_handler(HTTPException, _http)  # type: ignore[arg-type]  # see above
    app.add_middleware(InternalError)
    app.add_middleware(ActorGoneIs401)
    _document_invalid_input(app)
