from app.agent.step_text import FAILED_SEARCH_RULE


def test_failed_search_rule_keeps_vendor_configuration_from_the_customer():
    assert 'Never name a data source, vendor' in FAILED_SEARCH_RULE and 'top up credits' in FAILED_SEARCH_RULE
    assert 'only when no tool result of this turn charged any' in FAILED_SEARCH_RULE


def test_failed_search_rule_leaves_the_retry_to_the_fixed_line():
    """Demo QA chat-2: the answer repeated the failure, the credits and the retry that the chat already shows."""
    assert (
        'it must not say any of that again' in FAILED_SEARCH_RULE
        and 'first sentence is that suggestion' in FAILED_SEARCH_RULE
    )


def test_failed_search_rule_bans_the_failure_only_when_every_search_failed():
    """The fixed line shows only when every search failed: a partial result and a low balance keep their own text."""
    banned, otherwise = FAILED_SEARCH_RULE.split('Otherwise, ', 1)
    assert 'When every search of a turn fails' in banned and 'no sentence about the failure' in banned
    assert otherwise.startswith('after a partial result, say what was found') and 'top up credits' in otherwise
