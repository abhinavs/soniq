"""Throughput and latency benchmark for Soniq workers.

Starts W worker processes, enqueues N no-op jobs while they run, waits for
all jobs to finish, then prints jobs/sec and enqueue-to-start latency.

    python bench/throughput.py --jobs 10000 --workers 2 --concurrency 8

The same file is the job module the workers import, so it must stay
importable without side effects.
"""

import argparse
import asyncio
import os
import platform
import statistics
import subprocess
import sys
import time

import asyncpg

from soniq import Soniq

JOB_NAME = "bench.noop"
DEFAULT_URL = "postgresql://postgres@localhost:5432/soniq_bench"

soniq = Soniq(database_url=os.environ.get("SONIQ_DATABASE_URL", DEFAULT_URL))


@soniq.job(name=JOB_NAME)
async def noop():
    # The job records its own start time. Workers and the database run on
    # the same machine, so this clock is comparable to jobs.created_at.
    return {"started": time.time()}


def _sh(*cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "0"


def machine_info(pg_version: str) -> dict:
    cpu = platform.processor() or "unknown"
    ram = 0.0
    if sys.platform == "darwin":
        cpu = _sh("sysctl", "-n", "machdep.cpu.brand_string")
        ram = int(_sh("sysctl", "-n", "hw.memsize")) / 2**30
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
            with open("/proc/meminfo") as f:
                ram = int(f.readline().split()[1]) / 2**20
        except OSError:
            pass
    return {
        "OS": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "CPU": f"{cpu} ({os.cpu_count()} logical cores)",
        "RAM": f"{ram:.0f} GB",
        "Postgres": pg_version,
        "Python": platform.python_version(),
    }


def pct(sorted_vals: list, p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


async def run(args) -> None:
    conn = await asyncpg.connect(args.database_url)
    workers: list = []
    try:
        pg_version = (await conn.fetchval("SHOW server_version")).split()[0]
        clean = "DELETE FROM soniq_jobs WHERE job_name = $1"

        producer = Soniq(database_url=args.database_url, enqueue_validation="none")
        await producer.setup()
        await conn.execute(clean, JOB_NAME)

        env = dict(
            os.environ,
            SONIQ_DATABASE_URL=args.database_url,
            SONIQ_JOBS_MODULES="throughput",
            SONIQ_RESULT_TTL="86400",  # keep finished rows until we read them
            PYTHONPATH=os.path.dirname(os.path.abspath(__file__)),
        )
        cmd = [sys.executable, "-m", "soniq.cli.main", "worker"]
        cmd += ["--concurrency", str(args.concurrency), "--log-level", "ERROR"]
        for _ in range(args.workers):
            workers.append(
                subprocess.Popen(
                    cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
            )
        await asyncio.sleep(args.warmup)  # let workers import and connect
        for w in workers:
            if w.poll() is not None:
                raise RuntimeError(f"worker exited early: {w.stderr.read().decode()}")

        t0 = time.time()
        for i in range(0, args.jobs, args.batch):
            n = min(args.batch, args.jobs - i)
            await producer.enqueue_many(JOB_NAME, [{}] * n)
        enqueue_secs = time.time() - t0

        deadline = time.time() + args.timeout
        while True:
            done = await conn.fetchval(
                "SELECT count(*) FROM soniq_jobs WHERE job_name = $1 AND status = 'done'",
                JOB_NAME,
            )
            if done >= args.jobs:
                break
            if time.time() > deadline:
                raise TimeoutError(f"only {done}/{args.jobs} jobs finished")
            await asyncio.sleep(0.25)

        rows = await conn.fetch(
            "SELECT extract(epoch FROM created_at)::float8 AS c, "
            "(result->>'started')::float8 AS s FROM soniq_jobs WHERE job_name = $1",
            JOB_NAME,
        )
        await conn.execute(clean, JOB_NAME)
        await producer.close()
    finally:
        for w in workers:
            w.terminate()
        for w in workers:
            try:
                w.wait(timeout=10)
            except subprocess.TimeoutExpired:
                w.kill()
        await conn.close()

    lat = sorted((r["s"] - r["c"]) * 1000 for r in rows)
    total = max(r["s"] for r in rows) - min(r["c"] for r in rows)

    print("\n### Machine\n")
    for k, v in machine_info(pg_version).items():
        print(f"- {k}: {v}")
    print("\n### Results\n")
    print(
        "| Jobs | Workers | Concurrency | Jobs/sec | Enqueue/sec | p50 ms | p95 ms | p99 ms |"
    )
    print("|---|---|---|---|---|---|---|---|")
    print(
        f"| {args.jobs} | {args.workers} | {args.concurrency} "
        f"| {args.jobs / total:.0f} | {args.jobs / enqueue_secs:.0f} "
        f"| {statistics.median(lat):.0f} | {pct(lat, 0.95):.0f} | {pct(lat, 0.99):.0f} |"
    )


def main() -> int:
    env_url = os.environ.get("SONIQ_DATABASE_URL", DEFAULT_URL)
    p = argparse.ArgumentParser(description="Soniq throughput benchmark")
    p.add_argument("--jobs", type=int, default=10000)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--database-url", default=env_url)
    p.add_argument("--batch", type=int, default=500, help="enqueue_many batch size")
    p.add_argument(
        "--warmup", type=float, default=3.0, help="seconds for workers to start"
    )
    p.add_argument("--timeout", type=float, default=600.0)
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
