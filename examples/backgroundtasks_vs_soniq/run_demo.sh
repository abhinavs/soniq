#!/usr/bin/env bash
# Send 20 signups, SIGTERM the server after 2 seconds, restart it, count emails.
# Needs a local Postgres. Override any setting with an environment variable.
set -u
cd "$(dirname "$0")"

PY=${PY:-../../.venv/bin/python}
SONIQ=${SONIQ:-$(dirname "$PY")/soniq}
GRACE=${GRACE:-1}               # uvicorn --timeout-graceful-shutdown, seconds; 0 = uvicorn default (wait forever)
DB=${DB:-soniq_m8}
PGUSER_NAME=${PGUSER_NAME:-postgres}
PORT=${PORT:-8018}
SIGNUPS=${SIGNUPS:-20}
KILL_AFTER=${KILL_AFTER:-2}     # seconds between the signups and SIGTERM
WAIT_AFTER=${WAIT_AFTER:-60}    # max seconds the restarted app runs; ends early once all emails exist
export SEND_SECONDS=${SEND_SECONDS:-5}
export DATABASE_URL=${DATABASE_URL:-postgresql://$PGUSER_NAME@localhost/$DB}
export SONIQ_DATABASE_URL=$DATABASE_URL
PSQL="psql -U $PGUSER_NAME -d $DB -tA -q"
PID=""
GRACE_FLAG=""; [ "$GRACE" != 0 ] && GRACE_FLAG="--timeout-graceful-shutdown $GRACE"
LOG=$(mktemp)

cleanup() { [ -n "$PID" ] && kill -9 "$PID" 2>/dev/null; true; }
trap cleanup EXIT

start() {
  "$PY" -m uvicorn app:app --app-dir "$1" --port "$PORT" --log-level warning $GRACE_FLAG >>"$LOG" 2>&1 &
  PID=$!
  for _ in $(seq 100); do
    curl -sf "localhost:$PORT/health" >/dev/null && return 0
    sleep 0.1
  done
  echo "server did not start, see $LOG"; exit 1
}

stop() { kill -TERM "$PID"; wait "$PID" 2>/dev/null; PID=""; }

run_case() {  # $1 = before | after
  $PSQL -c "DROP TABLE IF EXISTS emails; CREATE TABLE emails (user_id int PRIMARY KEY);" >/dev/null
  "$SONIQ" setup >>"$LOG" 2>&1
  $PSQL -c "TRUNCATE soniq_jobs" >/dev/null
  start "$1"
  pids=()
  for i in $(seq "$SIGNUPS"); do
    curl -sf -X POST "localhost:$PORT/signup/$i" >/dev/null &
    pids+=($!)
  done
  wait "${pids[@]}"
  sleep "$KILL_AFTER"
  stop
  at_kill=$($PSQL -c "SELECT count(*) FROM emails")
  start "$1"
  for _ in $(seq "$WAIT_AFTER"); do
    [ "$($PSQL -c "SELECT count(*) FROM emails")" -ge "$SIGNUPS" ] && break
    sleep 1
  done
  stop
  final=$($PSQL -c "SELECT count(*) FROM emails")
  printf "%-8s %9s %10s %13s %6s\n" "$1" "$SIGNUPS" "$at_kill" "$final" "$((SIGNUPS - final))"
}

echo "signups=$SIGNUPS send_time=${SEND_SECONDS}s sigterm_after=${KILL_AFTER}s max_wait_after_restart=${WAIT_AFTER}s uvicorn_grace=${GRACE}s"
printf "%-8s %9s %10s %13s %6s\n" "case" "signups" "at_sigterm" "after_restart" "lost"
run_case before
run_case after
