"""Watchlists API (E13.3, #647). The active project scopes every route, as for lists and campaigns."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.projects.routes import current_project_or_api_key
from app.watchlists import service
from app.watchlists.models import (
    Signals,
    Watchlist,
    WatchlistIn,
    WatchlistPatch,
    WatchlistRun,
    WatchlistRuns,
    Watchlists,
)

router = APIRouter(prefix='/v1/watchlists', tags=['watchlists'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)


@router.get('', response_model=Watchlists)
def list_watchlists(user: CurrentUser = Auth, project_id: str = Project):
    return {'watchlists': service.list_all(user, project_id)}


@router.post('', status_code=201, response_model=Watchlist)
def create(body: WatchlistIn, user: CurrentUser = Auth, project_id: str = Project):
    return service.create(user, project_id, body)


@router.patch('/{watchlist_id}', response_model=Watchlist)
def update(watchlist_id: UUID, body: WatchlistPatch, user: CurrentUser = Auth, project_id: str = Project):
    return service.update(user, project_id, watchlist_id, body)


@router.delete('/{watchlist_id}', status_code=204)
def delete(watchlist_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    service.delete(user, project_id, watchlist_id)
    return Response(status_code=204)


@router.post('/{watchlist_id}/run', status_code=202, response_model=WatchlistRun | None)
def run(watchlist_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    # The next minute's beat takes this durable row, also after a broker outage.
    return service.run_now(user, project_id, watchlist_id)


@router.get('/{watchlist_id}/runs', response_model=WatchlistRuns)
def runs(watchlist_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return {'runs': service.runs(user, project_id, watchlist_id)}


@router.get('/{watchlist_id}/signals', response_model=Signals)
def signals(watchlist_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return {'signals': service.signals(user, project_id, watchlist_id)}
