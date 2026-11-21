CREATE TYPE insight_kind AS ENUM ('deadline_count', 'bills_affected', 'weekly_change', 'due_soon');

-- Read model: regenerated at every publish and rollback. position orders display only.
CREATE TABLE insights (
  session int NOT NULL,
  position smallint NOT NULL,
  kind insight_kind NOT NULL,
  body text NOT NULL,
  href text NOT NULL,
  link_text text NOT NULL,
  run_id uuid NOT NULL,
  PRIMARY KEY (session, position),
  UNIQUE (session, kind));
