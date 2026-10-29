"""What an API test produced, written where the web tests read it (#661): web/lib/api/fixtures/<name>.json.

``harness/web_fixtures.py`` runs the tests marked ``web_fixture`` with ``WEB_FIXTURES_OUT`` set and compares the
files with the committed ones; without the variable ``web_fixture`` writes nothing. A fixture is the JSON the API
sends, with only the values that change from run to run replaced by fixed ones."""

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.lists.models import ListRow

CREATED_AT = '2026-01-01T00:00:00Z'


def web_fixture(name: str, value: Any) -> None:
    out = os.environ.get('WEB_FIXTURES_OUT')
    if not out:
        return
    path = Path(out) / f'{name}.json'
    # Two tests that write one name would leave whichever ran last: the check could not tell which one is right.
    if path.exists():
        raise FileExistsError(f'web fixture {name} written twice in one run')
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def wire_row(row: dict, row_id: str) -> dict:
    """``row`` as the rows and phone routes send it (both use ``response_model_exclude_unset``), with its id and
    timestamps fixed. ``row_id`` replaces the database id: the web tests name a row by it."""
    wire = ListRow.model_validate(row).model_dump(mode='json', exclude_unset=True)
    return {**wire, 'id': row_id, 'created_at': CREATED_AT}


def web_row(name: str, row: dict) -> None:
    """Writes one row fixture, named and identified by ``name``."""
    web_fixture(name, wire_row(row, name))


def web_frames(name: str, frames: list[list], list_id: str, row_ids: Sequence[str] = ()) -> None:
    """Writes ``[event, data]`` chat frames; a ``result_preview`` names the list by its id and the rows (``list.undelivered``
    keys) by theirs, here fixed: the list as ``list``, ``row_ids[i]`` as ``row-<i+1>``."""

    def fixed_preview(preview: str) -> str:
        for i, row_id in enumerate(row_ids):
            preview = preview.replace(row_id, f'row-{i + 1}')
        return preview.replace(list_id, 'list')

    fixed = [
        [event, {**data, 'result_preview': fixed_preview(data['result_preview'])}]
        if 'result_preview' in data
        else [event, data]
        for event, data in frames
    ]
    web_fixture(name, fixed)
