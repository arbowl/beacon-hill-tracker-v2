ALTER TYPE no_requirements_reason ADD VALUE 'record_incomplete';

-- Why the legislative record does not show how the bill came before the committee, a gap
-- reviewed by hand; set exactly when reason is record_incomplete.
ALTER TABLE no_requirement_tenures ADD COLUMN note text;
