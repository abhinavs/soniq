#!/bin/sh
# Usage: BASE_URL=http://localhost:8000 ./demo.sh
set -eu
BASE_URL="${BASE_URL:-http://localhost:8000}"
fail() { echo "FAIL: $1"; exit 1; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

before=$(curl -fsS "$BASE_URL/state")
orders_before=$(echo "$before" | field "['orders']")
jobs_before=$(echo "$before" | field "['jobs'].get('process_order:done',0)")
sent_before=$(echo "$before" | field "['welcome_sent']")

echo "1. Sign up a user. The welcome email job should run."
curl -fsS -X POST "$BASE_URL/signup?email=ada@example.com"; echo
echo "2. Create a good order. It commits with its job."
curl -fsS -X POST "$BASE_URL/orders?item=book"; echo
echo "3. Create a failing order. Both the order and its job roll back."
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/orders?item=cursed&fail=1")
[ "$code" = "500" ] || fail "expected 500 from the failing order, got $code"

echo "Waiting for the worker..."
i=0
while [ $i -lt 20 ]; do
  s=$(curl -fsS "$BASE_URL/state")
  [ "$(echo "$s" | field "['welcome_sent']")" -gt "$sent_before" ] && break
  i=$((i + 1)); sleep 0.5
done
sleep 1
s=$(curl -fsS "$BASE_URL/state")
echo "$s"

[ "$(echo "$s" | field "['welcome_sent']")" -eq $((sent_before + 1)) ] || fail "welcome job did not run"
[ "$(echo "$s" | field "['orders']")" -eq $((orders_before + 1)) ] || fail "rolled-back order was saved"
[ "$(echo "$s" | field "['jobs'].get('process_order:done',0)")" -eq $((jobs_before + 1)) ] || fail "expected exactly one new order job"
echo "OK: job ran, rolled-back order left no row and no job."
echo "Open the dashboard (http://localhost:6161) to see both."
