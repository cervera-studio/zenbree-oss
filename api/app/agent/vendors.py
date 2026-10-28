"""Which vendors answered a tool call (#352): recorded by the loop on ``tool_calls[].vendors``, for the back office
only."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.billing.paid_call import vendor_trail


@contextmanager
def tracked() -> Iterator[list[str]]:
    """The vendors called inside the block, in order, each once (``record_vendor_call`` appends them)."""
    vendors: list[str] = []
    token = vendor_trail.set(vendors)
    try:
        yield vendors
    finally:
        vendor_trail.reset(token)


def user_messages(conn, session_id) -> list[dict]:
    """A chat's messages as their users read them: which vendors answered is the back office's."""
    messages = conn.execute(
        'SELECT id, session_id, role, content, tool_calls, sources, credits_used, status, terminal_code,'
        ' question, created_at FROM chat_messages WHERE session_id = %s ORDER BY created_at',
        (session_id,),
    ).fetchall()
    for m in messages:
        for call in m['tool_calls'] or []:
            if isinstance(call, dict):  # an entry that is no call is dropped later, by ToolCalls
                call.pop('vendors', None)
    return messages
