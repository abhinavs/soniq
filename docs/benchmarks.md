# Benchmarks

These numbers show how Soniq behaves on one developer laptop. They are not from a tuned server. Do not use them as a capacity plan. Run the script on your own hardware to get numbers that matter for you.

## What the test does

The script `bench/throughput.py` starts W worker processes and enqueues N no-op jobs. It waits until all jobs are done. It then reports:

- **Jobs/sec:** N divided by the time from the first enqueue to the last job start.
- **Enqueue/sec:** N divided by the time spent in `enqueue_many`.
- **Latency:** the time between the job row being created and the job starting. p50, p95 and p99 are the 50th, 95th and 99th percentiles.

The job records its own start time, so the latency does not depend on worker polling. The producer enqueues faster than the workers finish jobs. A backlog builds up, so the latency includes the time a job waits in the queue.

## Machine

- OS: macOS (Darwin 25.4.0, arm64)
- CPU: Apple M1 Pro (10 logical cores)
- RAM: 16 GB
- Postgres: 16.4 (`postgres:16` Docker image, run with Docker on OrbStack on the same Mac, default settings)
- Python: 3.13.5

Nothing else heavy ran on the machine during these runs. Each row is the median of 3 runs, after one discarded warm-up run.

## Results

Command for each row (only `--jobs` changes), with `--database-url` pointing at the Docker container:

```bash
python bench/throughput.py --jobs 10000 --workers 2 --concurrency 8
```

| Jobs  | Workers | Concurrency | Jobs/sec | Enqueue/sec | p50 ms  | p95 ms  | p99 ms  |
| ----- | ------- | ----------- | -------- | ----------- | ------- | ------- | ------- |
| 1000  | 2       | 8           | 1682     | 8464        | 319     | 510     | 527     |
| 10000 | 2       | 8           | 1136     | 19283       | 4979    | 8095    | 8281    |
| 50000 | 2       | 8           | 296      | 16730       | 110112  | 163137  | 165610  |

Throughput fell as the backlog grew. A short queue was much faster than a queue of tens of thousands of waiting jobs. The first 50000 job run was the fastest (399 jobs/sec), and the other two gave 296 and 291.

An earlier version of this page used Homebrew Postgres 14.19 running natively, and it measured higher throughput for the 1000 and 10000 job rows (3984 and 1940 jobs/sec). Postgres in Docker adds networking and storage overhead, so that gap says little about Postgres 14 versus 16.

## Reproduce

Use Postgres 16 in Docker. The steps are in [`bench/README.md`](https://github.com/abhinavs/soniq/blob/main/bench/README.md).
