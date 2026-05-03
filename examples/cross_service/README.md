# Cross-service enqueue (manual sanity)

Two scripts that share a single Postgres database. The producer
service has no local registry and writes by name; the consumer
service registers the handler and runs the worker.

## Run it

In one shell:

    export SONIQ_DATABASE_URL="postgresql://localhost/soniq_demo"
    soniq setup
    python consumer.py

In another shell (same `SONIQ_DATABASE_URL`):

    python producer.py

The producer prints the job id; the consumer logs the handler running
with the producer's args.

This is the "manual sanity" check from
`impl_plan_multi.md` phase 1 exit criteria. The MemoryBackend
equivalent lives in `tests/integration/test_cross_service_enqueue.py`
and runs as part of the test suite; this example is for verifying the
real Postgres path end-to-end before cutting a release.
