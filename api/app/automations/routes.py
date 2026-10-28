"""Automations API (#939). The active project scopes every route, as for lists, campaigns and watchlists."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.automations import approval, instantly_step, preview, service
from app.automations.models import (
    Automation,
    AutomationIn,
    AutomationPatch,
    AutomationPreview,
    AutomationPreviewIn,
    AutomationRun,
    AutomationRunDetail,
    AutomationRuns,
    Automations,
    WebhookSecret,
)
from app.projects.routes import current_project_or_api_key

router = APIRouter(prefix='/v1/automations', tags=['automations'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)


@router.get('', response_model=Automations)
def list_automations(user: CurrentUser = Auth, project_id: str = Project):
    return {'automations': service.list_all(user, project_id)}


@router.post('', status_code=201, response_model=Automation)
def create(body: AutomationIn, user: CurrentUser = Auth, project_id: str = Project):
    return service.create(user, project_id, body)


@router.post('/preview', response_model=AutomationPreview)
async def preview_definition(body: AutomationPreviewIn, user: CurrentUser = Auth, project_id: str = Project):
    shown = await asyncio.to_thread(preview.preview, user, project_id, body)
    return await instantly_step.preview(shown, project_id, body.definition)


@router.patch('/{automation_id}', response_model=Automation)
def update(automation_id: UUID, body: AutomationPatch, user: CurrentUser = Auth, project_id: str = Project):
    return service.update(user, project_id, automation_id, body)


@router.delete('/{automation_id}', status_code=204)
def delete(automation_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    service.delete(user, project_id, automation_id)
    return Response(status_code=204)


@router.post('/{automation_id}/webhook/secret', response_model=WebhookSecret)
def webhook_secret(automation_id: UUID, response: Response, user: CurrentUser = Auth, project_id: str = Project):
    """A new signing secret: shown in this answer only, and the old one stops working."""
    response.headers['Cache-Control'] = 'no-store'
    return service.issue_secret(user, project_id, automation_id)


@router.post('/{automation_id}/run', status_code=202, response_model=AutomationRun)
def run(automation_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    # The next minute's beat takes this durable row, also after a broker outage.
    return service.run_now(user, project_id, automation_id)


@router.get('/{automation_id}/runs', response_model=AutomationRuns)
def runs(automation_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return {'runs': service.runs(user, project_id, automation_id)}


@router.get('/{automation_id}/runs/{run_id}', response_model=AutomationRunDetail)
def run_detail(automation_id: UUID, run_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return service.run_detail(user, project_id, automation_id, run_id)


@router.post('/{automation_id}/runs/{run_id}/cancel', response_model=AutomationRunDetail)
def cancel_run(automation_id: UUID, run_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return service.cancel_run(user, project_id, automation_id, run_id)


@router.post('/{automation_id}/runs/{run_id}/approve', response_model=AutomationRunDetail)
def approve_run(automation_id: UUID, run_id: UUID, user: CurrentUser = Auth, project_id: str = Project):
    return approval.approve(user, project_id, automation_id, run_id)
