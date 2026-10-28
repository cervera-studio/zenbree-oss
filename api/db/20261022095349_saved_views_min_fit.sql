-- A saved view keeps its fit floor: the same rule as GET /v1/lists/{id}/rows?min_fit=N. Null (old views) means no fit filter.
ALTER TABLE saved_views ADD COLUMN min_fit smallint CHECK (min_fit BETWEEN 0 AND 100);
