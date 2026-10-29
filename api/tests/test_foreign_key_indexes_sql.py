"""Every foreign key has an index that leads with its first column (#688, #692).

Without one, deleting a parent row, directly or by an ON DELETE CASCADE / SET NULL chain from a user, organization or
project delete, scans the whole child table once per deleted parent row.
"""

import pytest

from tests.test_agent import db as db
from tests.test_agent import needs_db

pytestmark = needs_db

# The key's ON DELETE lookup filters `first = $1 AND ...`, which a partial index serves only when its predicate is
# `first IS NOT NULL`.
UNINDEXED_FOREIGN_KEYS = """
SELECT c.conrelid::regclass::text || '.' ||
       (SELECT string_agg(attname, ',' ORDER BY k.n) FROM unnest(c.conkey) WITH ORDINALITY k(attnum, n)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) AS key,
       format('CREATE INDEX %I ON %s(%I);', lower('idx_' || t.relname || '_' || first.attname), c.conrelid::regclass,
              first.attname) AS fix
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_attribute first ON first.attrelid = c.conrelid AND first.attnum = c.conkey[1]
WHERE c.contype = 'f' AND NOT EXISTS (
  SELECT 1 FROM pg_index i
  WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]
    AND (i.indpred IS NULL OR pg_get_expr(i.indpred, i.indrelid) = format('(%I IS NOT NULL)', first.attname)))
ORDER BY 1
"""


def test_every_foreign_key_has_an_index(db):
    offenders = db.execute(UNINDEXED_FOREIGN_KEYS).fetchall()
    keys = '\n'.join(f'  {r["key"]}' for r in offenders)
    # Two keys that start with the same column share one index.
    fixes = '\n'.join(dict.fromkeys(r['fix'] for r in offenders))
    assert not offenders, (
        f'{len(offenders)} foreign keys have no index that leads with their first column:\n{keys}\n'
        'Add these lines to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql:\n'
        f'{fixes}'
    )


@pytest.mark.parametrize(
    ('foreign_key', 'index', 'offenders'),
    [
        # The lookup filters on parent_id too: an index on owner_id alone scans every row of the tenant.
        (
            'FOREIGN KEY (parent_id, owner_id) REFERENCES zp (id, owner_id)',
            '(owner_id)',
            [{'key': 'zc.parent_id,owner_id', 'fix': 'CREATE INDEX idx_zc_parent_id ON zc(parent_id);'}],
        ),
        ('FOREIGN KEY (parent_id) REFERENCES zp', '(parent_id) WHERE parent_id IS NOT NULL', []),
        (
            'FOREIGN KEY (parent_id) REFERENCES zp',
            '(parent_id) WHERE owner_id IS NOT NULL',
            [{'key': 'zc.parent_id', 'fix': 'CREATE INDEX idx_zc_parent_id ON zc(parent_id);'}],
        ),
        ('FOREIGN KEY (parent_id) REFERENCES zp', '(parent_id, lower(email))', []),
    ],
)
def test_an_index_counts_when_it_serves_the_delete_lookup(db, foreign_key, index, offenders):
    db.execute(f"""
        CREATE TABLE zp (id int PRIMARY KEY, owner_id int, UNIQUE (id, owner_id));
        CREATE TABLE zc (parent_id int, owner_id int, email text, {foreign_key} ON DELETE CASCADE);
        CREATE INDEX ON zc {index};
    """)
    assert db.execute(UNINDEXED_FOREIGN_KEYS).fetchall() == offenders
