---
description: Upgrade Soniq from 0.1.0 to 0.2.0 with no downtime. Apply migration 0006, roll out workers in any order, fix deprecation warnings, and roll back if needed.
---

# Upgrade Soniq from 0.1.0 to 0.2.0

Upgrading from Soniq 0.1.0 to 0.2.0 needs no downtime. Jobs in the queue keep running, recurring schedules keep firing, and 0.1.0 and 0.2.0 workers can share one PostgreSQL database during the roll.

## What changes in the database

0.2.0 adds one migration, `0006_sql_api`. It only adds things and fixes data, so it is safe to apply while 0.1.0 workers are running:

- Two functions, `soniq_enqueue()` and `soniq_get_job()`, so code in other languages can enqueue jobs and read their results. See [Storage format](../reference/storage-format.md). 0.1.0 never calls them.
- The `soniq_jobs.max_attempts` column default goes from 3 to 4. Python always sets the value itself, so this only matters for raw `INSERT`s that leave the column out. 4 matches what Python writes by default (`retries=3`, plus the first attempt).
- 0.1.0 stored dead-letter `tags` as a JSON string holding an object. The migration rewrites those rows as objects, and 0.2.0 reads both shapes. 0.1.0 itself never sets tags, so only your own code calling `backend.mark_job_dead_letter(tags=...)` on 0.1.0 can write more in the old shape after the migration, and a dead-letter search by tag can miss those rows. If you do that, run the migration's `UPDATE soniq_dead_letter_jobs` statement by hand once the last 0.1.0 code is gone (`soniq migrations export --sql --from 0005` prints it). It is safe to run more than once.

The row format itself didn't change. Every row 0.2.0 writes is a valid 0.1.0 row, and the other way round.

## Steps

1. **Apply the migration once**, from one place, before or during the deploy:

    ```bash
    soniq setup myapp.jobs:soniq
    ```

    It applies only `0006_sql_api`. The migration changes a column default on `soniq_jobs`, which takes a brief exclusive lock on the table. It waits at most 5 seconds for that lock (busy workers hold short locks on the table) and fails rather than stall the queue behind it. If it fails, run `soniq setup` again: nothing was applied. If Alembic owns your schema, export just the new migration instead:

    ```bash
    soniq migrations export --alembic --from 0005 --down-revision <rev> \
      > migrations/versions/xxxx_soniq_0006.py
    ```

    `--sql --from 0005` gives plain SQL for other tools, wrapped in its own `BEGIN`/`COMMIT`. See [Migrations with Alembic](migrations.md).

    **If you applied `0006_sql_api` from a 0.2.0 release candidate**, apply it again. The final version fixes a privilege escalation in `soniq_enqueue()` on Postgres 14 and older, and `soniq setup` won't rerun a migration it has recorded. The migration is safe to run twice:

    ```bash
    soniq migrations export --sql --from 0005 | psql -v ON_ERROR_STOP=1 "$DATABASE_URL"
    ```

    **On a database created before Postgres 15 (including one moved to a newer version with `pg_upgrade`), if other roles can log in to the database**, also run `REVOKE CREATE ON SCHEMA public FROM PUBLIC` once as the database owner. Otherwise any of those roles can create a function in `public` that shadows a built-in and runs as whoever calls it, the superuser running `soniq setup` included. 0.2.0 qualifies the built-ins it calls, but your own SQL and extensions may not. See [Going to production](going-to-production.md#on-a-database-created-before-postgres-15-revoke-create-on-public).

2. **Roll out the new code** to workers, schedulers and producers, in any order. Old and new workers process each other's jobs.

3. **Watch the logs for deprecation warnings** and fix them at your own pace. Nothing breaks until 1.0.

## Things to check in your code

- **Job names.** `@soniq.job()` and `@soniq.periodic(...)` without `name=` now warn. Set `name=` to exactly the name the warning prints. A different name breaks the link to jobs already queued under the old one, and those jobs go to the dead-letter queue as `job_not_found`. Rename later, after the queue has drained. If the warning says the printed name doesn't match `task_name_pattern` (an uppercase letter or a leading underscore in the module path, say), `name=` will reject it. Either widen `SONIQ_TASK_NAME_PATTERN` so it matches, or wait until no jobs are queued under the old name and then switch to a name that fits.
- **Arguments must be plain JSON.** `enqueue` rejects `NaN` and `Infinity` with `SONIQ_TASK_ARGS_INVALID`. It already rejected `datetime`, `UUID` and other objects.
- **Return values must be plain JSON.** A handler that returns a `datetime` or a Pydantic model used to have its `str()` stored. Now the job completes and the result is `None`, with an error in the worker log. During the roll, the same job can end up with either, depending on which worker ran it.
- **`SONIQ_DEPRECATIONS=error`** turns the warnings into errors, and that happens at import, as the decorators run. Leave it off until your job names are fixed.

Old rows need nothing from you. A stringified datetime that 0.1.0 put in `args` reaches the handler as the same string it always did, and old results expire after `result_ttl`.

## Rolling back

Deploy the 0.1.0 code again and leave the database as it is. 0.1.0 ignores the new functions, accepts the new column default, and reads the fixed tags. `soniq migrate-status` on 0.1.0 shows no pending migrations.

If you rolled back the dashboard too, its new endpoints (`/api/dead-letter`, `/api/workers`) go with it.

## Next steps

- [Migrations with Alembic](migrations.md) - export the 0006 migration as an Alembic revision
- [Going to production](going-to-production.md) - the production checklist
