-- Rate-limit ledger for the contact form. Messages themselves are emailed, never stored.
CREATE TABLE contact_submissions (
  id bigserial PRIMARY KEY,
  ip inet NOT NULL,
  submitted_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX contact_submissions_ip ON contact_submissions (ip, submitted_at);
