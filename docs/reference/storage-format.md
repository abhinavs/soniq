# Storage format

Soniq keeps every job in PostgreSQL as plain rows and plain JSON. This page is the contract for those rows. Code in any language can read them with nothing but a Postgres driver and a JSON parser, and it can write jobs that a Soniq worker will run.

This is format 1. It came in with Soniq 0.2.0.

## Format 1 and older rows

Format 1 is a strict subset of what Soniq 0.1.0 already wrote. The shape didn't change. What changed is that values have to be standard JSON:

- `args` is a JSON object of keyword arguments.
- `result` is any JSON value, or SQL `NULL`.
- No value is a Python object turned into text. A `datetime` used to become `"2024-01-01 00:00:00+00:00"`. Now it is rejected at enqueue time.
- No `NaN`, `Infinity` or `-Infinity`. They aren't JSON.

There's no version column and no marker in the row. Every row 0.2.0 writes is also a valid 0.1.0 row, so a rolling deploy works in either direction, and so does a rollback.

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
| `max_attempts` | `int` | Total attempts allowed, so retries plus one. |
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

Every timestamp column is `timestamp with time zone`. Write times with an explicit offset, or use `NOW()`.

To enqueue from another language, insert a row with `status = 'queued'` and let the rest default:

```sql
INSERT INTO soniq_jobs (id, job_name, args, status, queue, max_attempts)
VALUES (gen_random_uuid(), 'billing.send_invoice', '{"order_id": 123}', 'queued', 'default', 3);
```

Do this inside your own transaction and the job exists only if that transaction commits.

A failed job doesn't get a status of its own. It goes back to `queued` with a later `scheduled_at` until it runs out of attempts. Then it moves to `soniq_dead_letter_jobs`.

## `soniq_dead_letter_jobs`

Jobs that failed for good. The row keeps the original `id`, `job_name`, `args`, `queue`, `priority`, `max_attempts`, `attempts` and `last_error`, plus:

| Column | Type | Meaning |
| --- | --- | --- |
| `dead_letter_reason` | `text` | `max_retries_exceeded`, `permanent_failure`, `job_not_found` or `invalid_arguments`. |
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
4. Take the SHA-256 of the UTF-8 bytes and write it as lowercase hex.

Test vector:

```text
args:      {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]}
canonical: {"order_id":42,"tags":["a","b"],"to":"zo\u00eb@example.com"}
args_hash: 4679a46d38de4bf4d70793166c09d90fb32fa2f36cac5f9193000b4e17bc2c9c
```

Floats are the weak spot. Languages don't agree on how to print them (`1.0` or `1`, `1e+16` or `10000000000000000.0`), so two producers can hash the same value differently. Keep floats out of the args of unique jobs, or send them as strings.

## Not language neutral yet

These parts still assume Python on both ends:

- **Running jobs.** Only Soniq's Python worker claims and runs jobs. The claim protocol isn't documented for other workers yet.
- **`last_error`.** It's text meant for people, often a Python traceback. Don't parse it.
- **Webhook secrets** in the webhook tables are encrypted with a Python library.
- **`soniq_task_registry.args_model_repr`** is a Python `repr`.
