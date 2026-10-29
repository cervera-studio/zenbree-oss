"""Validated customer details and the documented Stripe/A-Cube customer mapping."""

import asyncio
import re
from contextlib import contextmanager
from typing import Literal, Self, get_args

import stripe
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator
from stripe.params import CustomerCreateTaxIdParams

from app.auth.middleware import CurrentUser, require_org_admin
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error

router = APIRouter(prefix='/v1/billing', tags=['billing'])
logger = structlog.get_logger()
COUNTRY_CODES = """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE
JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO
MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM
TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"""
COUNTRIES = COUNTRY_CODES.split()
EU_CODES = 'AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK'
EU = set(EU_CODES.split())
IT_PROVINCES = 'AG AL AN AO AP AQ AR AT AV BA BG BI BL BN BO BR BS BT BZ CA CB CE CH CL CN CO CR CS CT CZ EN FC FE FG FI FM FR GE GO GR IM IS KR LC LE LI LO LT LU MB MC ME MI MN MO MS MT NA NO NU OR PA PC PD PE PG PI PN PO PR PT PU PV PZ RA RC RE RG RI RM RN RO SA SI SO SP SR SS SU SV TA TE TN TO TP TR TS TV UD VA VB VC VE VI VR VT VV'
US_STATES = 'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'
CA_PROVINCES = 'AB BC MB NB NL NS NT NU ON PE QC SK YT'
SDK_TAX_TYPES = get_args(get_args(CustomerCreateTaxIdParams.__annotations__['type'])[0])


def tax_types(country: str) -> list[str]:
    types = [t for t in SDK_TAX_TYPES if t.startswith(country.lower() + '_') and t != 'it_cf']
    if country in EU or country == 'GB':
        types.insert(0, 'eu_vat')
    return types


class Address(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    country: str = Field(min_length=2, max_length=2)
    state: str = Field(default='', max_length=100)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(default='', max_length=20)
    line1: str = Field(min_length=1, max_length=200)
    line2: str = Field(default='', max_length=200)

    @field_validator('country')
    @classmethod
    def country_code(cls, value: str) -> str:
        value = value.upper()
        if value not in COUNTRIES:
            raise ValueError('Invalid country')
        return value

    @model_validator(mode='after')
    def italian_address(self) -> Self:
        if self.country == 'IT':
            self.state = self.state.upper()
            if self.state not in IT_PROVINCES.split() or not re.fullmatch(r'\d{5}', self.postal_code):
                raise ValueError('Italian addresses require a province code and five-digit postal code')
        if self.country == 'US':
            self.state = self.state.upper()
            if self.state not in US_STATES.split() or not re.fullmatch(r'\d{5}(?:-\d{4})?', self.postal_code):
                raise ValueError('US addresses require state and postal code')
        if self.country == 'CA':
            self.state = self.state.upper()
            self.postal_code = self.postal_code.upper()
            if self.state not in CA_PROVINCES.split() or not re.fullmatch(r'[A-Z]\d[A-Z] ?\d[A-Z]\d', self.postal_code):
                raise ValueError('Canadian addresses require province and postal code')
        return self


class BillingProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    customer_type: Literal['individual', 'business']
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    address: Address
    fiscal_code: str = Field(default='', max_length=64)
    tax_id_type: str = Field(default='', max_length=30)
    tax_id_value: str = Field(default='', max_length=64)
    recipient_code: str = Field(default='', max_length=7)
    pec: EmailStr | Literal[''] = Field(default='', max_length=254)

    @model_validator(mode='after')
    def fiscal_fields(self) -> Self:
        country = self.address.country
        self.fiscal_code = re.sub(r'\s+', '', self.fiscal_code).upper()
        if self.tax_id_type == 'eu_vat':
            self.tax_id_value = re.sub(r'[\s.-]+', '', self.tax_id_value).upper()
        self.recipient_code = self.recipient_code.upper()
        if (
            country == 'IT'
            and self.fiscal_code
            and not re.fullmatch(
                r'(?:[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]|\d{11})',
                self.fiscal_code,
            )
        ):
            raise ValueError('Invalid Italian fiscal code format')
        if self.recipient_code and (country != 'IT' or not re.fullmatch(r'[A-Z0-9]{7}', self.recipient_code)):
            raise ValueError('Recipient code must contain seven letters or digits for Italian customers')
        if self.customer_type == 'individual':
            if not self.fiscal_code:
                raise ValueError('Fiscal identifier is required for individuals')
            self.tax_id_type = self.tax_id_value = ''
        else:
            if self.tax_id_type not in tax_types(country) or not self.tax_id_value:
                raise ValueError('A supported business tax identifier is required')
            if self.tax_id_type == 'eu_vat':
                prefix = 'EL' if country == 'GR' else 'XI' if country == 'GB' else country
                if country == 'IT' and re.fullmatch(r'\d{11}', self.tax_id_value):
                    self.tax_id_value = 'IT' + self.tax_id_value
                if not re.fullmatch(prefix + r'[A-Z0-9]{2,12}', self.tax_id_value):
                    raise ValueError('VAT number must match the billing country')
                if country == 'IT' and not re.fullmatch(r'IT\d{11}', self.tax_id_value):
                    raise ValueError('Italian VAT numbers require eleven digits')
        return self


class Country(BaseModel):
    code: str
    tax_id_types: list[str]


ProfileSyncStatus = Literal['local', 'pending', 'synced']


class ProfileState(BaseModel):
    profile: BillingProfile | None
    sync_status: ProfileSyncStatus
    countries: list[Country]


class SavedBillingProfile(BaseModel):
    profile: BillingProfile
    sync_status: ProfileSyncStatus


@contextmanager
def locked(org_id: str):
    # The same lock covers profile save, tax-ID replacement and Checkout creation for this org.
    with get_conn() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 4201))', (org_id,))
        yield conn


def required(conn, org_id: str) -> BillingProfile:
    row = conn.execute('SELECT profile FROM billing_profiles WHERE organization_id = %s', (org_id,)).fetchone()
    if row is None:
        raise api_error(409, 'BILLING.PROFILE_REQUIRED', 'Save complete billing details before paying')
    return BillingProfile.model_validate(row['profile'])


def _sync_customer(customer_id: str, data: BillingProfile) -> None:
    if not settings.stripe_secret_key:
        raise stripe.AuthenticationError('Stripe unavailable')
    metadata = {
        'fiscal_code': data.fiscal_code if data.customer_type == 'individual' else '',
        'codice_destinatario': data.recipient_code or ('0000000' if data.address.country == 'IT' else 'XXXXXXX'),
    }
    stripe.Customer.modify(
        customer_id,
        api_key=settings.stripe_secret_key,
        name=data.name,
        email=str(data.email),
        address={
            'country': data.address.country,
            'state': data.address.state,
            'city': data.address.city,
            'postal_code': data.address.postal_code,
            'line1': data.address.line1,
            'line2': data.address.line2,
        },
        metadata=metadata,
    )
    desired = (data.tax_id_type, data.tax_id_value) if data.customer_type == 'business' else None
    existing = list(
        stripe.Customer.list_tax_ids(customer_id, api_key=settings.stripe_secret_key, limit=100).auto_paging_iter()
    )
    kept = False
    for tax_id in existing:
        if desired == (tax_id.type, tax_id.value) and not kept:
            kept = True
        else:
            stripe.Customer.delete_tax_id(customer_id, tax_id.id, api_key=settings.stripe_secret_key)
    if desired and not kept:
        stripe.Customer.create_tax_id(
            customer_id, type=desired[0], value=desired[1], api_key=settings.stripe_secret_key
        )


def sync_customer(conn, org_id: str, customer_id: str, data: BillingProfile) -> bool:
    try:
        _sync_customer(customer_id, data)
    except stripe.StripeError as exc:
        logger.warning('billing.profile.sync_failed', error_type=type(exc).__name__)
        conn.execute("UPDATE billing_profiles SET sync_status = 'pending' WHERE organization_id = %s", (org_id,))
        # A partial provider update must not leave recurring top-ups using mismatched fiscal details.
        conn.execute(
            'UPDATE auto_topups SET enabled=false, awaiting_card=false, updated_at=now() WHERE organization_id=%s',
            (org_id,),
        )
        return False
    conn.execute("UPDATE billing_profiles SET sync_status = 'synced' WHERE organization_id = %s", (org_id,))
    return True


def sync_error() -> HTTPException:
    return api_error(
        502,
        'BILLING.PROFILE_SYNC_FAILED',
        'Billing details saved locally; payment provider update failed. Retry saving and re-enable automatic top-ups.',
    )


@router.get('/profile', response_model=ProfileState)
def get_profile(user: CurrentUser = Depends(require_org_admin)) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT profile, sync_status FROM billing_profiles WHERE organization_id = %s', (user.org_id,)
        ).fetchone()
    return {
        'profile': row['profile'] if row else None,
        'sync_status': row['sync_status'] if row else 'local',
        'countries': [{'code': c, 'tax_id_types': tax_types(c)} for c in COUNTRIES],
    }


@router.put('/profile', response_model=SavedBillingProfile)
async def put_profile(request: Request, user: CurrentUser = Depends(require_org_admin)) -> dict:
    try:
        data = BillingProfile.model_validate(await request.json())
    except ValidationError, ValueError:
        # Pydantic/FastAPI's default 422 body echoes input, including fiscal identifiers and addresses.
        raise api_error(422, 'BILLING.PROFILE_INVALID', 'Check the billing details and fiscal identifiers') from None
    return await asyncio.to_thread(save_profile, user.org_id, data)


def save_profile(org_id: str, data: BillingProfile) -> dict:
    failed = False
    with locked(org_id) as conn:
        conn.execute(
            """INSERT INTO billing_profiles (organization_id, profile, sync_status) VALUES (%s,%s,'pending')
                        ON CONFLICT (organization_id) DO UPDATE SET profile = EXCLUDED.profile,
                        sync_status = 'pending', updated_at = now()""",
            (org_id, Jsonb(data.model_dump(mode='json'))),
        )
        customer = conn.execute(
            'SELECT stripe_customer_id FROM billing_customers WHERE organization_id = %s', (org_id,)
        ).fetchone()
        if customer:
            failed = not sync_customer(conn, org_id, customer['stripe_customer_id'], data)
        else:
            conn.execute("UPDATE billing_profiles SET sync_status='local' WHERE organization_id=%s", (org_id,))
    if failed:
        raise sync_error()
    return {'profile': data.model_dump(mode='json'), 'sync_status': 'synced' if customer else 'local'}
