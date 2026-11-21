CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE chamber AS ENUM ('House', 'Senate', 'Joint');
CREATE TYPE requirement AS ENUM (
  'hearing_notice', 'summary_posted', 'votes_posted', 'report_out_deadline', 'tenure_observed');
CREATE TYPE req_status AS ENUM ('Met', 'Unmet', 'Provisional');
CREATE TYPE prov_reason AS ENUM ('not_yet_due', 'ambiguous_timing', 'missing_data', 'awaiting_review');
CREATE TYPE label AS ENUM ('Compliant', 'Provisional', 'Non-Compliant');
CREATE TYPE run_status AS ENUM ('open', 'published', 'abandoned', 'pruned');
CREATE TYPE user_role AS ENUM ('user', 'webmaster');
CREATE TYPE key_scope AS ENUM ('read', 'ingest');

CREATE TABLE users (
  id bigserial PRIMARY KEY,
  email citext UNIQUE NOT NULL,
  role user_role NOT NULL DEFAULT 'user',
  created_at timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz,
  disabled_at timestamptz);

CREATE TABLE api_keys (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users,
  scope key_scope NOT NULL,
  name text NOT NULL,
  prefix text NOT NULL,
  key_hash bytea UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  revoked_at timestamptz);

-- One row per push from the client. run_id is the client's push_id.
CREATE TABLE runs (
  run_id uuid PRIMARY KEY,
  session int NOT NULL,
  code_version text NOT NULL,
  created_at timestamptz NOT NULL,
  scope text[] NOT NULL,
  status run_status NOT NULL DEFAULT 'open',
  opened_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  api_key_id bigint NOT NULL REFERENCES api_keys);
CREATE INDEX runs_open ON runs (opened_at) WHERE status = 'open';

-- Upload ledger, with each committee's evaluation provenance and dimension values.
CREATE TABLE run_committees (
  run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
  committee_id text NOT NULL,
  evaluation_run_id uuid NOT NULL,
  ruleset_id text NOT NULL,
  ruleset_version text NOT NULL,
  as_of timestamptz NOT NULL,
  name text NOT NULL,
  chamber chamber NOT NULL,
  url text NOT NULL,
  bundle_sha256 text NOT NULL,
  tenure_count int NOT NULL,
  uploaded_at timestamptz NOT NULL,
  pruned_at timestamptz,
  PRIMARY KEY (run_id, committee_id));

CREATE TABLE run_bills (
  run_id uuid NOT NULL,
  committee_id text NOT NULL,
  bill_id text NOT NULL,
  title text,
  url text NOT NULL,
  PRIMARY KEY (run_id, committee_id, bill_id),
  FOREIGN KEY (run_id, committee_id) REFERENCES run_committees ON DELETE CASCADE);

CREATE TABLE committee_outputs (
  run_id uuid NOT NULL,
  committee_id text NOT NULL,
  bill_id text NOT NULL,
  title text,
  originated_on date NOT NULL,
  source_bills text[] NOT NULL,
  PRIMARY KEY (run_id, committee_id, bill_id),
  FOREIGN KEY (run_id, committee_id) REFERENCES run_committees ON DELETE CASCADE);

CREATE TABLE tenures (
  run_id uuid NOT NULL,
  tenure_id text NOT NULL,
  session int NOT NULL,
  bill_id text NOT NULL,
  committee_id text NOT NULL,
  referred_on date,                          -- NULL for a tenure whose referral was not observed
  label label NOT NULL,
  input_fingerprint text NOT NULL,
  PRIMARY KEY (run_id, tenure_id),
  FOREIGN KEY (run_id, committee_id) REFERENCES run_committees ON DELETE CASCADE);
CREATE INDEX tenures_committee ON tenures (run_id, committee_id);

CREATE TABLE requirements (
  run_id uuid NOT NULL,
  tenure_id text NOT NULL,
  requirement requirement NOT NULL,
  obligation_id text NOT NULL,
  status req_status NOT NULL,
  reason prov_reason,
  detail text NOT NULL,
  due date,
  resolves_on date,
  completed_on date,
  PRIMARY KEY (run_id, tenure_id, requirement),
  FOREIGN KEY (run_id, tenure_id) REFERENCES tenures ON DELETE CASCADE,
  CHECK ((status = 'Provisional') = (reason IS NOT NULL)));

CREATE TABLE requirement_audit (
  run_id uuid NOT NULL,
  tenure_id text NOT NULL,
  requirement requirement NOT NULL,
  derivation jsonb NOT NULL,
  evidence jsonb NOT NULL,
  clauses jsonb NOT NULL,                    -- [{ref, text_hash}] as cited
  PRIMARY KEY (run_id, tenure_id, requirement),
  FOREIGN KEY (run_id, tenure_id, requirement) REFERENCES requirements ON DELETE CASCADE);

-- Content-addressed rule text; kept forever so any past citation can be shown.
CREATE TABLE clauses (
  text_hash text PRIMARY KEY,
  source text NOT NULL,
  rule text NOT NULL,
  paragraph int,
  text text NOT NULL,
  first_run_id uuid NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX clauses_ref ON clauses (source, rule, paragraph, first_seen_at DESC);

CREATE TABLE committees (
  session int NOT NULL,
  committee_id text NOT NULL,
  name text NOT NULL,
  chamber chamber NOT NULL,
  url text NOT NULL,
  last_run_id uuid NOT NULL,
  PRIMARY KEY (session, committee_id));

CREATE TABLE committee_current (
  session int NOT NULL,
  committee_id text NOT NULL,
  run_id uuid NOT NULL REFERENCES runs,
  as_of timestamptz NOT NULL,
  PRIMARY KEY (session, committee_id));

CREATE TABLE committee_publications (
  session int NOT NULL,
  committee_id text NOT NULL,
  run_id uuid NOT NULL REFERENCES runs,
  published_at timestamptz NOT NULL,
  rolled_back_at timestamptz,
  PRIMARY KEY (session, committee_id, run_id));
CREATE INDEX committee_publications_latest
  ON committee_publications (session, committee_id, published_at DESC);

-- Read model: one row per live tenure. A NULL requirement column means no obligation arose.
CREATE TABLE current_tenures (
  session int NOT NULL,
  tenure_id text NOT NULL,
  run_id uuid NOT NULL,
  bill_id text NOT NULL,
  bill_num int NOT NULL,
  title text,
  bill_url text NOT NULL,
  committee_id text NOT NULL,
  committee_name text NOT NULL,
  referred_on date,
  label label NOT NULL,
  hearing_notice req_status,
  summary_posted req_status,
  votes_posted req_status,
  report_out req_status,
  referral_observed boolean NOT NULL,
  report_due date,
  next_due date,                             -- earliest due date of a Provisional requirement
  severity smallint NOT NULL,                -- 0 Non-Compliant, 1 Provisional, 2 Compliant
  search tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', bill_id), 'A') ||
    setweight(to_tsvector('english', coalesce(title, '')), 'B')) STORED,
  PRIMARY KEY (session, tenure_id));
CREATE INDEX ct_list ON current_tenures (session, severity, next_due, bill_num, tenure_id);
CREATE INDEX ct_committee ON current_tenures (session, committee_id, severity, bill_num, tenure_id);
CREATE INDEX ct_bill ON current_tenures (session, bill_id);
CREATE INDEX ct_search ON current_tenures USING gin (search);
CREATE INDEX ct_bill_trgm ON current_tenures USING gin (bill_id gin_trgm_ops);

-- Read model: one row per committee plus a session row with committee_id '*'. Counts are tenures
-- unless named bills_*.
CREATE TABLE committee_stats (
  session int NOT NULL,
  committee_id text NOT NULL,
  as_of timestamptz NOT NULL,
  total int NOT NULL,
  compliant int NOT NULL,
  provisional int NOT NULL,
  non_compliant int NOT NULL,
  bills_total int NOT NULL,
  bills_with_violation int NOT NULL,
  hearing_notice_total int NOT NULL,        -- tenures with a hearing-notice obligation
  hearing_notice_unmet int NOT NULL,
  summary_unmet int NOT NULL,
  votes_unmet int NOT NULL,
  report_out_unmet int NOT NULL,
  due_next_14_days int NOT NULL,
  funnel int[] NOT NULL,                     -- [total, then cumulative stages passed]
  compliance_rate numeric(5, 2),
  decided_rate numeric(5, 2),
  PRIMARY KEY (session, committee_id));

-- History, never pruned.
CREATE TABLE snapshots (
  session int NOT NULL,
  committee_id text NOT NULL,
  taken_at timestamptz NOT NULL,
  run_id uuid NOT NULL,
  total int NOT NULL,
  compliant int NOT NULL,
  provisional int NOT NULL,
  non_compliant int NOT NULL,
  PRIMARY KEY (session, committee_id, taken_at));

-- History, never pruned. to_label NULL: the tenure is no longer in the committee's data.
CREATE TABLE label_changes (
  id bigserial PRIMARY KEY,
  session int NOT NULL,
  tenure_id text NOT NULL,
  committee_id text NOT NULL,
  run_id uuid NOT NULL,
  changed_at timestamptz NOT NULL,
  from_label label,
  to_label label,
  changed_requirements jsonb NOT NULL,       -- [{requirement, from, to}]
  CHECK (from_label IS NOT NULL OR to_label IS NOT NULL));
CREATE INDEX label_changes_tenure ON label_changes (session, tenure_id, changed_at);
