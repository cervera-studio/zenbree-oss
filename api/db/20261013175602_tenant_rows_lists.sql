-- Every tenant table stores its own organization_id (2 of 4: lists, runs and lookup claims).
-- See the first file of this change for the reason, the trigger and the lock note.
--
-- lookup_claims copies the organization of its job; a claim of a job and a list row of two organizations stops the
-- migration like the signals above.
--
-- A row copies the organization of its list (list_rows, list_columns, list_plans, saved_searches, watchlists), of its
-- row (row_charges), or of its watchlist or saved search (the rest). A link that may be NULL and points into another
-- organization than the row is cut (NULL). signals and watchlist_accounts name a watchlist and a row; a pair from two
-- organizations links them across tenants and has no organization to be in. No path makes one, and this migration
-- deletes no row: it stops and names the count, so a person decides.

ALTER TABLE list_rows ADD COLUMN organization_id uuid;
ALTER TABLE list_rows DISABLE TRIGGER home_touch;
UPDATE list_rows SET organization_id = p.organization_id FROM lists p WHERE p.id = list_rows.list_id;
ALTER TABLE list_rows ENABLE TRIGGER home_touch;
ALTER TABLE list_columns ADD COLUMN organization_id uuid;
UPDATE list_columns SET organization_id = p.organization_id FROM lists p WHERE p.id = list_columns.list_id;
ALTER TABLE list_plans ADD COLUMN organization_id uuid;
UPDATE list_plans SET organization_id = p.organization_id FROM lists p WHERE p.id = list_plans.list_id;
ALTER TABLE watchlists ADD COLUMN organization_id uuid;
UPDATE watchlists SET organization_id = p.organization_id FROM lists p WHERE p.id = watchlists.list_id;
ALTER TABLE saved_searches ADD COLUMN organization_id uuid;
UPDATE saved_searches SET organization_id = p.organization_id FROM lists p WHERE p.id = saved_searches.list_id;
ALTER TABLE row_charges ADD COLUMN organization_id uuid;
UPDATE row_charges SET organization_id = p.organization_id FROM list_rows p WHERE p.id = row_charges.row_id;
ALTER TABLE signals ADD COLUMN organization_id uuid;
ALTER TABLE watchlist_accounts ADD COLUMN organization_id uuid;
DO $$
DECLARE
    crossing_signals int;
    crossing_accounts int;
BEGIN
    SELECT count(*) INTO crossing_signals FROM signals s JOIN watchlists w ON w.id = s.watchlist_id
        JOIN list_rows r ON r.id = s.row_id WHERE w.organization_id <> r.organization_id;
    SELECT count(*) INTO crossing_accounts FROM watchlist_accounts s JOIN watchlists w ON w.id = s.watchlist_id
        JOIN list_rows r ON r.id = s.row_id WHERE w.organization_id <> r.organization_id;
    IF crossing_signals > 0 OR crossing_accounts > 0 THEN
        RAISE EXCEPTION 'signals.organization_id: % row(s), watchlist_accounts.organization_id: % row(s) link a watchlist and a list row of two organizations',
            crossing_signals, crossing_accounts;
    END IF;
END $$;
UPDATE signals s SET organization_id = w.organization_id FROM watchlists w WHERE w.id = s.watchlist_id;
UPDATE watchlist_accounts s SET organization_id = w.organization_id FROM watchlists w WHERE w.id = s.watchlist_id;
ALTER TABLE lookup_claims ADD COLUMN organization_id uuid;
DO $$
DECLARE
    crossing_claims int;
BEGIN
    SELECT count(*) INTO crossing_claims FROM lookup_claims c JOIN jobs j ON j.id = c.job_id
        JOIN list_rows r ON r.id = c.row_id WHERE j.organization_id <> r.organization_id;
    IF crossing_claims > 0 THEN
        RAISE EXCEPTION 'lookup_claims.organization_id: % row(s) link a job and a list row of two organizations',
            crossing_claims;
    END IF;
END $$;
UPDATE lookup_claims c SET organization_id = j.organization_id FROM jobs j WHERE j.id = c.job_id;
ALTER TABLE watchlist_runs ADD COLUMN organization_id uuid;
UPDATE watchlist_runs SET organization_id = p.organization_id FROM watchlists p WHERE p.id = watchlist_runs.watchlist_id;
ALTER TABLE saved_search_runs ADD COLUMN organization_id uuid;
UPDATE saved_search_runs SET organization_id = p.organization_id FROM saved_searches p WHERE p.id = saved_search_runs.saved_search_id;

-- Links to a parent of another organization.
UPDATE saved_searches SET campaign_id = NULL FROM outreach_campaigns p
    WHERE p.id = saved_searches.campaign_id AND p.organization_id <> saved_searches.organization_id;
UPDATE list_plans SET approved_job_id = NULL FROM jobs p
    WHERE p.id = list_plans.approved_job_id AND p.organization_id <> list_plans.organization_id;
UPDATE row_charges SET job_id = NULL FROM jobs p
    WHERE p.id = row_charges.job_id AND p.organization_id <> row_charges.organization_id;
UPDATE signals SET run_id = NULL FROM watchlist_runs p
    WHERE p.id = signals.run_id AND p.organization_id <> signals.organization_id;
UPDATE watchlist_runs SET reservation_id = NULL FROM credit_transactions p
    WHERE p.id = watchlist_runs.reservation_id AND p.organization_id <> watchlist_runs.organization_id;
UPDATE saved_search_runs SET reservation_id = NULL FROM credit_transactions p
    WHERE p.id = saved_search_runs.reservation_id AND p.organization_id <> saved_search_runs.organization_id;

ALTER TABLE list_rows ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_list_rows_organization_id ON list_rows(organization_id);
CREATE TRIGGER list_rows_fill_organization_id BEFORE INSERT OR UPDATE OF list_id ON list_rows
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
ALTER TABLE list_columns ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_list_columns_organization_id ON list_columns(organization_id);
CREATE TRIGGER list_columns_fill_organization_id BEFORE INSERT OR UPDATE OF list_id ON list_columns
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
ALTER TABLE list_plans ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_list_plans_organization_id ON list_plans(organization_id);
CREATE TRIGGER list_plans_fill_organization_id BEFORE INSERT OR UPDATE OF list_id ON list_plans
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
ALTER TABLE watchlists ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_watchlists_organization_id ON watchlists(organization_id);
CREATE TRIGGER watchlists_fill_organization_id BEFORE INSERT OR UPDATE OF list_id ON watchlists
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
ALTER TABLE saved_searches ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_saved_searches_organization_id ON saved_searches(organization_id);
CREATE TRIGGER saved_searches_fill_organization_id BEFORE INSERT OR UPDATE OF list_id ON saved_searches
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
ALTER TABLE row_charges ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_row_charges_organization_id ON row_charges(organization_id);
CREATE TRIGGER row_charges_fill_organization_id BEFORE INSERT OR UPDATE OF row_id ON row_charges
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('list_rows', 'row_id');
ALTER TABLE signals ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_signals_organization_id ON signals(organization_id);
CREATE TRIGGER signals_fill_organization_id BEFORE INSERT OR UPDATE OF watchlist_id ON signals
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('watchlists', 'watchlist_id');
ALTER TABLE watchlist_accounts ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_watchlist_accounts_organization_id ON watchlist_accounts(organization_id);
CREATE TRIGGER watchlist_accounts_fill_organization_id BEFORE INSERT OR UPDATE OF watchlist_id ON watchlist_accounts
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('watchlists', 'watchlist_id');
ALTER TABLE lookup_claims ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_lookup_claims_organization_id ON lookup_claims(organization_id);
CREATE TRIGGER lookup_claims_fill_organization_id BEFORE INSERT OR UPDATE OF job_id ON lookup_claims
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('jobs', 'job_id');
ALTER TABLE watchlist_runs ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_watchlist_runs_organization_id ON watchlist_runs(organization_id);
CREATE TRIGGER watchlist_runs_fill_organization_id BEFORE INSERT OR UPDATE OF watchlist_id ON watchlist_runs
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('watchlists', 'watchlist_id');
ALTER TABLE saved_search_runs ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_saved_search_runs_organization_id ON saved_search_runs(organization_id);
CREATE TRIGGER saved_search_runs_fill_organization_id BEFORE INSERT OR UPDATE OF saved_search_id ON saved_search_runs
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('saved_searches', 'saved_search_id');

-- The parents of the keys below.
ALTER TABLE list_rows ADD CONSTRAINT list_rows_id_org_key UNIQUE (id, organization_id);
ALTER TABLE watchlists ADD CONSTRAINT watchlists_id_org_key UNIQUE (id, organization_id);
ALTER TABLE watchlist_runs ADD CONSTRAINT watchlist_runs_id_org_key UNIQUE (id, organization_id);
ALTER TABLE saved_searches ADD CONSTRAINT saved_searches_id_org_key UNIQUE (id, organization_id);
ALTER TABLE saved_searches ADD CONSTRAINT saved_searches_id_user_org_key UNIQUE (id, user_id, organization_id);

-- The composite keys.
ALTER TABLE list_rows DROP CONSTRAINT list_rows_list_id_fkey,
    ADD CONSTRAINT list_rows_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE list_columns DROP CONSTRAINT list_columns_list_id_fkey,
    ADD CONSTRAINT list_columns_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE list_plans DROP CONSTRAINT list_plans_list_id_fkey,
    ADD CONSTRAINT list_plans_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE list_plans DROP CONSTRAINT list_plans_approved_job_id_fkey,
    ADD CONSTRAINT list_plans_approved_job_id_org_fkey FOREIGN KEY (approved_job_id, organization_id)
    REFERENCES jobs (id, organization_id) ON DELETE SET NULL (approved_job_id);
ALTER TABLE watchlists DROP CONSTRAINT watchlists_list_id_fkey,
    ADD CONSTRAINT watchlists_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE saved_searches DROP CONSTRAINT saved_searches_list_id_fkey,
    ADD CONSTRAINT saved_searches_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE saved_searches DROP CONSTRAINT saved_searches_campaign_id_fkey,
    ADD CONSTRAINT saved_searches_campaign_id_org_fkey FOREIGN KEY (campaign_id, organization_id)
    REFERENCES outreach_campaigns (id, organization_id) ON DELETE CASCADE;
ALTER TABLE saved_searches DROP CONSTRAINT saved_searches_campaign_id_user_id_fkey,
    ADD CONSTRAINT saved_searches_campaign_user_org_fkey FOREIGN KEY (campaign_id, user_id, organization_id)
    REFERENCES outreach_campaigns (id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE row_charges DROP CONSTRAINT row_charges_row_id_fkey,
    ADD CONSTRAINT row_charges_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
    REFERENCES list_rows (id, organization_id) ON DELETE CASCADE;
ALTER TABLE row_charges DROP CONSTRAINT row_charges_job_id_fkey,
    ADD CONSTRAINT row_charges_job_id_org_fkey FOREIGN KEY (job_id, organization_id)
    REFERENCES jobs (id, organization_id) ON DELETE SET NULL (job_id);
ALTER TABLE lookup_claims DROP CONSTRAINT lookup_claims_job_id_fkey,
    ADD CONSTRAINT lookup_claims_job_id_org_fkey FOREIGN KEY (job_id, organization_id)
    REFERENCES jobs (id, organization_id) ON DELETE CASCADE;
ALTER TABLE lookup_claims DROP CONSTRAINT lookup_claims_row_id_fkey,
    ADD CONSTRAINT lookup_claims_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
    REFERENCES list_rows (id, organization_id) ON DELETE CASCADE;
ALTER TABLE signals DROP CONSTRAINT signals_watchlist_id_fkey,
    ADD CONSTRAINT signals_watchlist_id_org_fkey FOREIGN KEY (watchlist_id, organization_id)
    REFERENCES watchlists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE signals DROP CONSTRAINT signals_row_id_fkey,
    ADD CONSTRAINT signals_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
    REFERENCES list_rows (id, organization_id) ON DELETE CASCADE;
ALTER TABLE signals DROP CONSTRAINT signals_run_id_fkey,
    ADD CONSTRAINT signals_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
    REFERENCES watchlist_runs (id, organization_id) ON DELETE SET NULL (run_id);
ALTER TABLE watchlist_accounts DROP CONSTRAINT watchlist_accounts_watchlist_id_fkey,
    ADD CONSTRAINT watchlist_accounts_watchlist_id_org_fkey FOREIGN KEY (watchlist_id, organization_id)
    REFERENCES watchlists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE watchlist_accounts DROP CONSTRAINT watchlist_accounts_row_id_fkey,
    ADD CONSTRAINT watchlist_accounts_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
    REFERENCES list_rows (id, organization_id) ON DELETE CASCADE;
ALTER TABLE watchlist_runs DROP CONSTRAINT watchlist_runs_watchlist_id_fkey,
    ADD CONSTRAINT watchlist_runs_watchlist_id_org_fkey FOREIGN KEY (watchlist_id, organization_id)
    REFERENCES watchlists (id, organization_id) ON DELETE CASCADE;
ALTER TABLE watchlist_runs DROP CONSTRAINT watchlist_runs_reservation_id_fkey,
    ADD CONSTRAINT watchlist_runs_reservation_id_org_fkey FOREIGN KEY (reservation_id, organization_id)
    REFERENCES credit_transactions (id, organization_id);
ALTER TABLE saved_search_runs DROP CONSTRAINT saved_search_runs_saved_search_id_fkey,
    ADD CONSTRAINT saved_search_runs_saved_search_id_org_fkey FOREIGN KEY (saved_search_id, organization_id)
    REFERENCES saved_searches (id, organization_id) ON DELETE CASCADE;
ALTER TABLE saved_search_runs DROP CONSTRAINT saved_search_runs_saved_search_id_user_id_fkey,
    ADD CONSTRAINT saved_search_runs_search_user_org_fkey FOREIGN KEY (saved_search_id, user_id, organization_id)
    REFERENCES saved_searches (id, user_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE saved_search_runs DROP CONSTRAINT saved_search_runs_reservation_id_fkey,
    ADD CONSTRAINT saved_search_runs_reservation_id_org_fkey FOREIGN KEY (reservation_id, organization_id)
    REFERENCES credit_transactions (id, organization_id);

-- Nothing references the keys that carried the owner without the organization.
ALTER TABLE saved_searches DROP CONSTRAINT saved_searches_id_user_id_key;
