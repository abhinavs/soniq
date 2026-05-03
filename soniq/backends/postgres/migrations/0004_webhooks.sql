-- Webhooks: endpoints + delivery tracking.
--
-- Promoted to the core slice. Empty when the deployment never registers
-- webhook endpoints.

CREATE TABLE IF NOT EXISTS soniq_webhook_endpoints (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  secret TEXT,
  events JSONB,
  active BOOLEAN DEFAULT true,
  max_retries INTEGER DEFAULT 3,
  timeout_seconds INTEGER DEFAULT 30,
  headers JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS soniq_webhook_deliveries (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES soniq_webhook_endpoints(id) ON DELETE CASCADE,
  event TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 3,
  next_retry_at TIMESTAMP WITH TIME ZONE,
  last_error TEXT,
  response_status INTEGER,
  response_body TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  delivered_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_soniq_webhook_deliveries_status
  ON soniq_webhook_deliveries (status);
CREATE INDEX IF NOT EXISTS idx_soniq_webhook_deliveries_next_retry
  ON soniq_webhook_deliveries (next_retry_at);
CREATE INDEX IF NOT EXISTS idx_soniq_webhook_deliveries_endpoint
  ON soniq_webhook_deliveries (endpoint_id);
