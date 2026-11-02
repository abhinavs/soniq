---
description: "Soniq storage format 1: PostgreSQL tables, columns and SQL functions (soniq_enqueue, soniq_get_job) to enqueue and read background jobs from any language."
---

# Soniq storage format: PostgreSQL job tables and SQL API

Soniq keeps every job in PostgreSQL as plain rows and plain JSON. This page is the contract for those rows. Code in any language can read them with a Postgres driver and a JSON parser, and can write jobs that a Soniq worker runs.

This is format 1. It came in with Soniq 0.2.0.

## Format 1 and older rows

Format 1 is a strict subset of what Soniq 0.1.0 already wrote. The shape didn't change. What changed is that values have to be standard JSON:

- `args` is a JSON object of keyword arguments.
- `result` is any JSON value, or SQL `NULL`.
- No value is a Python object turned into text. A `datetime` used to become `"2024-01-01 00:00:00+00:00"`. Now it is rejected at enqueue time.
- No `NaN`, `Infinity` or `-Infinity`. They aren't JSON.

There's no version column and no marker in the row. Every row 0.2.0 writes is also a valid 0.1.0 row, so a rolling deploy works in either direction, and so does a rollback. The one exception is a row you write with `max_attempts` set to `NULL`: a 0.1.0 worker that claims it raises `TypeError` and leaves it in `processing`. Don't write `NULL` until no 0.1.0 worker is left.

Rows that older versions wrote are still processed. A stringified datetime in old `args` is just a JSON string, and the handler gets that string, as it always did. These rows go away by themselves:

- Queued jobs run and finish.
- Results expire after `result_ttl` (300 seconds by default).
- Recurring schedules are rewritten from code each time a scheduler registers them.
- Dead-letter rows keep whatever they had. Replaying one passes its `args` through unchanged.

## `soniq_jobs`

One row per job.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `uuid` | Job id. Generate a random UUID (v4) when you insert. |
| `job_name` | `text` | Which handler runs it. See [Job names](#job-names). |
| `args` | `jsonb` | Keyword arguments, as a JSON object. `{}` for none. |
| `args_hash` | `text` | Set only for unique jobs. See [`args_hash`](#args_hash). |
| `status` | `text` | `queued`, `processing`, `done` or `cancelled`. |
| `attempts` | `int` | How many times a worker has started this job. Insert `0`. |
| `max_attempts` | `int` | Total attempts allowed, so retries plus one. Defaults to `4`. `NULL` makes the worker use the handler's `retries`. 0.1.0 workers can't run such a row, so only use `NULL` once every worker runs 0.2.0. |
| `queue` | `text` | Queue name. `default` unless you route it. |
| `priority` | `int` | Lower runs first. Default `100`. |
| `unique_job` | `boolean` | `true` for a unique job. |
| `dedup_key` | `text` | Optional caller-chosen key for deduplication. |
| `scheduled_at` | `timestamptz` | Don't run before this. `NULL` means now. |
| `expires_at` | `timestamptz` | When a finished row may be deleted. Set by the worker. |
| `last_error` | `text` | The last error, as text for people to read. |
| `result` | `jsonb` | The handler's return value. Set by the worker. |
| `producer_id` | `text` | Free-form name of whoever enqueued the job. |
| `worker_id` | `uuid` | The worker running it, from `soniq_workers`. Set by the worker. Leave it `NULL`. |
| `created_at`, `updated_at` | `timestamptz` | Bookkeeping. Let them default. |

Every timestamp column is `timestamp with time zone`. Write times with an explicit offset, or use `NOW()`. The database must use UTF8 encoding, the Postgres default.

To enqueue from another language, see [Enqueue from any language](#enqueue-from-any-language).

A failed job doesn't get a status of its own. It goes back to `queued` with a later `scheduled_at` until it runs out of attempts. Then it moves to `soniq_dead_letter_jobs`.

## Enqueue from any language

Call `soniq_enqueue()`. It is a plain SQL function, so any Postgres driver can call it. It checks that the job name matches the pattern and that args are a JSON object, computes `args_hash` for unique jobs, returns the id of a queued duplicate instead of failing, and wakes idle workers.

It can't see your handler, so it doesn't check args against its signature the way Python's `enqueue` does. A job whose args don't fit the handler fails on every attempt and lands in the dead-letter table as `max_retries_exceeded`.

```sql
SELECT soniq_enqueue('billing.send_invoice', '{"order_id": 123}');

-- Every option, by name. Leave out what you don't need.
SELECT soniq_enqueue(
  'billing.send_invoice', '{"order_id": 123}',
  queue        => 'billing',
  priority     => 10,                          -- lower runs first, default 100
  run_at       => now() + interval '1 hour',   -- default: now
  max_attempts => 4,                           -- retries plus one, default 4; NULL uses the handler's retries (0.2.0+ workers only)
  unique_job   => true,
  dedup_key    => 'invoice-123',
  producer_id  => 'checkout-go',               -- default: application_name, or session_user if that's empty
  job_id       => '6f1c...'::uuid              -- default: a random UUID
);
```

It returns the job's id. Call it inside your own transaction and the job exists only if that transaction commits. Workers are woken at commit, so a rollback wakes nobody.

The handler's decorator settings don't apply here. A job enqueued from SQL gets the `queue`, `priority` and `max_attempts` you pass, or the defaults above, not the `queue=` or `retries=` on `@soniq.job()`. The same goes for `unique=`: pass `unique_job => true` yourself.

Bad input raises SQLSTATE `22023`, with the Soniq error code in the error's `HINT`:

- `SONIQ_INVALID_TASK_NAME` for a name that doesn't match the pattern.
- `SONIQ_TASK_ARGS_INVALID` for args that aren't a JSON object, that hold a number too big for a 64-bit float (`1e400`, which Python would read as `inf`), or that nest 500 or more levels deep.
- `SONIQ_INVALID_MAX_ATTEMPTS` for a `max_attempts` below 1.

A `job_id` that already exists raises `23505`. Args holding a NUL character (`\u0000`) fail with `22P05` before `soniq_enqueue` runs, because `jsonb` can't store it.

Job names are checked against `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`. If your Python side sets a different `task_name_pattern`, set the same one for the database:

```sql
ALTER DATABASE myapp SET soniq.task_name_pattern = '^[a-z][a-z0-9_.-]*$';
```

Only the database-level setting counts. A `SET` or `ALTER ROLE` from a caller is ignored, so a producer can't loosen the check for itself.

With `unique_job` or `dedup_key`, a caller in a `REPEATABLE READ` or `SERIALIZABLE` transaction that races another producer enqueueing the same job gets a serialization failure (SQLSTATE `40001`) instead of the other job's id. Retry the transaction, as you would for any serialization failure. At the default `READ COMMITTED` this doesn't happen.

### Read a job back

```sql
SELECT status, result, last_error FROM soniq_get_job('6f1c...'::uuid);
```

It returns these columns: `id`, `job_name`, `status`, `queue`, `attempts`, `max_attempts`, `result`, `last_error`, `dead_letter_reason`, `scheduled_at`, `created_at` and `updated_at`. A job that failed for good comes back with status `dead_letter`. No row means the id is unknown, or the job finished and its result expired after `result_ttl`.

### Permissions

Both functions run with their owner's rights, so a service account needs no access to the tables. They are not callable by everyone by default. Grant them to each producer:

```sql
GRANT USAGE ON SCHEMA public TO checkout_service;
GRANT EXECUTE ON FUNCTION soniq_enqueue, soniq_get_job TO checkout_service;
```

On Postgres 14 and older every role can create objects in `public` unless you revoke it. The functions call built-ins by their `pg_catalog` names, so a planted function can't run with their owner's rights, but a planted overload can still make a call ambiguous and break enqueueing. If the roles sharing the database don't fully trust each other, you must run this on Postgres 14 and older (Postgres 15 does it by default):

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

### Trust model

Everyone granted `soniq_enqueue` shares one namespace. Dedup keys, unique hashes and job ids are not scoped to a producer, so a producer that reuses another's `dedup_key` gets that producer's job id back instead of a new job. It can also park a job under a key first and block the real one.

`soniq_get_job` returns the status, result and last error of any job to any role that can execute it, whoever enqueued it.

If your producers don't trust each other, give each a prefix for its dedup keys (`billing:order-123`), keep secrets out of job results, or use separate databases.

`producer_id` defaults to the connection's `application_name`, and any client can set that to anything. Treat it as a label for debugging, not as proof of who enqueued a job.

### Raw INSERT

If you can't call a function, insert the row yourself:

```sql
INSERT INTO soniq_jobs (id, job_name, args, status, queue, max_attempts)
VALUES (gen_random_uuid(), 'billing.send_invoice', '{"order_id": 123}', 'queued', 'default', 4);
SELECT pg_notify('soniq_new_job', 'default');
```

The `pg_notify` wakes idle workers. Without it they still find the job on their next poll, within `poll_interval` (5 seconds by default). You check the name and args yourself, and for a unique job you compute [`args_hash`](#args_hash) and handle the conflict.

## `soniq_dead_letter_jobs`

Jobs that failed for good. The row keeps the original `id`, `job_name`, `args`, `queue`, `priority`, `max_attempts`, `attempts` and `last_error`, plus:

| Column | Type | Meaning |
| --- | --- | --- |
| `dead_letter_reason` | `text` | Why the job failed for good. Workers write `max_retries_exceeded`, `permanent_failure`, `job_not_found` or `invalid_arguments`. |
| `original_created_at` | `timestamptz` | When the job was first enqueued. |
| `moved_to_dead_letter_at` | `timestamptz` | When it landed here. |
| `resurrection_count` | `int` | How many times it was replayed. |
| `last_resurrection_at` | `timestamptz` | When it was last replayed. |
| `tags` | `jsonb` | A JSON object of string tags, or `NULL`. |

## `soniq_recurring_jobs`

Periodic schedules. The scheduler reads these and inserts a `soniq_jobs` row each time one is due.

| Column | Type | Meaning |
| --- | --- | --- |
| `job_name` | `text` | Handler to run. |
| `schedule_type` | `text` | `interval` or `cron`. |
| `schedule_value` | `text` | Seconds as a string for `interval` (`"900"`), or a five-field cron expression. |
| `job_kwargs` | `jsonb` | The `args` each run gets, as a JSON object. |
| `status` | `text` | `active` or `paused`. |
| `queue`, `priority`, `max_attempts` | | Copied onto every job it creates. |
| `id` | `uuid` | Required, with no default. Generate one. |
| `next_run`, `last_run`, `run_count`, `last_job_id` | | Kept up to date by the scheduler. Leave `next_run` `NULL` and the scheduler works it out. |

To add a schedule from SQL:

```sql
INSERT INTO soniq_recurring_jobs (id, job_name, schedule_type, schedule_value, job_kwargs)
VALUES (gen_random_uuid(), 'reports.daily', 'cron', '0 9 * * *', '{}');
```

Keep one row per `job_name`: the scheduler keys schedules by it. It reads the table when it starts, so restart `soniq scheduler` to pick up a row you inserted. Schedules defined in code (`@soniq.periodic`) are rewritten from code, so don't hand-edit those rows.

## Job names

A job name is the only link between a row and the code that runs it, so pick it on purpose:

```python
@soniq.job(name="billing.send_invoice")
async def send_invoice(order_id: int): ...
```

Names must match `task_name_pattern`. By default that's lowercase words joined by dots, like `billing.send_invoice`.

If you leave `name=` out, Soniq still uses `module.function`, which is a Python import path that other languages have no reason to know. That's deprecated since 0.2.0 and goes away in 1.0. To move off it without breaking jobs already in the queue, set `name=` to exactly the name Soniq derived. The warning prints it.

## `args_hash`

Unique jobs (`unique_job = true`) carry a hash of their `args`. A partial unique index on `(job_name, args_hash)` over queued rows is what stops a duplicate. If you insert unique jobs yourself, compute the hash the same way:

1. Serialize `args` as JSON with keys sorted at every level.
2. Use `,` and `:` as separators, with no spaces.
3. Escape strings the way Python's `json.dumps` does by default:
   - `"`, `\`, newline, carriage return, tab, backspace and form feed use their short forms (`\"`, `\\`, `\n`, `\r`, `\t`, `\b`, `\f`).
   - Every other character below U+0020, and U+007F, becomes `\u00XX` in lowercase hex, so DEL is `\u007f`.
   - Every non-ASCII character becomes `\uXXXX` in lowercase hex, with surrogate pairs above U+FFFF. `é` is `\u00e9` and `😀` is `\ud83d\ude00`.
   - `/` is not escaped.
4. Write numbers the way Postgres prints them back from `jsonb`: integers as digits, other numbers in plain decimal with no exponent (`1e16` as `10000000000000000`, `1e-7` as `0.0000001`), and negative zero as `0.0`.
5. Take the SHA-256 of the UTF-8 bytes and write it as lowercase hex.

Test vector:

```text
args:      {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]}
canonical: {"order_id":42,"tags":["a","b"],"to":"zo\u00eb@example.com"}
args_hash: 4679a46d38de4bf4d70793166c09d90fb32fa2f36cac5f9193000b4e17bc2c9c
```

`soniq_args_hash(args jsonb)` in the database computes the same hash, so you can check your own implementation against it.

Floats are the weak spot. Python keeps `1.0` and `1` apart, and so does `jsonb`, but other languages often don't, so two producers can hash the same value differently. Keep floats out of the args of unique jobs, or send them as strings.

Python turns non-string dict keys into strings first, as `json.dumps` does, so `{10: "x"}` hashes like `{"10": "x"}`. Args of a unique job can nest at most 499 levels deep, in Python and in SQL.

## Not language neutral yet

These parts still assume Python on both ends:

- **Running jobs.** Only Soniq's Python worker claims and runs jobs. The claim protocol isn't documented for other workers yet. Workers `LISTEN` for new jobs, which needs a session-mode connection if they go through PgBouncer.
- **`last_error`.** It's text meant for people, often a Python traceback. Don't parse it.
- **Webhook secrets** in the webhook tables are encrypted with a Python library.
- **`soniq_task_registry.args_model_repr`** is a Python `repr`.
