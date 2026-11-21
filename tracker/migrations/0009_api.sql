-- Requests per read key per clock minute, for the public API's rate limit.
CREATE TABLE api_rate_windows (
  key_id bigint NOT NULL REFERENCES api_keys ON DELETE CASCADE,
  window_start timestamptz NOT NULL,
  requests int NOT NULL,
  PRIMARY KEY (key_id, window_start));
