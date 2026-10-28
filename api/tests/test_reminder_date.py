from datetime import date

import pytest

from app.outreach.reminder_date import remind_on

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize(
    ('asked', 'expected'),
    [
        ('2027-01', date(2027, 1, 1)),  # a month in a coming year keeps its year
        ('2026-12', date(2026, 12, 1)),
        ('2025-12', date(2026, 12, 1)),  # a month gone by rolls to the next one to come
        ('2026-10', date(2027, 10, 1)),
        ('2027-12', date(2027, 10, 7)),  # past a year: the clamp
        ('2026-12-24', date(2026, 12, 24)),
        (None, date(2027, 1, 5)),  # no date: 90 days
    ],
)
def test_remind_on(asked, expected):
    assert remind_on(asked, TODAY) == expected
