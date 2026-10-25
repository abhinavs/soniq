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

-- Every built-in function and operator below is called as pg_catalog.x()
-- or OPERATOR(pg_catalog.x), and every literal has a type. On Postgres 14
-- and older any role can create objects in public, and an unqualified call
-- can pick a planted public.convert_to(text, text) or #>>(jsonb, jsonb)
-- over the built-in because its argument types fit better. The search_path
-- pin doesn't stop that, and in a SECURITY DEFINER function the planted
-- code would run as the owner. Types need no prefix: they can't be
-- overloaded, pg_catalog comes first and pg_temp last. There is no percent
-- sign anywhere, because Alembic's offline mode doubles it.

-- One JSON string as text, escaped the way Python's json.dumps(ensure_ascii=True) does.
CREATE OR REPLACE FUNCTION soniq_json_string(s text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE
    -- Printable ASCII only: Postgres and Python escape it identically (only " and \).
    WHEN s OPERATOR(pg_catalog.~) '^[ -~]*$'::text THEN pg_catalog.to_json(s)::text
    ELSE '"'::text OPERATOR(pg_catalog.||) (
      SELECT pg_catalog.string_agg(
        CASE
          WHEN c OPERATOR(pg_catalog.=) '"'::text THEN '\"'::text
          WHEN c OPERATOR(pg_catalog.=) '\'::text THEN '\\'::text
          WHEN cp OPERATOR(pg_catalog.=) 8  THEN '\b'::text
          WHEN cp OPERATOR(pg_catalog.=) 9  THEN '\t'::text
          WHEN cp OPERATOR(pg_catalog.=) 10 THEN '\n'::text
          WHEN cp OPERATOR(pg_catalog.=) 12 THEN '\f'::text
          WHEN cp OPERATOR(pg_catalog.=) 13 THEN '\r'::text
          WHEN cp OPERATOR(pg_catalog.<) 32 OR cp OPERATOR(pg_catalog.=) 127
               OR (cp OPERATOR(pg_catalog.>) 127 AND cp OPERATOR(pg_catalog.<=) 65535)
            THEN '\u'::text OPERATOR(pg_catalog.||) pg_catalog.lpad(pg_catalog.to_hex(cp), 4, '0'::text)
          WHEN cp OPERATOR(pg_catalog.>) 65535
            THEN '\u'::text
              OPERATOR(pg_catalog.||) pg_catalog.to_hex(55296 OPERATOR(pg_catalog.+)
                ((cp OPERATOR(pg_catalog.-) 65536) OPERATOR(pg_catalog.>>) 10))
              OPERATOR(pg_catalog.||) '\u'::text
              OPERATOR(pg_catalog.||) pg_catalog.to_hex(56320 OPERATOR(pg_catalog.+)
                ((cp OPERATOR(pg_catalog.-) 65536) OPERATOR(pg_catalog.&) 1023))
          ELSE c
        END, ''::text ORDER BY i)
      FROM pg_catalog.regexp_split_to_table(s, ''::text) WITH ORDINALITY AS t(c, i),
           LATERAL pg_catalog.ascii(c) AS cp
    ) OPERATOR(pg_catalog.||) '"'::text
  END
$$;

-- Canonical text of a jsonb value: json.dumps(v, sort_keys=True, separators=(",", ":")).
-- COLLATE "C" sorts by UTF-8 bytes, which is code point order, which is Python's str order.
-- A number prints as Postgres stores it (1e16 as 10000000000000000), and
-- Python's compute_args_hash prints floats the same way.
CREATE OR REPLACE FUNCTION soniq_args_canonical(v jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE
    WHEN t OPERATOR(pg_catalog.=) 'object'::text THEN
      '{'::text OPERATOR(pg_catalog.||) coalesce((
        SELECT pg_catalog.string_agg(
                 soniq_json_string(k) OPERATOR(pg_catalog.||) ':'::text
                   OPERATOR(pg_catalog.||) soniq_args_canonical(e),
                 ','::text ORDER BY k COLLATE pg_catalog."C")
        FROM pg_catalog.jsonb_each(v) AS x(k, e)), ''::text) OPERATOR(pg_catalog.||) '}'::text
    WHEN t OPERATOR(pg_catalog.=) 'array'::text THEN
      '['::text OPERATOR(pg_catalog.||) coalesce((
        SELECT pg_catalog.string_agg(soniq_args_canonical(e), ','::text ORDER BY i)
        FROM pg_catalog.jsonb_array_elements(v) WITH ORDINALITY AS x(e, i)), ''::text)
        OPERATOR(pg_catalog.||) ']'::text
    WHEN t OPERATOR(pg_catalog.=) 'string'::text THEN
      soniq_json_string(v OPERATOR(pg_catalog.#>>) '{}'::text[])
    ELSE v::text  -- number, boolean, null
  END
  FROM pg_catalog.jsonb_typeof(v) AS t
$$;

CREATE OR REPLACE FUNCTION soniq_args_hash(args jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.convert_to(soniq_args_canonical(args), 'UTF8'::name)),
    'hex'::text)
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
    (SELECT pg_catalog.substr(c, 25)  -- after 'soniq.task_name_pattern='
     FROM pg_catalog.pg_db_role_setting s, pg_catalog.unnest(s.setconfig) AS c
     WHERE s.setrole OPERATOR(pg_catalog.=) 0::oid
       AND s.setdatabase OPERATOR(pg_catalog.=) (
         SELECT d.oid FROM pg_catalog.pg_database d
         WHERE d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database())
       AND pg_catalog.starts_with(c, 'soniq.task_name_pattern='::text)
       AND c OPERATOR(pg_catalog.<>) 'soniq.task_name_pattern='::text),
    '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'::text);
  v_hash text;
  v_id uuid;
BEGIN
  IF job_name IS NULL OR job_name OPERATOR(pg_catalog.!~) v_pattern THEN
    RAISE EXCEPTION USING
      MESSAGE = 'soniq: job name '::text OPERATOR(pg_catalog.||) pg_catalog.quote_nullable(job_name)
        OPERATOR(pg_catalog.||) ' does not match '::text OPERATOR(pg_catalog.||) v_pattern,
      ERRCODE = '22023', HINT = 'SONIQ_INVALID_TASK_NAME';
  END IF;
  IF args IS NULL OR pg_catalog.jsonb_typeof(args) OPERATOR(pg_catalog.<>) 'object'::text THEN
    RAISE EXCEPTION 'soniq: args must be a JSON object' USING ERRCODE = '22023', HINT = 'SONIQ_TASK_ARGS_INVALID';
  END IF;
  -- Python reads a bigger number as inf, or fails on one over 4300 digits.
  IF pg_catalog.jsonb_path_exists(args,
       'lax $.** ? (@.type() == "number" && @.abs() > 1.7976931348623157e308)'::jsonpath) THEN
    RAISE EXCEPTION 'soniq: args hold a number too big for a 64-bit float'
      USING ERRCODE = '22023', HINT = 'SONIQ_TASK_ARGS_INVALID';
  END IF;
  -- Python can't decode deep nesting, so the worker would dead-letter the
  -- job, and hashing a unique job runs out of stack past about a thousand.
  IF pg_catalog.jsonb_path_exists(args, 'strict $.**{500 to last}'::jsonpath) THEN
    RAISE EXCEPTION 'soniq: args nest 500 or more levels deep'
      USING ERRCODE = '22023', HINT = 'SONIQ_TASK_ARGS_INVALID';
  END IF;
  -- NULL is allowed: the worker then uses the handler's retries, as for a raw INSERT.
  IF max_attempts OPERATOR(pg_catalog.<) 1 THEN
    RAISE EXCEPTION 'soniq: max_attempts must be >= 1 or NULL'
      USING ERRCODE = '22023', HINT = 'SONIQ_INVALID_MAX_ATTEMPTS';
  END IF;

  v_hash := CASE WHEN unique_job THEN soniq_args_hash(args) END;
  producer_id := coalesce(
    producer_id,
    (SELECT a FROM pg_catalog.current_setting('application_name'::text, true) AS a
     WHERE a OPERATOR(pg_catalog.<>) ''::text),
    session_user::text);

  LOOP
    INSERT INTO soniq_jobs AS j
      (id, job_name, args, args_hash, max_attempts, priority, queue,
       unique_job, dedup_key, scheduled_at, producer_id)
    VALUES (coalesce(job_id, pg_catalog.gen_random_uuid()), job_name, args, v_hash, max_attempts,
            coalesce(priority, 100), coalesce(queue, 'default'::text), coalesce(unique_job, false),
            dedup_key, run_at, producer_id)
    ON CONFLICT DO NOTHING          -- covers both the dedup_key and the (job_name, args_hash) index
    RETURNING j.id INTO v_id;

    IF v_id IS NULL THEN            -- a queued duplicate exists: return its id, like Python does
      SELECT j.id INTO v_id FROM soniq_jobs j
      WHERE j.status OPERATOR(pg_catalog.=) 'queued'::text
        AND ((dedup_key IS NOT NULL AND j.dedup_key OPERATOR(pg_catalog.=) dedup_key)
          OR (unique_job AND j.unique_job
              AND j.job_name OPERATOR(pg_catalog.=) job_name
              AND j.args_hash OPERATOR(pg_catalog.=) v_hash))
      LIMIT 1;
      -- Also catches a primary-key clash on a caller-supplied job_id.
      IF v_id IS NULL AND job_id IS NOT NULL
         AND EXISTS (SELECT 1 FROM soniq_jobs x WHERE x.id OPERATOR(pg_catalog.=) job_id) THEN
        RAISE EXCEPTION USING
          MESSAGE = 'soniq: job id '::text OPERATOR(pg_catalog.||) job_id::text
            OPERATOR(pg_catalog.||) ' already exists'::text,
          ERRCODE = '23505';
      END IF;
    END IF;

    EXIT WHEN v_id IS NOT NULL;     -- else the duplicate was claimed meanwhile; insert again
  END LOOP;

  -- Delivered at COMMIT, so a rolled-back enqueue wakes nobody.
  PERFORM pg_catalog.pg_notify('soniq_new_job'::text, coalesce(queue, 'default'::text));
  RETURN v_id;
END
$$;

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
    FROM soniq_jobs j WHERE j.id OPERATOR(pg_catalog.=) soniq_get_job.job_id
    UNION ALL
    SELECT 1, d.id, d.job_name, 'dead_letter'::text, d.queue, d.attempts,
           d.max_attempts, NULL::jsonb, d.last_error, d.dead_letter_reason,
           NULL::timestamptz, d.original_created_at, d.moved_to_dead_letter_at
    FROM soniq_dead_letter_jobs d WHERE d.id OPERATOR(pg_catalog.=) soniq_get_job.job_id
  ) found
  ORDER BY rank
  LIMIT 1
$$;

-- Pin every function to the schema the tables live in, so SECURITY
-- DEFINER can't be hijacked through the caller's search_path. The hash
-- helpers call each other by name, so a caller whose search_path lacks the
-- Soniq schema can still use soniq_args_hash. pg_temp goes last: left out,
-- Postgres would search it first for types.
DO $$
DECLARE
  v_path text := 'pg_catalog, '::text
    OPERATOR(pg_catalog.||) pg_catalog.quote_ident(pg_catalog.current_schema()::text)
    OPERATOR(pg_catalog.||) ', pg_temp'::text;
  f text;
BEGIN
  FOREACH f IN ARRAY ARRAY[
    'soniq_json_string(text)',
    'soniq_args_canonical(jsonb)',
    'soniq_args_hash(jsonb)',
    'soniq_enqueue(text, jsonb, text, int, timestamptz, int, boolean, text, text, uuid)',
    'soniq_get_job(uuid)'
  ]::text[] LOOP
    EXECUTE 'ALTER FUNCTION '::text OPERATOR(pg_catalog.||) f
      OPERATOR(pg_catalog.||) ' SET search_path = '::text OPERATOR(pg_catalog.||) v_path;
  END LOOP;
END
$$;

-- Functions are executable by PUBLIC by default. Producers get it explicitly:
--   GRANT EXECUTE ON FUNCTION soniq_enqueue, soniq_get_job TO my_go_service;
REVOKE EXECUTE ON FUNCTION soniq_enqueue(text, jsonb, text, int, timestamptz, int, boolean, text, text, uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION soniq_get_job(uuid) FROM PUBLIC;

-- Soniq 0.1.0 stored some dead-letter tags encoded twice, as a JSON string
-- holding an object. Store them as the object.
UPDATE soniq_dead_letter_jobs
SET tags = (tags OPERATOR(pg_catalog.#>>) '{}'::text[])::jsonb
WHERE pg_catalog.jsonb_typeof(tags) OPERATOR(pg_catalog.=) 'string'::text
  AND pg_catalog.starts_with(tags OPERATOR(pg_catalog.#>>) '{}'::text[], '{'::text);

-- Python enqueues with max_attempts = retries + 1 = 4 by default. Match it
-- for raw INSERTs that leave the column out (it was 3).
-- Last, so the exclusive lock is held only until COMMIT.
ALTER TABLE soniq_jobs ALTER COLUMN max_attempts SET DEFAULT 4;
