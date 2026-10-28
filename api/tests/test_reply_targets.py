"""Which organization an email reply belongs to (#414): the pure ordering, no database."""

from datetime import UTC, datetime, timedelta

from app.outreach.reply_targets import scope_to_organization

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def at(days):
    return None if days is None else NOW + timedelta(days=days)


def row(id_, org, *, quoted=None, sent=None, age=0):
    return {
        'id': id_,
        'organization_id': org,
        'by_reference': quoted is not None,
        'quoted_at': at(quoted),
        'last_sent_at': at(sent if sent is not None else quoted),
        'created_at': NOW + timedelta(minutes=age),
    }


def picked(rows):
    return [r['id'] for r in scope_to_organization(rows)]


def test_a_quote_beats_a_newer_unquoted_send():
    rows = [row('a', 'A', sent=9), row('b', 'B', quoted=1)]
    assert picked(rows) == ['b']


def test_the_newest_of_two_quotes_wins():
    rows = [row('a', 'A', quoted=1), row('b', 'B', quoted=2)]
    assert picked(rows) == ['b']


def test_with_no_quote_the_newest_send_wins():
    rows = [row('a', 'A', sent=1), row('b', 'B', sent=3), row('c', 'C', sent=2)]
    assert picked(rows) == ['b']


def test_with_no_sends_the_newest_target_wins():
    rows = [row('a', 'A', age=0), row('b', 'B', age=2), row('c', 'C', age=1)]
    assert picked(rows) == ['b']


def test_a_target_that_was_sent_to_beats_a_newer_one_never_sent_to():
    rows = [row('a', 'A', sent=1, age=0), row('b', 'B', age=5)]
    assert picked(rows) == ['a']


def test_the_chosen_organizations_targets_are_all_kept_quoted_first_then_newest_send():
    rows = [
        row('x', 'A', sent=1),
        row('y', 'A', quoted=2),
        row('z', 'A', sent=3),
        row('other', 'B', sent=9),
    ]
    assert picked(rows) == ['y', 'z', 'x']


def test_no_rows_pick_nothing():
    assert scope_to_organization([]) == []
