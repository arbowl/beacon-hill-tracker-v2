-- Every distinct text of each client release, as sent; never pruned or deleted.
-- The row with the latest current_since per version is the one shown.
CREATE TABLE changelog_releases (
  version text NOT NULL,
  content_hash text NOT NULL,
  major int NOT NULL,
  minor int NOT NULL,
  patch int NOT NULL,
  released_on date NOT NULL,
  sections jsonb NOT NULL,
  code_version text NOT NULL,
  api_key_id bigint NOT NULL REFERENCES api_keys,
  received_at timestamptz NOT NULL,
  current_since timestamptz NOT NULL,
  PRIMARY KEY (version, content_hash));
CREATE INDEX changelog_releases_current ON changelog_releases (version, current_since DESC);
