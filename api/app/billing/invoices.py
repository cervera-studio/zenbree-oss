"""Read-only Stripe documents. These are not proof of Italian SDI delivery."""

from datetime import UTC, datetime

import stripe
import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from stripe.params import InvoiceListParams

from app.auth.middleware import CurrentUser, require_org_admin
from app.billing.routes import _stored_customer_id
from app.core.config import settings
from app.core.errors_http import api_error

router = APIRouter(prefix='/v1/billing', tags=['billing'])
logger = structlog.get_logger()


def _document(invoice: dict) -> dict:
    net = invoice.get('total_excluding_tax')
    tax = invoice['total'] - net if net is not None else None
    return {
        'id': invoice['id'],
        'number': invoice.get('number'),
        'created_at': datetime.fromtimestamp(invoice['created'], UTC).isoformat(),
        'status': invoice.get('status'),
        'currency': invoice['currency'],
        'net_amount': net,
        'tax_amount': tax,
        'total_amount': invoice['total'],
        'pdf_url': invoice.get('invoice_pdf') if invoice.get('status') != 'draft' else None,
        'hosted_url': invoice.get('hosted_invoice_url') if invoice.get('status') != 'draft' else None,
    }


class Invoice(BaseModel):
    id: str
    number: str | None
    created_at: str
    status: str | None
    currency: str
    net_amount: int | None
    tax_amount: int | None
    total_amount: int
    pdf_url: str | None
    hosted_url: str | None


class Invoices(BaseModel):
    items: list[Invoice]
    next_cursor: str | None
    has_customer: bool
    portal_available: bool


@router.get('/invoices', response_model=Invoices)
def invoices(
    limit: int = Query(20, ge=1, le=100),
    starting_after: str | None = Query(None, pattern=r'^in_[A-Za-z0-9]+$', max_length=255),
    user: CurrentUser = Depends(require_org_admin),
) -> dict:
    customer_id = _stored_customer_id(user.org_id)
    empty: dict = {
        'items': [],
        'next_cursor': None,
        'has_customer': bool(customer_id),
        'portal_available': bool(customer_id),
    }
    if not customer_id:
        return empty
    if not settings.stripe_secret_key:
        raise api_error(503, 'BILLING.INVOICES_UNAVAILABLE', 'Billing documents are unavailable right now')
    try:
        if starting_after:
            cursor = stripe.Invoice.retrieve(starting_after, api_key=settings.stripe_secret_key)
            if cursor.customer != customer_id:
                raise api_error(400, 'BILLING.INVALID_CURSOR', 'Invalid invoice cursor')
        params: InvoiceListParams = {'customer': customer_id, 'limit': limit, 'api_key': settings.stripe_secret_key}
        if starting_after:
            params['starting_after'] = starting_after
        page = stripe.Invoice.list(**params)
    except stripe.InvalidRequestError as e:
        if starting_after and e.param == 'id':
            raise api_error(400, 'BILLING.INVALID_CURSOR', 'Invalid invoice cursor') from e
        raise api_error(502, 'BILLING.INVOICES_UNAVAILABLE', 'Could not load billing documents') from e
    except stripe.StripeError as e:
        logger.warning('stripe.invoices.failed', error_type=type(e).__name__)
        raise api_error(502, 'BILLING.INVOICES_UNAVAILABLE', 'Could not load billing documents') from e
    items = [_document(inv.to_dict()) for inv in page.data]
    return {**empty, 'items': items, 'next_cursor': items[-1]['id'] if page.has_more and items else None}
