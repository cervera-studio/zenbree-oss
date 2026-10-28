"""The migration of the Instantly automation step (#1032) on the rows that exist before it, and the shape it gives the
hold of a run's people."""

import os
import re
import shutil
import uuid
from typing import get_args

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.automations.models import StepKind
from app.core.migrate import MIGRATIONS, migrate
from tests.automations_support import make_list, user
from tests.conftest import empty_database, member, migrated_database, project

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
MIGRATION = '20261019104117_automation_instantly.sql'
OLD_KINDS = ('find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot')


@pytest.fixture
def before(tmp_path):
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < MIGRATION:
            shutil.copy(path, tmp_path)
    with empty_database() as c:
        migrate(c, tmp_path)
        yield c, tmp_path


def _run(c) -> tuple[str, str, str]:
    uid = user(c)
    org = member(c, uid).org_id
    project_id = project(c, uid)
    list_id, _ = make_list(c, uid, 'person')
    definition = Jsonb({'list_id': list_id, 'steps': [{'type': 'find_emails'}]})
    automation = c.execute(
        'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, credit_cap, definition)'
        " VALUES (%s, %s, %s, 'A', 'manual', 5, %s) RETURNING id",
        (org, project_id, uid, definition),
    ).fetchone()['id']
    run = c.execute(
        'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
        " scheduled_for) VALUES (%s, %s, 1, %s, 5, 'manual', now()) RETURNING id",
        (org, automation, definition),
    ).fetchone()['id']
    return str(org), str(project_id), str(run)


def _check_kinds(c) -> set[str]:
    definition = c.execute(
        "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'automation_run_steps_kind_check'"
    ).fetchone()['d']
    return set(re.findall(r"'(\w+)'::text", definition))


def test_the_migration_keeps_a_step_row_of_every_old_kind(before):
    c, directory = before
    org, _, run = _run(c)
    for position, kind in enumerate(OLD_KINDS):
        c.execute(
            'INSERT INTO automation_run_steps (organization_id, run_id, position, kind) VALUES (%s, %s, %s, %s)',
            (org, run, position, kind),
        )

    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    assert _check_kinds(c) == set(get_args(StepKind))
    kinds = [r['kind'] for r in c.execute('SELECT kind FROM automation_run_steps ORDER BY position').fetchall()]
    assert kinds == list(OLD_KINDS)
    c.execute(
        "INSERT INTO automation_run_steps (organization_id, run_id, position, kind) VALUES (%s, %s, 9, 'add_to_instantly')",
        (org, run),
    )


def test_a_hold_either_holds_people_or_names_its_batch():
    with migrated_database() as (c, _):
        org, project_id, run = _run(c)

        def hold(row_ids, enrollment_id=None):
            c.execute(
                'INSERT INTO automation_instantly (run_id, organization_id, project_id, workspace_id, campaign_id,'
                ' campaign_status, row_ids, enrollment_id) VALUES (%s, %s, %s, %s, %s, 0, %s::uuid[], %s)',
                (run, org, project_id, 'ws-1', str(uuid.uuid4()), row_ids, enrollment_id),
            )

        with pytest.raises(psycopg.errors.CheckViolation, match='automation_instantly_held_or_made'):
            hold([])
        with pytest.raises(psycopg.errors.CheckViolation, match='automation_instantly_people'):
            hold([str(uuid.uuid4()) for _ in range(1001)])
        hold([str(uuid.uuid4())])
        with pytest.raises(psycopg.errors.CheckViolation, match='automation_instantly_held_or_made'):
            c.execute('UPDATE automation_instantly SET enrollment_id = gen_random_uuid() WHERE run_id = %s', (run,))
