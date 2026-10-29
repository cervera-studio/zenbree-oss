"""The Celery task of a CSV import (#344), declared here and registered by app/worker.py (as ``register_sweep``)."""


def register_csv_import(member_task):
    @member_task(name='run_csv_import', soft_time_limit=600, time_limit=660)
    def run_csv_import(import_id: str) -> None:
        from app.lists.csv_run import run

        run(import_id)

    return run_csv_import
