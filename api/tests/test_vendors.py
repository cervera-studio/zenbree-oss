"""The vendor registry (``app.billing.vendors``) is the one place a vendor's price and tariffs live."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.billing.paid_call import paid_call, record_vendor_call
from app.billing.vendors import SERPER, VENDORS, Vendor, register
from app.core.errors import AppError, Err, Ok
from app.integrations.providers import MAILBOX_PROVIDERS
from app.people.adapters.composite_contact_enrichment import CONTACT_PROVIDERS

APP = Path(__file__).resolve().parents[1] / 'app'
FIX = 'register the vendor in api/app/billing/vendors.py'


def _literal_first_args(func: str) -> dict[str, str]:
    """The first argument of each ``func(...)`` call in ``app/`` written as a string, with its file."""
    found: dict[str, str] = {}
    for path in APP.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == func
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found[node.args[0].value] = str(path.relative_to(APP))
    return found


def test_every_paid_call_operation_has_a_tariff() -> None:
    """``paid_call`` raises ``KeyError`` on an operation with no tariff; this finds it before a call does."""
    operations = _literal_first_args('paid_call')
    assert operations, 'the scan found no paid_call with a literal operation: fix the scan'
    known = {f'{v.name}.{op}' for v in VENDORS.values() for op in v.tariffs}
    missing = {op: f for op, f in operations.items() if op not in known}
    assert not missing, f'paid_call names operations with no tariff: {missing}; add a Tariff to the vendor: {FIX}'


def test_every_web_search_provider_is_a_vendor() -> None:
    """``web.py`` looks the vendor up by ``res['provider']``, a value no type checks."""
    providers: dict[str, str] = {}
    for path in (APP / 'scraping').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == 'provider'
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        providers[value.value] = str(path.relative_to(APP))
    assert providers, 'the scan found no provider in app/scraping: fix the scan'
    missing = {v: f for v, f in providers.items() if v not in VENDORS}
    assert not missing, f'web search returns providers that are not vendors: {missing}; {FIX}'


def test_a_vendor_name_registers_once() -> None:
    with pytest.raises(ValueError, match='serper'):
        register(Vendor(name='serper', usd_per_unit=1.0))
    assert VENDORS['serper'] is SERPER


def _rows(vendor_db) -> list[tuple]:
    return [p for s, p in vendor_db.executed if 'vendor_calls' in s]


def test_a_recorded_call_costs_its_metered_units_at_the_vendor_price(vendor_db) -> None:
    record_vendor_call(SERPER, 'places', metered=2, units=2, success=False)  # Serper bills a page that failed
    assert _rows(vendor_db) == [(None, 'serper', 'serper.places', 0.002, 2, False)]


async def test_paid_call_prices_and_logs_by_the_vendor_meter(vendor_db, monkeypatch) -> None:
    logged: list[tuple[str, dict]] = []

    def log(event: str, **fields) -> None:
        logged.append((event, fields))

    monkeypatch.setattr('app.billing.paid_call.logger', SimpleNamespace(info=log, warning=log))

    async def treg_answer():
        return Ok(SimpleNamespace(status_code=200, headers={'X-Treg-Cost-Micro': '1234'}))

    async def no_match():
        return Err(AppError(code='CONTACTS.NO_MATCH'))

    await paid_call('treg.call', treg_answer)
    await paid_call('aiark.find_email', no_match)
    assert _rows(vendor_db) == [
        (None, 'treg', 'treg.call', 0.001234, 1, True),
        (None, 'aiark', 'aiark.find_email', 0.0, 1, False),
    ]
    assert [(event, fields.get('treg_cost_micro'), fields.get('aiark_credits')) for event, fields in logged] == [
        ('treg.call.end', 1234, None),
        ('aiark.find_email.end', None, 0),
    ]


def test_every_vendor_switch_has_an_admin_description() -> None:
    page = (Path(__file__).resolve().parents[2] / 'web/app/admin/settings/page.tsx').read_text()
    described = set(re.findall(r"'vendor_disabled_(\w+)'", page))
    missing = sorted((set(VENDORS) | set(MAILBOX_PROVIDERS) | set(CONTACT_PROVIDERS)) - described)
    assert not missing, f'add vendor_disabled_<vendor> to DESCRIBED in web/app/admin/settings/page.tsx for {missing}'
