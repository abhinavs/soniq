-- Dead letter queue table.
--
-- Per docs/_internals/contracts/dead_letter.md (Option A), DLQ rows live exclusively in
-- soniq_dead_letter_jobs; this table is part of the base schema rather than
-- an opt-in feature so dashboard/metrics queries can rely on it existing.

CREATE TABLE IF NOT EXISTS soniq_dead_letter_jobs (
  id UUID PRIMARY KEY,
  job_name TEXT NOT NULL,
  args JSONB NOT NULL,
  queue TEXT NOT NULL,
  priority INTEGER NOT NULL,
  max_attempts INTEGER NOT NULL,
  attempts INTEGER NOT NULL,
  last_error TEXT,
  dead_letter_reason TEXT NOT NULL,
  original_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  moved_to_dead_letter_at TIMESTAMP WITH TIME ZONE NOT NULL,
  resurrection_count INTEGER DEFAULT 0,
  last_resurrection_at TIMESTAMP WITH TIME ZONE,
  tags JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_job_name
  ON soniq_dead_letter_jobs (job_name);
CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_queue
  ON soniq_dead_letter_jobs (queue);
CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_reason
  ON soniq_dead_letter_jobs (dead_letter_reason);
CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_moved_at
  ON soniq_dead_letter_jobs (moved_to_dead_letter_at);
