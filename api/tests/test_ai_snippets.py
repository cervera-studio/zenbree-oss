import json

import pytest

from app.lists.ai_snippets import snippet_cell
from app.llm.base import LLMResponse

NEWS = {'url': 'https://acme.test/news', 'title': 'Acme raises'}
ROW = {
    'cells': {'name': 'Acme', 'ai_news': 'Acme raised 12 million in May', 'ai_funding': ''},
    'cell_meta': {'ai_news': {'status': 'filled', 'source': NEWS}, 'ai_funding': {'status': 'empty', 'source': None}},
}
INPUTS = {'ai_news': 'Recent news', 'ai_funding': 'Funding'}


class Model:
    def __init__(self, answer: dict | str | Exception) -> None:
        self.answer, self.asked = answer, []

    async def chat(self, *, system, messages, **kw):
        self.asked.append(json.loads(messages[0]['content']))
        if isinstance(self.answer, Exception):
            raise self.answer
        return LLMResponse(text=self.answer if isinstance(self.answer, str) else json.dumps(self.answer))


async def test_a_grounded_snippet_fills_with_the_source_of_the_cell_it_used():
    model = Model({'text': 'Congrats on the 12 million round, a 30 day trial fits.', 'used': ['ai_news']})

    cell = await snippet_cell(model, 'company', ROW, INPUTS, 'Link their news to our 30 day trial')

    assert cell == {
        'status': 'filled',
        'value': 'Congrats on the 12 million round, a 30 day trial fits.',
        'source': NEWS,
        'error': None,
    }
    assert model.asked[0]['research'] == {'ai_news': {'label': 'Recent news', 'value': 'Acme raised 12 million in May'}}


@pytest.mark.parametrize(
    ('answer', 'error'),
    [
        ({'text': 'Congrats on the round.', 'used': []}, 'ungrounded'),
        ({'text': 'Congrats on the round.', 'used': ['ai_funding', 'ai_other']}, 'ungrounded'),
        ({'text': 'Congrats on the 15 million round.', 'used': ['ai_news']}, 'ungrounded'),
        ({'text': None, 'used': []}, None),
    ],
    ids=['names-no-cell', 'names-no-filled-input', 'invented-number', 'no-answer'],
)
async def test_a_snippet_the_research_does_not_ground_stays_empty(answer, error):
    cell = await snippet_cell(Model(answer), 'company', ROW, INPUTS, 'Link their news to our offer')

    assert cell == {'status': 'empty', 'value': None, 'source': None, 'error': error}


async def test_a_row_without_research_asks_the_model_nothing():
    model = Model({'text': 'Congrats.', 'used': ['ai_news']})
    edited = {'cells': {'name': 'Acme', 'ai_news': 'typed by hand'}, 'cell_meta': {'ai_news': {'status': 'manual'}}}

    cell = await snippet_cell(model, 'company', edited, INPUTS, 'Link their news to our offer')

    assert (cell['status'], cell['error'], model.asked) == ('empty', 'no_research', [])


@pytest.mark.parametrize('answer', [RuntimeError('timeout'), 'not json'])
async def test_a_model_failure_is_a_failed_cell(answer):
    cell = await snippet_cell(Model(answer), 'company', ROW, INPUTS, 'Link their news to our offer')

    assert (cell['status'], cell['value'], cell['error']) == ('failed', None, 'snippet_failed')
