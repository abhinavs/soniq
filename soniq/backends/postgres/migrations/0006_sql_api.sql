-- SQL API for producers in any language, plus two data fixes.
--
-- soniq_enqueue() lets Go, Node, Ruby or plain psql enqueue a job with the
-- same checks, dedup and worker wake-up that Python's enqueue() has.
-- soniq_get_job() reads a job's status and result back, dead-lettered
-- jobs included. Both run as the owner (SECURITY DEFINER) and are
-- revoked from PUBLIC, so a service needs only:
--   GRANT EXECUTE ON FUNCTION soniq_enqueue, soniq_get_job TO my_service;
-- See docs/reference/storage-format.md.
--
-- Old workers ignore all of this, so 0.1.0 and 0.2.0 workers can share a
-- database after it runs. Needs a UTF8 database (the default).

-- The ALTER TABLE at the end needs a brief exclusive lock on soniq_jobs.
-- Give up rather than queue every enqueue and claim behind a long
-- transaction; rerunning `soniq setup` later is safe.
SET LOCAL lock_timeout = '5s';

-- One JSON string as text, escaped the way Python's json.dumps(ensure_ascii=True) does.
CREATE OR REPLACE FUNCTION soniq_json_string(s text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE
    -- Printable ASCII only: Postgres and Python escape it identically (only " and \).
    WHEN s ~ '^[ -~]*$' THEN to_json(s)::text
    ELSE '"' || (
      SELECT string_agg(
        CASE
          WHEN c = '"'  THEN '\"'
          WHEN c = '\'  THEN '\\'
          WHEN cp = 8   THEN '\b'
          WHEN cp = 9   THEN '\t'
          WHEN cp = 10  THEN '\n'
          WHEN cp = 12  THEN '\f'
          WHEN cp = 13  THEN '\r'
          WHEN cp < 32 OR cp = 127 OR (cp > 127 AND cp <= 65535)
            THEN '\u' || lpad(to_hex(cp), 4, '0')
          WHEN cp > 65535
            THEN '\u' || to_hex(55296 + ((cp - 65536) >> 10))
              || '\u' || to_hex(56320 + ((cp - 65536) & 1023))
          ELSE c
        END, '' ORDER BY i)
      FROM regexp_split_to_table(s, '') WITH ORDINALITY AS t(c, i),
           LATERAL ascii(c) AS cp
    ) || '"'
  END
$$;

-- Canonical text of a jsonb value: json.dumps(v, sort_keys=True, separators=(",", ":")).
-- COLLATE "C" sorts by UTF-8 bytes, which is code point order, which is Python's str order.
CREATE OR REPLACE FUNCTION soniq_args_canonical(v jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE jsonb_typeof(v)
    WHEN 'object' THEN '{' || coalesce((
        SELECT string_agg(soniq_json_string(k) || ':' || soniq_args_canonical(e),
                          ',' ORDER BY k COLLATE "C")
        FROM jsonb_each(v) AS x(k, e)), '') || '}'
    WHEN 'array' THEN '[' || coalesce((
        SELECT string_agg(soniq_args_canonical(e), ',' ORDER BY i)
        FROM jsonb_array_elements(v) WITH ORDINALITY AS x(e, i)), '') || ']'
    WHEN 'string' THEN soniq_json_string(v #>> '{}')
    ELSE v::text  -- number, boolean, null
  END
$$;

CREATE OR REPLACE FUNCTION soniq_args_hash(args jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT encode(sha256(convert_to(soniq_args_canonical(args), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION soniq_enqueue(
  job_name     text,
  args         jsonb       DEFAULT '{}',
  queue        text        DEFAULT 'default',
  priority     int         DEFAULT 100,
  run_at       timestamptz DEFAULT NULL,
  max_attempts int         DEFAULT 4,      -- Python's default: retries=3, plus one
  unique_job   boolean     DEFAULT false,
  dedup_key    text        DEFAULT NULL,
  producer_id  text        DEFAULT NULL,
  job_id       uuid        DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER AS $$
#variable_conflict use_variable
DECLARE
  -- Read the ALTER DATABASE value, not current_setting(): a caller could
  -- SET the pattern in its own session to get past the check.
  v_pattern text := coalesce(
    (SELECT nullif(substr(c, length('soniq.task_name_pattern=') + 1), '')
     FROM pg_db_role_setting s, unnest(s.setconfig) AS c
     WHERE s.setrole = 0
       AND s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
       AND c LIKE 'soniq.task\_name\_pattern=%'),
    '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$');
  v_hash text;
  v_id uuid;
BEGIN
  IF job_name IS NULL OR job_name !~ v_pattern THEN
    RAISE EXCEPTION 'soniq: job name % does not match %', quote_nullable(job_name), v_pattern
      USING ERRCODE = '22023', HINT = 'SONIQ_INVALID_TASK_NAME';
  END IF;
  IF args IS NULL OR jsonb_typeof(args) <> 'object' THEN
    RAISE EXCEPTION 'soniq: args must be a JSON object' USING ERRCODE = '22023', HINT = 'SONIQ_TASK_ARGS_INVALID';
  END IF;
  IF max_attempts IS NULL OR max_attempts < 1 THEN
    RAISE EXCEPTION 'soniq: max_attempts must be >= 1' USING ERRCODE = '22023';
  END IF;

  v_hash := CASE WHEN unique_job THEN soniq_args_hash(args) END;
  producer_id := coalesce(producer_id, nullif(current_setting('application_name', true), ''), session_user::text);

  LOOP
    INSERT INTO soniq_jobs AS j
      (id, job_name, args, args_hash, max_attempts, priority, queue,
       unique_job, dedup_key, scheduled_at, producer_id)
    VALUES (coalesce(job_id, gen_random_uuid()), job_name, args, v_hash, max_attempts,
            coalesce(priority, 100), coalesce(queue, 'default'), coalesce(unique_job, false),
            dedup_key, run_at, producer_id)
    ON CONFLICT DO NOTHING          -- covers both the dedup_key and the (job_name, args_hash) index
    RETURNING j.id INTO v_id;

    IF v_id IS NULL THEN            -- a queued duplicate exists: return its id, like Python does
      SELECT j.id INTO v_id FROM soniq_jobs j
      WHERE j.status = 'queued'
        AND ((dedup_key IS NOT NULL AND j.dedup_key = dedup_key)
          OR (unique_job AND j.unique_job AND j.job_name = job_name AND j.args_hash = v_hash))
      LIMIT 1;
      -- Also catches a primary-key clash on a caller-supplied job_id.
      IF v_id IS NULL AND job_id IS NOT NULL AND EXISTS (SELECT 1 FROM soniq_jobs WHERE id = job_id) THEN
        RAISE EXCEPTION 'soniq: job id % already exists', job_id USING ERRCODE = '23505';
      END IF;
    END IF;

    EXIT WHEN v_id IS NOT NULL;     -- else the duplicate was claimed meanwhile; insert again
  END LOOP;

  -- Delivered at COMMIT, so a rolled-back enqueue wakes nobody.
  PERFORM pg_notify('soniq_new_job', coalesce(queue, 'default'));
  RETURN v_id;
END
$$;

-- Pin the function to the schema the tables live in, so SECURITY DEFINER
-- can't be hijacked through the caller's search_path.
DO $$
BEGIN
  -- The hash helpers call each other by name, so pin them too: a caller
  -- whose search_path lacks the Soniq schema can still use soniq_args_hash.
  EXECUTE format('ALTER FUNCTION soniq_json_string(text) SET search_path = pg_catalog, %I', current_schema());
  EXECUTE format('ALTER FUNCTION soniq_args_canonical(jsonb) SET search_path = pg_catalog, %I', current_schema());
  EXECUTE format('ALTER FUNCTION soniq_args_hash(jsonb) SET search_path = pg_catalog, %I', current_schema());
  EXECUTE format(
    'ALTER FUNCTION soniq_enqueue(text, jsonb, text, int, timestamptz, int, boolean, text, text, uuid) SET search_path = pg_catalog, %I, pg_temp',
    current_schema());
END
$$;

-- Functions are executable by PUBLIC by default. Producers get it explicitly:
--   GRANT EXECUTE ON FUNCTION soniq_enqueue TO my_go_service;
REVOKE EXECUTE ON FUNCTION soniq_enqueue(text, jsonb, text, int, timestamptz, int, boolean, text, text, uuid) FROM PUBLIC;

-- A job's current state, shaped like Python's get_job(). A dead-lettered
-- job comes back with status 'dead_letter'. No row means the id is unknown
-- or the finished job has passed result_ttl and been deleted.
CREATE OR REPLACE FUNCTION soniq_get_job(job_id uuid)
RETURNS TABLE (
  id                 uuid,
  job_name           text,
  status             text,
  queue              text,
  attempts           int,
  max_attempts       int,
  result             jsonb,
  last_error         text,
  dead_letter_reason text,
  scheduled_at       timestamptz,
  created_at         timestamptz,
  updated_at         timestamptz
)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT id, job_name, status, queue, attempts, max_attempts, result,
         last_error, dead_letter_reason, scheduled_at, created_at, updated_at
  FROM (
    SELECT 0 AS rank, j.id, j.job_name, j.status, j.queue, j.attempts,
           j.max_attempts, j.result, j.last_error, NULL::text AS dead_letter_reason,
           j.scheduled_at, j.created_at, j.updated_at
    FROM soniq_jobs j WHERE j.id = soniq_get_job.job_id
    UNION ALL
    SELECT 1, d.id, d.job_name, 'dead_letter', d.queue, d.attempts,
           d.max_attempts, NULL::jsonb, d.last_error, d.dead_letter_reason,
           NULL::timestamptz, d.original_created_at, d.moved_to_dead_letter_at
    FROM soniq_dead_letter_jobs d WHERE d.id = soniq_get_job.job_id
  ) found
  ORDER BY rank
  LIMIT 1
$$;

DO $$
BEGIN
  EXECUTE format(
    'ALTER FUNCTION soniq_get_job(uuid) SET search_path = pg_catalog, %I, pg_temp',
    current_schema());
END
$$;

REVOKE EXECUTE ON FUNCTION soniq_get_job(uuid) FROM PUBLIC;

-- Soniq 0.1.0 stored some dead-letter tags encoded twice, as a JSON string
-- holding an object. Store them as the object.
UPDATE soniq_dead_letter_jobs
SET tags = (tags #>> '{}')::jsonb
WHERE jsonb_typeof(tags) = 'string' AND tags #>> '{}' LIKE '{%';

-- Python enqueues with max_attempts = retries + 1 = 4 by default. Match it
-- for raw INSERTs that leave the column out (it was 3).
-- Last, so the exclusive lock is held only until COMMIT.
ALTER TABLE soniq_jobs ALTER COLUMN max_attempts SET DEFAULT 4;
