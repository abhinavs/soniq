#!/usr/bin/env bash
# Runs the README quickstart in a clean venv and checks the worker prints the job output.
# Uses SONIQ_DATABASE_URL if set. Otherwise starts a Docker Postgres 16 container.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
container=""
worker_pid=""

cleanup() {
  if [ -n "$worker_pid" ]; then kill "$worker_pid" 2>/dev/null || true; fi
  if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  rm -rf "$work"
}
trap cleanup EXIT

if [ -z "${SONIQ_DATABASE_URL:-}" ]; then
  container="$(docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16)"
  export SONIQ_DATABASE_URL="postgresql://postgres:postgres@localhost:55432/postgres"
  for _ in $(seq 30); do
    docker exec "$container" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
fi
export SONIQ_DATABASE_URL
export SONIQ_JOBS_MODULES=jobs

python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install -q "$root"
export PATH="$work/venv/bin:$PATH"

# Take jobs.py from the first python block of the README quickstart.
mkdir "$work/app"
python3 - "$root/README.md" "$work/app/jobs.py" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
quick = text.split("## Quickstart", 1)[1]
block = re.search(r"```python\n(.*?)```", quick, re.S).group(1)
open(sys.argv[2], "w").write(block)
PY

cd "$work/app"
soniq setup
PYTHONUNBUFFERED=1 soniq worker --concurrency 1 >"$work/worker.log" 2>&1 &
worker_pid=$!
sleep 3
python jobs.py

for _ in $(seq 20); do
  if grep -q "Sending welcome email to dev@example.com" "$work/worker.log"; then
    echo "OK: worker printed the welcome line"
    exit 0
  fi
  sleep 1
done
echo "FAIL: welcome line not found. Worker log:"
cat "$work/worker.log"
exit 1
