# Agent speed regression gate

`speed / benchmark` runs on every PR and main push. From `api/`, run:

```sh
TEST_DATABASE_URL=postgresql://…/disposable_test_db uv run pytest -q -s bench/test_speed.py
```

Locally, `harness/bench.sh` does this on the shared test database and takes the heavy-job lock. Use a disposable PostgreSQL database, never production. The existing agent-test
fixture creates and drops a unique schema per case. Missing database configuration
fails the benchmark. No vendor credentials are needed; HTTP calls are blocked.

The fixed briefs cover 10 local companies, 50 SaaS companies and 100 people, each
repeated three times. Every run must meet both checked-in budgets:

| Metric | Budget | Measured boundary |
| --- | --- | --- |
| First row | 1 second | `run_job` entry → first `list_updated` with persisted new rows |
| Finished list | 3 seconds | `run_job` entry → `done`, after scoring, credit settlement and job/message persistence |

These are initial internal regression budgets, chosen to leave headroom for a
shared CI runner while catching second-scale stalls. Every sample must pass;
there is no average hiding a slow run. JSON lines in CI logs contain both timings,
brief, row count and repetition. A failed, partial, empty, unscored or incorrectly
charged job also fails, even if fast. A ten-minute CI timeout bounds a hung run.

The real agent loop, SQL writes, deduplication, scoring batches and credit ledger
run against PostgreSQL. Only the model and search handler are scripted fixtures.
Setup/imports and schema creation are outside the measured interval. This is a
repeatable offline workload, not a claim about live provider latency or relevance:
LLM/vendor networking, Celery queue delay, Redis/SSE delivery and browser rendering
are excluded. Product latency targets need separate live measurements. Issue #29
remains independent: this gate uses existing events and requires no schema change.

The speed workflow is separate from deployment CI; it does not itself block main
image deployment. Require its PR status alongside the existing checks before merge.
