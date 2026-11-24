ALTER TYPE no_requirements_reason ADD VALUE 'accompanied';

-- The bill (a study order or new draft) a tenure accompanied before the committee, which is
-- judged in its place; set exactly when reason is accompanied.
ALTER TABLE no_requirement_tenures ADD COLUMN carried_by text;
