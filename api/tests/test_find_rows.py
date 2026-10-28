"""find_rows: the row ids of the people a chat message names, so «Cerca l'email di <nome>» enriches them (#560)."""

import pytest

from app.agent.tools.handlers import enrich_email
from app.agent.tools.handlers.find_rows import find_rows
from app.agent.tools.registry import ToolContext
from app.lists.rows import add_rows, create_list
from tests.conftest import act_in_own_org, project
from tests.test_agent import _chat, _job, _user, needs_db
from tests.test_agent import db as db

pytestmark = needs_db

PEOPLE = [
    {'first_name': 'Paolo', 'last_name': 'Marchi', 'company_name': 'Acme', 'title': 'CEO'},
    {'full_name': 'Dott.ssa Fabia Espòsito', 'company_name': 'Beta', 'phone': '+39 02 1234567'},
    {'first_name': 'Maria', 'last_name': 'Rossi', 'company_name': 'Gamma'},
    {'first_name': 'Maria Pia', 'last_name': 'Rossi', 'company_name': 'Delta'},
    {
        'first_name': 'Luca',
        'last_name': 'Bianchi',
        'company_name': 'Uno',
        'linkedin_url': 'https://linkedin.com/in/lb1',
    },
    {
        'first_name': 'Luca',
        'last_name': 'Bianchi',
        'company_name': 'Due',
        'linkedin_url': 'https://linkedin.com/in/lb2',
    },
]


@pytest.fixture
def chat(db):
    """A person list of one user's chat: (ctx, {display name: row id})."""
    user_id = _user(db)
    act_in_own_org(db, user_id)  # a tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find people')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    ids = add_rows(db, list_id, 'person', PEOPLE)
    names = ['Paolo Marchi', 'Fabia Esposito', 'Maria Rossi', 'Maria Pia Rossi', 'Luca Bianchi 1', 'Luca Bianchi 2']
    return ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id), dict(
        zip(names, ids, strict=True)
    )


def _found(res) -> dict[str, list[str]]:
    return {m['name']: [r['row_id'] for r in m['rows']] for m in res.data['matches']}


async def test_a_name_finds_that_row_only_and_enrich_searches_only_it(chat, monkeypatch):
    ctx, ids = chat
    res = await find_rows({'names': ['paolo marchi', 'Esposito Fabia']}, ctx)
    assert res.ok
    # case, accents, word order and a leading title do not matter
    assert _found(res) == {'paolo marchi': [ids['Paolo Marchi']], 'Esposito Fabia': [ids['Fabia Esposito']]}
    assert [m['matches'] for m in res.data['matches']] == [1, 1]
    paolo = res.data['matches'][0]['rows'][0]
    assert (paolo['name'], paolo['company_name'], paolo['title']) == ('Paolo Marchi', 'Acme', 'CEO')
    # whether a contact is there, never the contact itself: the result goes to the LLM vendor
    fabia = res.data['matches'][1]['rows'][0]
    assert (paolo['has_email'], paolo['has_phone'], fabia['has_phone']) == (False, False, True)
    assert 'phone' not in fabia and 'email' not in paolo

    searched: list[str] = []

    async def one(person, *_clients):
        searched.append(person['row_id'])
        return {'row_id': person['row_id'], 'cells': {'email': 'x@acme.it', 'email_status': 'verified'}}

    class Configured:
        is_configured = True

    for client in ('ApolloClient', 'TregClient', 'AiArkClient'):
        monkeypatch.setattr(enrich_email, client, Configured)
    monkeypatch.setattr(enrich_email, '_enrich_one', one)
    row_ids = [r['row_id'] for m in res.data['matches'] for r in m['rows']]
    enriched = await enrich_email.handle({'row_ids': row_ids}, ctx)
    assert enriched.ok and sorted(searched) == sorted([ids['Paolo Marchi'], ids['Fabia Esposito']])


async def test_a_name_not_in_the_list_matches_nothing(chat):
    ctx, _ = chat
    res = await find_rows({'names': ['Giorgio Verdi', 'Paolo Verdi', '  ', '·']}, ctx)
    assert res.ok and [(m['matches'], m['rows']) for m in res.data['matches']] == [(0, [])] * 4


async def test_a_name_on_several_rows_returns_them_all(chat):
    ctx, ids = chat
    res = await find_rows({'names': ['Luca Bianchi', 'Rossi']}, ctx)
    assert _found(res) == {
        'Luca Bianchi': [ids['Luca Bianchi 1'], ids['Luca Bianchi 2']],
        'Rossi': [ids['Maria Rossi'], ids['Maria Pia Rossi']],
    }
    assert [m['matches'] for m in res.data['matches']] == [2, 2]
    assert [r['company_name'] for r in res.data['matches'][0]['rows']] == ['Uno', 'Due']


async def test_a_full_name_beats_a_longer_one_that_contains_it(chat):
    ctx, ids = chat
    res = await find_rows({'names': ['Maria Rossi', 'Maria']}, ctx)
    assert _found(res) == {'Maria Rossi': [ids['Maria Rossi']], 'Maria': [ids['Maria Rossi'], ids['Maria Pia Rossi']]}


async def test_an_excluded_row_is_never_found(chat, db):
    ctx, ids = chat
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (ids['Paolo Marchi'],))
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'company_name', 'uno')",
        (project(db, ctx.user_id), ctx.user_id),
    )
    res = await find_rows({'names': ['Paolo Marchi', 'Luca Bianchi']}, ctx)
    # the excluded Luca Bianchi does not make the other one ambiguous
    assert _found(res) == {'Paolo Marchi': [], 'Luca Bianchi': [ids['Luca Bianchi 2']]}
    # the list still shows an excluded row: it is named, without a row id that nobody may enrich
    paolo, luca = res.data['matches']
    assert paolo['not_counted'] == [
        {'name': 'Paolo Marchi', 'title': 'CEO', 'company_name': 'Acme', 'reason': 'excluded'}
    ]
    assert luca['not_counted'] == [{'name': 'Luca Bianchi', 'company_name': 'Uno', 'reason': 'excluded'}]


async def test_a_masked_surname_that_fits_is_offered_to_confirm_never_counted(chat, db):
    ctx, _ = chat
    masked = {'first_name': 'Gino', 'last_name': 'Ma···i', 'full_name': 'Gino Ma···i', 'company_name': 'Rete Sei'}
    starred = {'first_name': 'Gino', 'last_name': 'Ve**i', 'company_name': 'Sette', 'linkedin_url': 'https://x.it/gv'}
    # _markdown_safe_mask('De Luca'): a surname of two words is masked as one
    compound = {'full_name': 'Gino De····a', 'company_name': 'Otto', 'linkedin_url': 'https://x.it/gd'}
    other = {'first_name': 'Paolo', 'last_name': 'Ro···i', 'company_name': 'Nove', 'linkedin_url': 'https://x.it/pr'}
    masked_id, starred_id, compound_id, _ = add_rows(db, ctx.list_id, 'person', [masked, starred, compound, other])
    names = ['Gino Marchi', 'Gino M.', 'gino ma', 'Gino De Luca', 'Gino Rossi', 'Ugo Marchi', 'Gino', 'i']
    res = await find_rows({'names': [*names, 'Maria De Rossi', 'Paolo Bianchi']}, ctx)
    by_name = {m['name']: m for m in res.data['matches']}
    fits = [{'row_id': masked_id, 'name': 'Gino Ma···i', 'company_name': 'Rete Sei', 'reason': 'masked'}]
    for name in ('Gino Marchi', 'Gino M.', 'gino ma'):
        assert (by_name[name]['matches'], by_name[name]['not_counted']) == (0, fits), name
    luca = [{'row_id': compound_id, 'name': 'Gino De····a', 'company_name': 'Otto', 'reason': 'masked'}]
    assert (by_name['Gino De Luca']['matches'], by_name['Gino De Luca']['not_counted']) == (0, luca)
    # surname does not fit, first name differs, a mask fragment, a row not masked, another masked surname
    for name in ('Gino Rossi', 'Ugo Marchi', 'i', 'Maria De Rossi', 'Paolo Bianchi'):
        assert (by_name[name]['matches'], by_name[name]['not_counted']) == (0, []), name
    # the first name alone names every Gino, as any first name does
    assert by_name['Gino']['matches'] == 3 and by_name['Gino']['not_counted'] == []
    assert sorted(r['row_id'] for r in by_name['Gino']['rows']) == sorted([masked_id, starred_id, compound_id])


async def test_another_lists_person_is_never_found(chat, db):
    ctx, _ = chat
    stranger = _user(db)
    their_session = _chat(db, stranger)
    theirs = create_list(db, user_id=stranger, session_id=their_session, name='Theirs', entity='person')
    add_rows(db, theirs, 'person', [{'first_name': 'Giorgio', 'last_name': 'Verdi', 'company_name': 'Zeta'}])
    my_other_session = _chat(db, ctx.user_id)
    mine_too = create_list(db, user_id=ctx.user_id, session_id=my_other_session, name='Other', entity='person')
    add_rows(db, mine_too, 'person', [{'first_name': 'Anna', 'last_name': 'Neri', 'company_name': 'Eta'}])
    res = await find_rows({'names': ['Giorgio Verdi', 'Anna Neri']}, ctx)
    assert [m['matches'] for m in res.data['matches']] == [0, 0]


async def test_no_list_yet_matches_nobody_without_failing_the_step():
    res = await find_rows({'names': ['Paolo Marchi']}, ToolContext(user_id='00000000-0000-0000-0000-000000000001'))
    assert res.ok and res.data == {
        'no_list': True,
        'matches': [{'name': 'Paolo Marchi', 'matches': 0, 'rows': [], 'not_counted': []}],
    }


async def test_names_that_are_not_a_list_name_nobody(chat):
    ctx, _ = chat
    for names in ('Paolo Marchi', {'a': 'Paolo'}, None):
        res = await find_rows({'names': names}, ctx)
        assert res.ok and res.data['matches'] == [], names
