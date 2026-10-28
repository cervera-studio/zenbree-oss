import asyncio

from app.agent.tools.handlers.save_to_list import _save_to_list
from app.agent.tools.registry import ToolContext

CTX = ToolContext(user_id='u1')


def run(args):
    return asyncio.run(_save_to_list(args, CTX))


def test_keeps_cited_rows_and_derives_domain():
    r = run(
        {
            'entity': 'company',
            'items': [
                {'name': 'Acme Srl', 'website': 'https://www.acme.it/chi-siamo', 'source_url': 'https://sortlist.it/x'},
                {'name': 'No Source Spa', 'website': 'https://nosource.it'},  # uncited: dropped
                {'website': 'https://noname.it', 'source_url': 'https://sortlist.it/x'},  # unnamed: dropped
            ],
        }
    )
    assert r.ok and r.entity == 'company' and r.data == {'saved': 1, 'skipped': 2}
    assert r.rows == [
        {
            'name': 'Acme Srl',
            'website': 'https://www.acme.it/chi-siamo',
            'domain': 'acme.it',
            'source': 'web',
            'source_url': 'https://sortlist.it/x',
        }
    ]


def test_rejects_bad_entity_and_empty():
    assert not run({'entity': 'lead', 'items': []}).ok
    assert not run({'entity': 'person', 'items': [{'title': 'CEO', 'source_url': 'https://x.it'}]}).ok


def test_is_free():
    from app.agent.tools.registry import get

    assert get('save_to_list').price is None
