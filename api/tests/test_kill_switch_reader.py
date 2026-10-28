"""``vendor_disabled_now`` is the one uncached reader of ``vendor_disabled:<name>``; every caller shares its rule."""

import contextlib

import pytest

from app.admin.settings import vendor_disabled_now


@pytest.fixture
def stored(monkeypatch):
    value: dict = {}

    class Conn:
        def execute(self, sql, params=()):
            self.key = params[0]
            return self

        def fetchone(self):
            return {'value': value['v']} if 'v' in value else None

    monkeypatch.setattr('app.admin.settings.get_conn', lambda: contextlib.nullcontext(Conn()))
    return value


@pytest.mark.parametrize(
    ('stored_value', 'blocked'),
    [(True, True), ('true', True), (False, False), ('false', False), ('0', False), (None, False)],
)
def test_which_stored_values_block(stored, stored_value, blocked):
    stored['v'] = stored_value
    assert vendor_disabled_now('apollo') is blocked


def test_no_row_blocks_nothing(stored):
    assert vendor_disabled_now('apollo') is False
