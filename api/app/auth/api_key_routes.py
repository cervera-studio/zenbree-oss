"""Authenticated routes for the API keys of the caller's organization: its admins only (#308)."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.auth.api_keys import LABEL_MAX_LENGTH, issue_api_key, list_api_keys, revoke_api_key
from app.auth.middleware import CurrentUser, require_org_admin
from app.core.errors_http import unwrap_or_raise

router = APIRouter(prefix='/v1/me/api-keys', tags=['API keys'])


class IssueApiKeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class ApiKey(BaseModel):
    id: str
    label: str
    last4: str | None  # None for a key issued before it was stored
    created_at: str
    revoked_at: str | None


class IssuedKey(ApiKey):
    key: str


class ApiKeys(BaseModel):
    keys: list[ApiKey]


@router.post('', status_code=status.HTTP_201_CREATED, response_model=IssuedKey)
def create_api_key(body: IssueApiKeyIn, user: CurrentUser = Depends(require_org_admin)) -> dict:
    key = unwrap_or_raise(issue_api_key(user, body.label))
    return {
        'id': key.id,
        'label': key.label,
        'key': key.key,
        'last4': key.last4,
        'created_at': key.created_at,
        'revoked_at': None,
    }


@router.get('', response_model=ApiKeys)
def get_api_keys(user: CurrentUser = Depends(require_org_admin)) -> dict:
    return {'keys': unwrap_or_raise(list_api_keys(user.org_id))}


@router.delete('/{key_id}', response_model=ApiKey)
def delete_api_key(key_id: UUID, user: CurrentUser = Depends(require_org_admin)) -> dict:
    return unwrap_or_raise(revoke_api_key(user.org_id, str(key_id)))
