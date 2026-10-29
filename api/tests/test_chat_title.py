"""A chat or list name is the first line of a message, cut at a word, never inside a number."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from app.agent.turn_start import chat_title

BRIEF = 'Trova i responsabili acquisti di aziende manifatturiere in Lombardia tra 50 e 200 dipendenti'
# a word that ends at the 79th character, then a space: the cut keeps it
EDGE = 'a' * 40 + ' ' + 'b' * 38 + ' cc'


@pytest.mark.parametrize(
    ('message', 'title'),
    [
        ('agencies in Milan', 'agencies in Milan'),
        (BRIEF, 'Trova i responsabili acquisti di aziende manifatturiere in Lombardia tra 50 e…'),
        ('\n  Build my first list \nPeople search filters:\n- titles: CTO', 'Build my first list'),
        ('CTO   of\tstartups', 'CTO of startups'),
        ('a' * 100, 'a' * 79 + '…'),
        ('Cerca ' + 'x' * 100, 'Cerca ' + 'x' * 73 + '…'),
        ('x' * 80, 'x' * 80),
        (' \n ', ''),
        (EDGE, 'a' * 40 + ' ' + 'b' * 38 + '…'),
        ('x' * 78 + '😀😀', 'x' * 78 + '😀😀'),
        ('x' * 78 + '😀😀😀', 'x' * 78 + '😀…'),
        (' \r first line\rsecond', 'first line'),
        (' 😀1béb1a😀b 😀aaba  b \r aé\t\r ', '😀1béb1a😀b 😀aaba b'),  # fast-check found it in the web cut
    ],
)
def test_a_title_is_the_first_line_cut_at_a_word(message, title):
    assert chat_title(message) == title
    assert len(chat_title(message)) <= 80


WORD = st.text(alphabet='ab1é😀', min_size=1, max_size=90)
GAP = st.sampled_from([' ', '  ', '\t', ' ', '　'])
BREAK = st.sampled_from(['\n', '\r', '\r\n', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', ' ', ' '])


@st.composite
def messages(draw) -> tuple[str, str]:
    """A message and its first non-empty line with whitespace collapsed, known from how it was built."""
    lines = draw(st.lists(st.lists(WORD, max_size=8), max_size=4))
    text = draw(BREAK).join(draw(GAP) + ''.join(w + draw(GAP) for w in words) for words in lines)
    first = next((words for words in lines if words), [])
    return text, ' '.join(first)


@given(messages())
@example((EDGE, EDGE))
def test_a_title_is_the_line_or_its_longest_cut_that_fits(case):
    text, line = case
    title = chat_title(text)
    assert len(title) <= 80
    if len(line) <= 80:
        assert title == line
        return
    head = title.removesuffix('…')
    assert title == head + '…'
    assert line.startswith(head)
    if len(head) != 79:  # a word cut: the next character is a space and no later word still fits
        assert line[len(head)] == ' '
        assert len(head) >= 40
        assert ' ' not in line[len(head) + 1 : 80]


@given(st.text())
def test_a_title_is_already_a_title(text):
    title = chat_title(text)
    assert len(title) <= 80
    assert chat_title(title) == title
