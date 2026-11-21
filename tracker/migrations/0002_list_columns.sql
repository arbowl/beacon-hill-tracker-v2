-- Chip tooltips need each requirement's detail without a join; keyset sorts need a non-null due key.
ALTER TABLE current_tenures
  ADD COLUMN hearing_notice_detail text,
  ADD COLUMN summary_posted_detail text,
  ADD COLUMN votes_posted_detail text,
  ADD COLUMN report_out_detail text,
  ADD COLUMN tenure_observed_detail text,
  ADD COLUMN due_key date GENERATED ALWAYS AS (coalesce(next_due, '9999-12-31'::date)) STORED;

DROP INDEX ct_list;
DROP INDEX ct_committee;
CREATE INDEX ct_severity ON current_tenures (session, severity, due_key, bill_num, bill_id, tenure_id);
CREATE INDEX ct_committee_severity
  ON current_tenures (session, committee_id, severity, due_key, bill_num, bill_id, tenure_id);
CREATE INDEX ct_bill_order ON current_tenures (session, bill_num, bill_id, tenure_id);
CREATE INDEX ct_committee_bill_order
  ON current_tenures (session, committee_id, bill_num, bill_id, tenure_id);
CREATE INDEX ct_due ON current_tenures (session, due_key, bill_num, bill_id, tenure_id);
CREATE INDEX ct_committee_due
  ON current_tenures (session, committee_id, due_key, bill_num, bill_id, tenure_id);
