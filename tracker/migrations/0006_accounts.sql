CREATE TYPE watch_kind AS ENUM ('bill', 'committee');
CREATE TYPE alert_kind AS ENUM ('status_changed', 'deadline_approaching', 'deadline_missed');
CREATE TYPE email_kind AS ENUM ('watch_status_changed', 'watch_deadline_approaching',
  'watch_deadline_missed', 'weekly_summary', 'announcements');

-- One row per kind for every account, written when the account is created.
CREATE TABLE user_email_prefs (
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  kind email_kind NOT NULL,
  enabled boolean NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind));

-- Emailed sign-in and sign-up links; only the token's sha256 is stored.
-- signup_prefs is NULL for a sign-in link, and the kinds ticked (possibly none) for a sign-up link.
CREATE TABLE login_tokens (
  token_hash bytea PRIMARY KEY,
  email citext NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  request_ip inet NOT NULL,
  signup_prefs email_kind[],
  next_path text);
CREATE INDEX login_tokens_expires ON login_tokens (expires_at);

-- Throttle ledger: every link request that was answered, including ones that sent no link.
CREATE TABLE login_requests (
  id bigserial PRIMARY KEY,
  email citext NOT NULL,
  ip inet NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX login_requests_email ON login_requests (email, requested_at);
CREATE INDEX login_requests_ip ON login_requests (ip, requested_at);

-- Server-side sessions; the cookie holds the id, the table its sha256.
CREATE TABLE web_sessions (
  id_hash bytea PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_seen_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX web_sessions_user ON web_sessions (user_id);

CREATE TABLE watches (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  kind watch_kind NOT NULL,
  session int NOT NULL,
  target_id text NOT NULL,
  alerts alert_kind[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, kind, session, target_id));
CREATE INDEX watches_target ON watches (kind, session, target_id);
