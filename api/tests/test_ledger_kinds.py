"""Every (type, reference_type) pair the API writes to the credit ledger has a label on the web (#736)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESERVED = r"reference_type='(\w[\w.]*)'"
CREDITED = r"increment_credits\(%s, %s, %s, '\w+', '(\w+)'"
TAB = r"kind='(\w+)'"
# ponytail: a regex over the sources. A writer that passes a constant or variable (the tool name `enrich_phone`,
# the plan references of billing/routes.py) or a plpgsql literal (`reservation.slack`) is not seen: list it by hand
# below. Upgrade path: have every writer take its reference type from one enum this test imports.
HELD = {'enrich_phone'}  # reserved through a constant
OTHER = {'stripe_plan_period', 'stripe_plan_upgrade', 'reservation.slack', 'plan_expiry'}
UNLABELLED = {'admin'}  # an admin's own reason stays raw


def test_every_pair_the_api_writes_has_a_web_label():
    web = (ROOT / 'web/lib/billing/transaction-types.ts').read_text()
    held, other = set(HELD), set(OTHER)
    for path in (ROOT / 'api/app').rglob('*.py'):
        text = path.read_text()
        held |= set(re.findall(RESERVED, text))
        if 'Tab.open' in text:
            held |= set(re.findall(TAB, text))
        other |= set(re.findall(CREDITED, text))
    # a hold is a reservation, then a deduction once it settles
    missing = {f'{t}:{r}' for r in held - UNLABELLED for t in ('reservation', 'deduction') if f"'{t}:{r}'" not in web}
    missing |= {r for r in other - UNLABELLED if f":{r}'" not in web}
    assert not missing, f'add {sorted(missing)} to KINDS in web/lib/billing/transaction-types.ts and its messages'
