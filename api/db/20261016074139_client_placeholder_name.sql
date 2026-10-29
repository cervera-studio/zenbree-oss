-- A project is a client (#307), and the interface calls it one: the placeholder name a new client
-- gets says «client» too. Only names still flagged as the placeholder change; a name the user
-- typed is theirs, whatever it spells.
UPDATE projects
SET name = CASE name
    WHEN 'Il mio progetto' THEN 'Il mio cliente'
    WHEN 'My project' THEN 'My client'
    WHEN 'Mi proyecto' THEN 'Mi cliente'
    WHEN 'Mon projet' THEN 'Mon client'
END
WHERE name_is_placeholder AND name IN ('Il mio progetto', 'My project', 'Mi proyecto', 'Mon projet');
