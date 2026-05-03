-- Structured logging: persisted log records.
--
-- Promoted to the core slice. Empty when the deployment does not opt
-- into structured logging persistence.

CREATE TABLE IF NOT EXISTS soniq_logs (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  logger_name TEXT NOT NULL,
  module TEXT,
  function TEXT,
  line_number INTEGER,
  request_id TEXT,
  job_id TEXT,
  job_name TEXT,
  queue TEXT,
  extra_data JSONB,
  exception_data JSONB,
  performance_data JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_soniq_logs_timestamp
  ON soniq_logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_soniq_logs_job_id
  ON soniq_logs (job_id);
CREATE INDEX IF NOT EXISTS idx_soniq_logs_level
  ON soniq_logs (level);
CREATE INDEX IF NOT EXISTS idx_soniq_logs_request_id
  ON soniq_logs (request_id);
