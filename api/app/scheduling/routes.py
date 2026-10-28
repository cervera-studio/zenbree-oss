from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.scheduling import service
from app.scheduling.models import SavedSearch, SavedSearches, SavedSearchRun, SavedSearchRuns, SearchInput, Toggle

router = APIRouter(prefix='/v1/saved-searches', tags=['saved-searches'])


@router.get('', response_model=SavedSearches)
def list_searches(user: CurrentUser = Depends(current_user_or_api_key)):
    return {'saved_searches': service.list_searches()}


@router.post('', status_code=201, response_model=SavedSearch)
def create(body: SearchInput, user: CurrentUser = Depends(current_user_or_api_key)):
    return service.save(user.id, body)


@router.put('/{search_id}', response_model=SavedSearch)
def update(search_id: UUID, body: SearchInput, user: CurrentUser = Depends(current_user_or_api_key)):
    return service.save(user.id, body, search_id, admin=user.org_role == 'admin')


@router.patch('/{search_id}', response_model=SavedSearch)
def toggle(search_id: UUID, body: Toggle, user: CurrentUser = Depends(current_user_or_api_key)):
    return service.toggle(user.id, search_id, enabled=body.enabled, admin=user.org_role == 'admin')


@router.delete('/{search_id}', status_code=204)
def delete(search_id: UUID, user: CurrentUser = Depends(current_user_or_api_key)):
    service.toggle(user.id, search_id, delete=True, admin=user.org_role == 'admin')
    return Response(status_code=204)


@router.post('/{search_id}/run', status_code=202, response_model=SavedSearchRun | None)
def run(search_id: UUID, user: CurrentUser = Depends(current_user_or_api_key)):
    # The next minute beat dispatches this durable row, including after broker outages.
    return service.run_now(user.id, search_id, admin=user.org_role == 'admin')


@router.get('/{search_id}/runs', response_model=SavedSearchRuns)
def runs(search_id: UUID, user: CurrentUser = Depends(current_user_or_api_key)):
    with get_conn() as conn:
        service.owned(conn, user.id, search_id, manage=False)
        return {
            'runs': conn.execute(
                'SELECT * FROM saved_search_runs WHERE saved_search_id=%s ORDER BY created_at DESC LIMIT 100',
                (search_id,),
            ).fetchall()
        }
