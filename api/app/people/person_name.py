"""The first and last name of a person row, whatever columns its source filled."""

import re

from app.people.apollo_decode import MASK_CHAR

# Leading titles of a full name ("Dott.ssa Eva De Luca"). The ones that are also plain words or
# given names (Ing, Arch, Sig) count only with their dot.
_NAME_TITLES = re.compile(
    r'^(?:(?:(?:dott|prof)(?:\.?ssa)?|dr|mr|mrs|ms)\.?\s+|(?:ing|avv|arch|geom|rag|sig)\.(?:ra|na)?\.?\s+)+',
    re.IGNORECASE,
)


def first_and_last(cells) -> tuple[str, str]:
    """``(first, last)`` of a person row: a campaign target, or the hints of a contact search. A CSV with one
    name column, ``save_to_list`` and the dev seed store only ``full_name``, so a missing part is taken from
    it, without its leading titles.

    ponytail: the first word is the first name, the rest the last; "Maria Pia Rossi" gives "Maria".
    """
    first, last = (str(cells.get(k) or '').strip() for k in ('first_name', 'last_name'))
    if not (first and last):
        head, _, tail = _NAME_TITLES.sub('', str(cells.get('full_name') or '').strip()).partition(' ')
        first, last = first or head, last or tail.strip()
    return first[:200], last[:200]


def masked_parts(surname: str) -> tuple[str, str] | None:
    """The visible start and end of a masked surname («Ma···i» is ``('Ma', 'i')``), or None when nothing is masked.
    A row from before the mask swap, or a CSV, can still carry the provider's ``*``."""
    if MASK_CHAR not in surname and '*' not in surname:
        return None
    parts = re.split(rf'[*{MASK_CHAR}]+', surname)
    return parts[0], parts[-1]
