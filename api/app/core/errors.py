"""
Result pattern — errors as values, never exceptions.

Use cases return Result[T]. Routers translate to HTTP at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeIs

#: The only ``context`` keys a client sees (#2696): what a caller needs to fix
#: its own request. Everything else in ``context`` is diagnostics and stays in
#: the log line — provider bodies and ids ride there today.
#: ``blocked_by`` is the other shape of "what to fix": not a value the caller
#: should have passed, but the rows that stand in the way of the operation it
#: asked for — ``{'count': int, ...ids}``.
RECOVERY_KEYS = frozenset(
    {
        'suggestion',
        'valid_values',
        'status_breakdown',
        'max_existing',
        'blocked_by',
    }
)


@dataclass(frozen=True, slots=True)
class AppError:
    """Domain error — never raised, always returned."""

    code: str
    detail: str = ''
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.code.split('.')[0]

    def to_response(self) -> dict[str, Any]:
        recovery = {k: v for k, v in self.context.items() if k in RECOVERY_KEYS}
        return {'error': {'code': self.code, 'message': self.detail, **recovery}}

    def to_log_dict(self) -> dict[str, Any]:
        return {
            'error_code': self.code,
            'error_detail': self.detail,
            'error_domain': self.domain,
            **{f'error_ctx_{k}': v for k, v in self.context.items()},
        }


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Err:
    error: AppError

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True


type Result[T] = Ok[T] | Err


def is_ok[T](result: Result[T]) -> TypeIs[Ok[T]]:
    """Narrow a ``Result`` to ``Ok`` at the call site.

    Module-level (not a method) and typed ``TypeIs`` so mypy narrows the
    ``Ok[T] | Err`` union for the caller: after ``if is_err(r): return r`` the
    fall-through is typed ``Ok[T]``, so ``r.value`` type-checks without a cast.
    The ``Ok.is_ok()`` / ``Err.is_err()`` methods stay for runtime and
    back-compat but cannot narrow ``self`` — a bool-returning method never
    refines the receiver's type.
    """
    return isinstance(result, Ok)


def is_err[T](result: Result[T]) -> TypeIs[Err]:
    """Narrow a ``Result`` to ``Err`` at the call site — see :func:`is_ok`."""
    return isinstance(result, Err)


def ok[T](value: T) -> Ok[T]:
    return Ok(value)


def err(code: str, detail: str = '', **ctx: Any) -> Err:
    return Err(AppError(code=code, detail=detail, context=ctx))
