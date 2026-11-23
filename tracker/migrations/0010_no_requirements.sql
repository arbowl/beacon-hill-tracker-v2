CREATE TYPE no_requirements_reason AS ENUM ('none_apply', 'none_yet');

-- Tenures that owed nothing as of their run. Kept out of tenures, current_tenures, and every
-- count, so they carry no label and move no rate. awaiting lists requirements that will arise
-- when the committee acts (non-empty exactly when reason is none_yet).
CREATE TABLE no_requirement_tenures (
  run_id uuid NOT NULL,
  committee_id text NOT NULL,
  tenure_id text NOT NULL,
  bill_id text NOT NULL,
  referred_on date NOT NULL,
  reason no_requirements_reason NOT NULL,
  awaiting text[] NOT NULL,
  PRIMARY KEY (run_id, committee_id, tenure_id),
  FOREIGN KEY (run_id, committee_id) REFERENCES run_committees ON DELETE CASCADE);
CREATE INDEX no_requirement_tenures_bill ON no_requirement_tenures (bill_id);
