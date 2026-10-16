# Benchmarks

`throughput.py` measures how many no-op jobs per second Soniq workers finish, and how long a job waits between enqueue and start.

## Run it

Start a Postgres 16 container and install Soniq:

```bash
docker run --rm -d --name soniq-bench -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=soniq_bench postgres:16
pip install -e .
```

Run the benchmark:

```bash
python bench/throughput.py --jobs 10000 --workers 2 --concurrency 8 \
  --database-url postgresql://postgres:postgres@localhost:5432/soniq_bench
```

You can also set `SONIQ_DATABASE_URL` instead of `--database-url`. The script only needs Soniq, `asyncpg` and the standard library.

## Options

| Option          | Default | Meaning                                  |
| --------------- | ------- | ---------------------------------------- |
| `--jobs`        | 10000   | Number of no-op jobs to enqueue.         |
| `--workers`     | 2       | Number of worker processes.              |
| `--concurrency` | 8       | Concurrency of each worker process.      |
| `--database-url`| env, then local `soniq_bench` | Postgres URL.      |
| `--batch`       | 500     | Jobs per `enqueue_many` call.            |
| `--warmup`      | 3       | Seconds to wait for workers to start.    |
| `--timeout`     | 600     | Seconds to wait for all jobs to finish.  |

## What it does

1. Runs `soniq setup` on the database and deletes old `bench.noop` rows.
2. Starts W `soniq worker` subprocesses. Each one claims jobs with `FOR UPDATE SKIP LOCKED`, so the numbers include real multi-process claiming.
3. Enqueues N jobs with `enqueue_many` while the workers run.
4. Waits until all jobs have status `done`, reads the results, and deletes the benchmark rows.
5. Prints the machine info and a markdown table.

Press Ctrl+C to stop. The script terminates its worker processes on exit.

## How the numbers are measured

- Jobs/sec is N divided by the time from the first `created_at` to the last job start.
- Enqueue/sec is N divided by the time spent in `enqueue_many`.
- Latency is the job start time minus `created_at`. The job writes its own start time (`time.time()`) into its result, so the clock does not depend on the worker's polling. Workers and Postgres must run on the same machine for the two clocks to match.

The producer enqueues faster than the workers finish jobs. A backlog builds up, so the latency figures include queue wait time. They are not the latency of an idle system.

The numbers depend on the machine, the Postgres settings and other load. Compare runs only on the same machine.
