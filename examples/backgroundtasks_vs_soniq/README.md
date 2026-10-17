# BackgroundTasks vs Soniq

Two small FastAPI apps send a "welcome email" after signup. The email is a row in an `emails` table, written after a 5-second sleep (a stand-in for a slow mail provider).

- `before/app.py` uses FastAPI `BackgroundTasks`. The pending work lives only in process memory.
- `after/app.py` enqueues a Soniq job and runs the worker in the same process with `soniq.integrations.fastapi.lifespan(soniq, run_worker=True, concurrency=2)`. The pending work lives in Postgres.

## Run it

You need a local Postgres and this repo installed with FastAPI and uvicorn.

```bash
createdb soniq_demo
DB=soniq_demo PY=/path/to/venv/bin/python ./run_demo.sh
```

The script does this for each app:

1. Create an empty `emails` table and the Soniq schema.
2. Start the app. Send 20 signups at once.
3. Wait 2 seconds. Send SIGTERM.
4. Count the emails (`at_sigterm`).
5. Start the app again. Wait until all 20 emails exist, or until 60 seconds pass.
6. Count the emails again (`after_restart`). `lost` is 20 minus that count.

## Settings that change the result

| Setting | Default | Effect |
|---|---|---|
| `GRACE` | `1` | Passed to uvicorn as `--timeout-graceful-shutdown`. After SIGTERM, uvicorn waits this long for running requests and background tasks, then cancels them. |
| `SEND_SECONDS` | `5` | Time one email takes. |
| `KILL_AFTER` | `2` | Seconds between the signups and SIGTERM. |
| `WAIT_AFTER` | `60` | Longest wait after the restart. |

The default uvicorn setting is to wait with no limit. With `GRACE=0` (no limit) `before` also loses nothing, because uvicorn keeps the process alive until every background task is done. Try it: `GRACE=0 ./run_demo.sh`. Most deploys do set a limit (a process manager or an orchestrator kills the process after a grace period), and a short limit is the case this demo shows. A crash or SIGKILL loses the `before` tasks in the same way, with any setting.

For `after`, Soniq uses its default `shutdown_timeout` of 30 seconds. The two running jobs (5 seconds each) finish inside that time, so none is cancelled. The other 18 jobs stay in the `soniq_jobs` table and the restarted app runs them. If a job did run past `shutdown_timeout`, Soniq would put it back in the queue and it would run again after the restart. See the [shutdown contract](../../docs/_internals/contracts/shutdown.md).

With `concurrency=2` and 5 seconds per job, 20 jobs need about 50 seconds. That is why the script waits up to 60 seconds after the restart. A count taken earlier would show jobs that are still queued, not jobs that are lost.

Handlers can run more than once. The email write uses `ON CONFLICT DO NOTHING`, so a repeat is harmless.
