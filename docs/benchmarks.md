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
- Postgres: 14.19 (Homebrew, default settings, same machine as the workers)
- Python: 3.14.6

Other test suites ran on the same machine at the same time as some of these runs. Treat the numbers as a rough floor.

## Results

Command for each row (only `--jobs` changes):

```bash
python bench/throughput.py --jobs 10000 --workers 2 --concurrency 8
```

| Jobs  | Workers | Concurrency | Jobs/sec | Enqueue/sec | p50 ms  | p95 ms  | p99 ms  |
| ----- | ------- | ----------- | -------- | ----------- | ------- | ------- | ------- |
| 1000  | 2       | 8           | 3984     | 15278       | 142     | 200     | 206     |
| 10000 | 2       | 8           | 1940     | 21937       | 3069    | 4627    | 4699    |
| 50000 | 2       | 8           | 169      | 17402       | 224696  | 291955  | 292806  |

A second run of the 50000 job test, on a freshly vacuumed table, gave 137 jobs/sec. Throughput fell as the backlog grew. On this machine, a short queue was much faster than a queue of tens of thousands of waiting jobs. Part of this may come from load on the laptop, so check it on your own hardware.

## Reproduce

Use Postgres 16 in Docker. The steps are in [`bench/README.md`](https://github.com/abhinavs/soniq/blob/main/bench/README.md). The numbers above come from Postgres 14, not 16.
