-- Something a watcher may be told about. status_changed events point at the label_changes row
-- they report; deadline events name the obligation and the due date they report. withdrawn_at is
-- set when the run the event came from is rolled back; withdrawn events are not sent.
CREATE TABLE alert_events (
  id bigserial PRIMARY KEY,
  kind alert_kind NOT NULL,
  session int NOT NULL,
  tenure_id text NOT NULL,
  bill_id text NOT NULL,
  committee_id text NOT NULL,
  run_id uuid NOT NULL,
  label_change_id bigint REFERENCES label_changes,
  requirement requirement,
  obligation_id text,
  due date,
  created_at timestamptz NOT NULL,
  withdrawn_at timestamptz,
  CHECK ((kind = 'status_changed') = (label_change_id IS NOT NULL)),
  CHECK (kind = 'status_changed'
         OR (requirement IS NOT NULL AND obligation_id IS NOT NULL AND due IS NOT NULL)));
CREATE UNIQUE INDEX alert_events_change ON alert_events (label_change_id)
  WHERE kind = 'status_changed' AND withdrawn_at IS NULL;
CREATE UNIQUE INDEX alert_events_deadline ON alert_events (kind, obligation_id, due)
  WHERE kind <> 'status_changed' AND withdrawn_at IS NULL;
CREATE INDEX alert_events_recent ON alert_events (created_at) WHERE withdrawn_at IS NULL;

CREATE TABLE alert_deliveries (
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  alert_event_id bigint NOT NULL REFERENCES alert_events ON DELETE CASCADE,
  sent_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, alert_event_id));
CREATE INDEX alert_deliveries_event ON alert_deliveries (alert_event_id);

-- Site-wide mailings already sent, so a rerun of the same week or announcement sends nothing.
CREATE TABLE email_sends (
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  kind email_kind NOT NULL,
  key text NOT NULL,
  sent_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, kind, key),
  CHECK (kind IN ('weekly_summary', 'announcements')));
