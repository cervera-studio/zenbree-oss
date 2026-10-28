-- A client created with the placeholder name takes the company's on the first profile save (#794). The flag, not
-- the text, says so: a name the user typed that spells the placeholder (in any language) is theirs.
ALTER TABLE projects ADD COLUMN name_is_placeholder BOOLEAN NOT NULL DEFAULT false;

-- Rows made before the flag cannot tell the two apart; the placeholder text is the best evidence.
UPDATE projects SET name_is_placeholder = true WHERE name = 'Il mio progetto';
