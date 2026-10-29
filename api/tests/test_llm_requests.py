from types import SimpleNamespace

import pytest

from app.llm.anthropic import AnthropicProvider
from app.outreach.inbox_reply import _DRAFT_SYSTEM
from app.outreach.replies import _AWAY_SYSTEM, _label_one
from app.outreach.replies import _SYSTEM as _LABEL_SYSTEM


@pytest.mark.parametrize('prompt', [_LABEL_SYSTEM, _DRAFT_SYSTEM, _AWAY_SYSTEM])
def test_json_mode_prompts_name_json_as_deepseek_requires(prompt):
    # api-docs.deepseek.com/guides/json_mode: JSON mode needs the word "json" in the prompt.
    assert 'json' in prompt.lower()


@pytest.mark.parametrize(
    ('reply', 'labelled'),
    [
        ('{"label": "interested"}', ('interested', None)),
        ('{"label": "not_now", "contact_again": "2027-03"}', ('not_now', '2027-03')),
    ],
)
async def test_a_label_answer_without_contact_again_keeps_its_label(monkeypatch, reply, labelled):
    # The live providers do not enforce the schema, so LLMProvider.chat checks the reply against it locally.
    async def create(**params):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0)
        content = [SimpleNamespace(type='text', text=reply, citations=None)]
        return SimpleNamespace(content=content, usage=usage, model=params['model'], stop_reason='end_turn')

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: provider)
    row = {'owner_id': None, 'subject': 'Re: Zenbree', 'body': 'Sounds good, tell me more.'}
    assert await _label_one(row) == labelled


async def test_claude_requests_cache_tools_and_system_with_one_breakpoint():
    sent = {}

    async def create(**params):
        sent.update(params)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0)
        return SimpleNamespace(content=[], usage=usage, model=params['model'], stop_reason='end_turn')

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    await provider.chat(system='You label replies.', messages=[{'role': 'user', 'content': 'hi'}])

    assert sent['system'] == [{'type': 'text', 'text': 'You label replies.', 'cache_control': {'type': 'ephemeral'}}]
