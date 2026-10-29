# API — agent notes

Read the root [AGENTS.md](../AGENTS.md) first. This file covers `api/` only. For "how to add X", see [docs/recipes.md](../docs/recipes.md).

## Layout and layering

- `app/core/` and `app/llm/` sit below the domains (`agent`, `lists`, `people`, `billing`, `outreach`, and so on). Today `core` imports only `core`, and `llm` imports only `llm` and `core`. Keep it so: a domain may import them, they never import a domain. **Not enforced:** no import linter checks this.
- Domains may import each other, but keep the arrows one-way. Put shared logic in `core` or in a new module, not in a domain that others reach into.
- `app/people/` and `app/scraping/` use ports and adapters. `people/ports/` holds the protocol, `people/adapters/` the vendor adapters, and `composite_contact_enrichment.py` chains them. `scraping/adapters/` holds the vendors and `scraping/use_cases/` the callers. `app/billing/tab.py` owns every credit hold (#660): a holder opens, records and closes a `Tab`. Only `tab.py` calls `credits.reserve` and `credits.grow`, and only `credits.py` calls the SQL functions of a hold (`tests/test_only_tab_reserves.py`).
- SQL is plain, with `psycopg` and no ORM. `with get_conn() as conn:` (`app/core/db.py`) commits on success and returns dicts. Schema changes are migrations in `db/` (see [docs/migrations.md](../docs/migrations.md)).

## Tests

- Tests are flat in `tests/`, named `test_<area>.py`. There are no subfolders.
- A file named `test_*_sql.py` needs a real Postgres. It skips without `TEST_DATABASE_URL`. Run `eval "$(../harness/testdb.sh)"` to start the shared database and set the variable, and run `uv run pytest -rs` to see what skipped and why.
- Run one test: `uv run pytest tests/test_agent.py::test_dedupe_key`. Run a file: `uv run pytest tests/test_find_rows.py`.
- `tests/conftest.py` redirects `paid_call`'s database writes to a fake, so no test pays a vendor.

## Checks

- `uv run ruff format .`, `uv run ruff check --fix .` and `uv run mypy` (only `app/`). Ruff uses single quotes and line length 120.
- `./harness/init.sh api --fast` runs them together with the size and migration-order checks.
- Size: `harness/size_check.py` keeps each file in `app/` within 400 lines. Files already over it are in `harness/size-baseline.json` and may only shrink. After you shrink one, run `python3 harness/size_check.py api --update` from the repo root to lower its entry. Put new logic in its own module and call it from the existing one.
