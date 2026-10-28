"""Every Celery task declares whose work it is (PR 8 of the organization-scoping run):

- ``member_task``: runs as the ``Member`` who queued it. Person work: a turn, an AI column fill, a CSV import, a run
  of a saved search or a watchlist for its owner. A member removed or banned since gets ``ActorGone`` at the task's
  next checkout, and the task ends there.
- ``org_task``: runs as ``ForOrg`` of the organization that queued it, from a ``Member`` or a ``ForOrg``. Work the
  organization owns, which goes on after the member who caused it left (#333): webhook deliveries, the balance rules,
  auto top-up, reply labels and drafts.
- ``system_task(reason)``: a beat task that crosses organizations as ``System(reason)``. Its body selects due work
  with its organization (and owner), then queues or runs each unit under ``acting_for`` or ``acting_as``.

Publishing: ``_stamp``, a ``before_task_publish`` handler, writes the publisher's scope into the message header
``zb_scope``. A member task queued outside a ``Member``, or an organization task queued outside a ``Member`` or a
``ForOrg``, raises ``ScopeMissing`` at ``.delay``, so the error names the enqueue site, not the worker.

Running: the task enters the header's scope before its body and leaves it when the body ends. A prefork child runs
many tasks in one process, so a scope left set would reach the next one. A task called in the process (directly, or
with ``apply``) runs in its caller's scope, the one a ``.delay`` there would publish. A message with no header was
queued by an image from before the header: it is dropped with a log line.

The scope travels in the header, never in the arguments: an argument that names a row of another organization then
finds nothing (row-level security), instead of acting there.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

import structlog
from celery import Celery, Task
from celery.signals import before_task_publish

from app.core import scope as scopes
from app.core.system_reasons import SystemReason

logger = structlog.get_logger()

HEADER = 'zb_scope'
MEMBER, ORG = 'member', 'org'
type Kind = str | SystemReason  # MEMBER, ORG, or the reason of a system task

#: The kind of each declared task, by name: ``_stamp`` gets the name only.
KINDS: dict[str, Kind] = {}


class _Declared(Task):
    zb_scope: Kind = ''

    # Not Task.__call__, which pushes a request with no headers and no retry count: the worker already pushed the
    # message's request, and a bound body reads it (Celery's documented pattern for a custom __call__).
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with _published(self, self.request) as runs:
            if not runs:
                return None
            try:
                return self.run(*args, **kwargs)
            # The member left or was banned: their work ends here. Caught at the task's edge, not deeper: a
            # BaseException that leaves the task kills the prefork child, and autoretry_for retries Exceptions only.
            except scopes.ActorGone as gone:
                logger.info('task.member_gone', task=self.name, org_id=gone.scope.org)
                return None


def member_task(app: Celery, **options: Any):
    """A task that runs as the ``Member`` who queued it."""
    return _declare(app, MEMBER, options)


def org_task(app: Celery, **options: Any):
    """A task that runs as ``ForOrg`` of the organization that queued it."""
    return _declare(app, ORG, options)


def system_task(app: Celery, reason: SystemReason, /, **options: Any):
    """A beat task that crosses organizations: select due work, then queue or run each unit in its organization."""
    return _declare(app, reason, options)


def _declare(app: Celery, kind: Kind, options: dict[str, Any]):
    def register(fn: Callable[..., Any]) -> Any:
        KINDS[options['name']] = kind
        return app.task(base=_Declared, zb_scope=kind, **options)(fn)

    return register


@before_task_publish.connect
def _stamp(sender: str | None = None, headers: dict[str, Any] | None = None, **_: Any) -> None:
    kind = KINDS.get(sender or '')
    if kind not in (MEMBER, ORG) or headers is None:
        return
    scope = scopes.current_or_none()
    # ScopeMissing, a BaseException: Celery logs and drops an Exception that a publish handler raises, and the
    # task would go out with no header.
    if isinstance(scope, scopes.Member):
        headers[HEADER] = {'org': scope.org, 'user': scope.user}
    elif isinstance(scope, scopes.ForOrg) and kind == ORG:
        headers[HEADER] = {'org': scope.org}
    else:
        who = 'a Member' if kind == MEMBER else 'a Member or ForOrg'
        raise scopes.ScopeMissing(f'{sender} runs as the {kind} that queued it: queue it in {who} scope, not {scope}')


@contextmanager
def _published(task: _Declared, request: Any) -> Iterator[bool]:
    """Enter the scope ``task`` runs in. Yields False when it must not run: a message with no header."""
    kind = task.zb_scope
    if isinstance(kind, SystemReason):
        with scopes.system(kind):
            yield True
        return
    header = request.get(HEADER)
    caller = scopes.current_or_none()
    in_process = header is None and (request.called_directly or request.is_eager)
    if in_process and caller is not None:  # the scope a .delay here would publish
        header = _header_of(kind, caller, task.name)
    elif in_process:
        raise scopes.ScopeMissing(f'{task.name} runs as the {kind} that queued it: call it in that scope')
    if header is None:
        logger.error('task.no_scope_header', task=task.name)
        yield False
        return
    entered: AbstractContextManager[object]
    if in_process and caller is not None and (kind == MEMBER or isinstance(caller, scopes.ForOrg)):
        entered = nullcontext()  # already in it: entering again would refuse a connection the caller holds
    elif kind == MEMBER:
        entered = scopes.acting_as(header['org'], header['user'])
    else:
        entered = scopes.acting_for(header['org'], f'task {task.name}')
    with entered:  # leaves the scope however the body ends: the next task in this process starts with none
        yield True


def _header_of(kind: Kind, scope: scopes.Scope, name: str) -> dict[str, str]:
    if isinstance(scope, scopes.Member):
        return {'org': scope.org, 'user': scope.user}
    if isinstance(scope, scopes.ForOrg) and kind == ORG:
        return {'org': scope.org}
    raise scopes.ScopeMissing(f'{name} runs as the {kind} that queued it: call it in that scope, not {scope}')


def queued_for(org_id: str, why: str) -> AbstractContextManager:
    """The scope to queue an organization task for ``org_id`` in: the caller's when it already acts in ``org_id``,
    else ``ForOrg(org_id)``. For a caller that names the organization from its data, often in a System scope."""
    current = scopes.current_or_none()
    if isinstance(current, scopes.Member | scopes.ForOrg) and current.org == org_id:
        return nullcontext()
    return scopes.acting_for(org_id, why)


def queue_as_owners(app: Celery, task: str, runs: list[tuple[str, str, str]]) -> int:
    """Queue ``task`` for each ``(organization, owner, run id)`` as its owner: the run stops if they leave."""
    for org_id, owner_id, run_id in runs:
        try:
            with scopes.acting_as(org_id, owner_id):
                app.send_task(task, args=[run_id])
        except Exception as exc:
            logger.error('scheduled_run.enqueue_failed', task=task, run_id=run_id, error_type=type(exc).__name__)
    return len(runs)


def for_member(org: str, user: str, unit: Callable[[], Any]) -> Any:
    """One unit of a beat's fan-out, as ``Member(org, user)``. None when that member left since the beat selected
    the unit: this unit ends, and the beat goes on with the others."""
    with as_member(org, user):
        return unit()
    return None


@contextmanager
def as_member(org: str, user: str) -> Iterator[None]:
    """A block of a beat's fan-out, as ``Member(org, user)``. A member gone since the beat selected the block ends
    that block only, at its next checkout: the ``with`` swallows ``ActorGone``, and the code after it runs. For an
    async block too, where ``for_member`` cannot take the unit."""
    try:
        with scopes.acting_as(org, user):
            yield
    except scopes.ActorGone:
        logger.info('task.unit_member_gone', org_id=org)


async def each_org[T](orgs: Iterable[str], why: str, unit: Callable[[str], Awaitable[T]]) -> list[T]:
    """``unit(org)`` for every organization at once, each as ``ForOrg(org)``, and the results of the units that
    returned. Each runs in a copy of the caller's context, so a scope that one unit enters, here or deeper, never
    reaches another unit at its next ``await``. A unit that raises is logged and left out of the results: the others
    run on."""

    async def one(org: str) -> list[T]:
        try:
            with scopes.acting_for(org, why):
                return [await unit(org)]
        except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
            raise
        # BaseException: a scope error (ScopeMissing, ScopeChangeWhileOpen) derives from it, and one let out of here
        # would end ``gather`` and, under ``asyncio.run``, cancel the other organizations' units.
        except BaseException as e:
            logger.exception('task.org_unit_failed', org_id=org, why=why, error_type=type(e).__name__)
            return []

    units = (asyncio.create_task(one(org), context=contextvars.copy_context()) for org in orgs)
    return [result for done in await asyncio.gather(*units) for result in done]
