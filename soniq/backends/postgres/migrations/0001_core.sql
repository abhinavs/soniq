-- Core schema: jobs, workers, producer_id, task registry.
--
-- Always applied by Soniq.setup(). Soniq-owned feature tables
-- (scheduler, dead_letter, webhooks, logs) live in their own migration
-- files but ship in the same core slice (0001-0099) so every install
-- gets them on first setup. See migrations/README.md.

-- ---------------------------------------------------------------------------
-- Jobs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS soniq_jobs (
  id UUID PRIMARY KEY,
  job_name TEXT NOT NULL,
  args JSONB NOT NULL,
  args_hash TEXT,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'done', 'cancelled')),
  attempts INT DEFAULT 0,
  max_attempts INT DEFAULT 3,
  queue TEXT DEFAULT 'default',
  priority INT DEFAULT 100,
  unique_job BOOLEAN DEFAULT FALSE,
  dedup_key TEXT,
  scheduled_at TIMESTAMP WITH TIME ZONE,
  expires_at TIMESTAMP WITH TIME ZONE,
  last_error TEXT,
  result JSONB,
  -- Producer-side observability: each row carries a small string the
  -- producer stamped at enqueue time so an oncall can answer 'who
  -- enqueued this poison message?' from the dashboard without grepping
  -- logs across services. NULLABLE on purpose; producers that opt out
  -- of stamping leave it NULL, and the dashboard renders NULL as
  -- 'unknown' or '-'.
  producer_id TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_soniq_jobs_status_priority
  ON soniq_jobs (status, priority) WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_soniq_jobs_queue_status
  ON soniq_jobs (queue, status);

-- Covers the worker's hot fetch:
-- WHERE queue = $1 AND status = 'queued' ORDER BY priority, scheduled_at
CREATE INDEX IF NOT EXISTS idx_soniq_jobs_queue_status_priority
  ON soniq_jobs (queue, status, priority, scheduled_at) WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_soniq_jobs_scheduled_at
  ON soniq_jobs (scheduled_at) WHERE scheduled_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_soniq_jobs_expires_at
  ON soniq_jobs (expires_at) WHERE expires_at IS NOT NULL;

-- Uniqueness while queued; completed jobs can be re-queued.
CREATE UNIQUE INDEX IF NOT EXISTS idx_soniq_jobs_unique_queued
  ON soniq_jobs (job_name, args_hash) WHERE status = 'queued' AND unique_job = TRUE;
CREATE INDEX IF NOT EXISTS idx_soniq_jobs_args_hash
  ON soniq_jobs (args_hash) WHERE unique_job = TRUE;

-- Dedup key: at most one queued job per key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_soniq_jobs_dedup_key
  ON soniq_jobs (dedup_key) WHERE status = 'queued' AND dedup_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Workers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS soniq_workers (
  id UUID PRIMARY KEY,
  hostname TEXT NOT NULL,
  pid INTEGER NOT NULL,
  queues TEXT[] DEFAULT '{}',
  concurrency INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'stopping', 'stopped')),
  last_heartbeat TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  version TEXT,
  metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_soniq_workers_last_heartbeat
  ON soniq_workers (last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_soniq_workers_status
  ON soniq_workers (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_soniq_workers_host_pid
  ON soniq_workers (hostname, pid);

-- ---------------------------------------------------------------------------
-- worker_id linkage on jobs
-- ---------------------------------------------------------------------------

ALTER TABLE soniq_jobs
  ADD COLUMN IF NOT EXISTS worker_id UUID;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'soniq_jobs_worker_id_fkey'
  ) THEN
    ALTER TABLE soniq_jobs
      ADD CONSTRAINT soniq_jobs_worker_id_fkey
      FOREIGN KEY (worker_id) REFERENCES soniq_workers(id) ON DELETE SET NULL;
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_soniq_jobs_worker_id
  ON soniq_jobs (worker_id) WHERE worker_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Task registry (observability metadata)
-- ---------------------------------------------------------------------------
-- Workers populate this table with the names they handle so the dashboard
-- can answer 'which workers handle billing.invoices.send.v2?' and a
-- deploy-skew detector can flag rows queued for names no worker has ever
-- registered.
--
-- LOAD-BEARING INVARIANT: this table is OBSERVABILITY METADATA ONLY. The
-- enqueue path never reads it. Adding a fallback in Soniq.enqueue that
-- consults this table would turn it into distributed coordination, which
-- is explicitly out of scope. Tests in tests/unit/test_enqueue.py and
-- tests/integration/test_cross_service_enqueue.py pin this invariant.
--
-- Composite primary key (task_name, worker_id) gives per-worker visibility
-- across the fleet without a future schema migration when a routing UI
-- wants to surface fleet topology.

CREATE TABLE IF NOT EXISTS soniq_task_registry (
  task_name TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
  args_model_repr TEXT,
  PRIMARY KEY (task_name, worker_id)
);

CREATE INDEX IF NOT EXISTS idx_soniq_task_registry_task_name
  ON soniq_task_registry (task_name);
