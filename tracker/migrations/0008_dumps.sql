CREATE TYPE dump_format AS ENUM ('csv.gz', 'json.gz');

-- Downloadable copies of a session's live data. data_version digests the (committee, run) pairs
-- the file was built from, so the same data always maps to one row and the same bytes.
-- current_since moves when that data becomes live again, as after a rollback.
CREATE TABLE data_dumps (
  id bigserial PRIMARY KEY,
  session int NOT NULL,
  format dump_format NOT NULL,
  data_version text NOT NULL,
  as_of timestamptz NOT NULL,
  built_at timestamptz NOT NULL,
  current_since timestamptz NOT NULL,
  sha256 text NOT NULL,
  size bigint NOT NULL,
  bytes bytea NOT NULL,
  UNIQUE (session, format, data_version));
CREATE INDEX data_dumps_current ON data_dumps (session, format, current_since DESC);
