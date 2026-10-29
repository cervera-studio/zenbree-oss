import asyncio

from app.agent.tools.handlers.linkedin_search import NOT_CONNECTED, handle
from app.agent.tools.registry import ToolContext


def test_a_project_without_linkedin_gets_a_code_the_customer_is_not_told_to_retry():
    result = asyncio.run(handle({'keywords': 'x'}, ToolContext(user_id='u', session_id='s', project_id=None)))
    assert (result.ok, result.error, result.error_code) == (False, NOT_CONNECTED, 'not_connected')
