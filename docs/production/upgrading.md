# Upgrading from 0.1.0 to 0.2.0

You can upgrade without downtime. Jobs already in the queue keep running, recurring schedules keep firing, and 0.1.0 and 0.2.0 workers can share the database while you roll.

## What changes in the database

0.2.0 adds one migration, `0006_sql_api`. It only adds things and fixes data, so it is safe to apply while 0.1.0 workers are running:

- Two functions, `soniq_enqueue()` and `soniq_get_job()`, so code in other languages can enqueue jobs and read their results. See [Storage format](../reference/storage-format.md). 0.1.0 never calls them.
- The `soniq_jobs.max_attempts` column default goes from 3 to 4. Python always sets the value itself, so this only matters for raw `INSERT`s that leave the column out. 4 matches what Python writes by default (`retries=3`, plus the first attempt).
- 0.1.0 stored some dead-letter `tags` as a JSON string holding an object. The migration rewrites those rows as objects. 0.2.0 reads both shapes, because a 0.1.0 worker that is still running can write more.

The row format itself didn't change. Every row 0.2.0 writes is a valid 0.1.0 row, and the other way round.

## Steps

1. **Apply the migration once**, from one place, before or during the deploy:

    ```bash
    soniq setup myapp.jobs:soniq
    ```

    It applies only `0006_sql_api`. If Alembic owns your schema, export just the new migration instead:

    ```bash
    soniq migrations export --alembic --from 0005 --down-revision <rev> \
      > migrations/versions/xxxx_soniq_0006.py
    ```

    `--sql --from 0005` gives plain SQL for other tools. See [Migrations with Alembic](migrations.md).

2. **Roll out the new code** to workers, schedulers and producers, in any order. Old and new workers process each other's jobs.

3. **Watch the logs for deprecation warnings** and fix them at your own pace. Nothing breaks until 1.0.

## Things to check in your code

- **Job names.** `@soniq.job()` and `@soniq.periodic(...)` without `name=` now warn. Set `name=` to exactly the name the warning prints. A different name breaks the link to jobs already queued under the old one, and those jobs go to the dead-letter queue as `job_not_found`. Rename later, after the queue has drained.
- **Arguments must be plain JSON.** `enqueue` rejects `NaN` and `Infinity` with `SONIQ_TASK_ARGS_INVALID`. It already rejected `datetime`, `UUID` and other objects.
- **Return values must be plain JSON.** A handler that returns a `datetime` or a Pydantic model used to have its `str()` stored. Now the job completes and the result is `None`, with an error in the worker log. During the roll, the same job can end up with either, depending on which worker ran it.
- **`SONIQ_DEPRECATIONS=error`** turns the warnings into errors, and that happens at import, as the decorators run. Leave it off until your job names are fixed.

Old rows need nothing from you. A stringified datetime that 0.1.0 put in `args` reaches the handler as the same string it always did, and old results expire after `result_ttl`.

## Rolling back

Deploy the 0.1.0 code again and leave the database as it is. 0.1.0 ignores the new functions, accepts the new column default, and reads the fixed tags. `soniq migrate-status` on 0.1.0 shows no pending migrations.

If you rolled back the dashboard too, its new endpoints (`/api/dead-letter`, `/api/workers`) go with it.
