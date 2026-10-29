-- our_class: Zenbree's vote on a reply's subject and text (#931), cast only on a marker. "Nothing matched" is no
-- vote, so it is never stored as human: a later autoresponder vote from Instantly must still pause.
-- hard_bounce: a leads pass saw the lead at Instantly status -1. The webhook's email_bounced does not say hard or soft.
-- applied_effect: the native effect last applied for the fact (app/integrations/instantly_sync/effects.py). A later
-- fact of the person never applies it again, so a user's undo (a deleted exclusion, a new enrollment) holds.
-- effect_attempts, effect_error: the failures in a row of the effect, retried by the reconcile pass up to a bound.
ALTER TABLE instantly_events
    ADD COLUMN our_class text CHECK (our_class IN ('auto', 'opt_out')),
    ADD COLUMN hard_bounce boolean NOT NULL DEFAULT false,
    ADD COLUMN applied_effect text
        CHECK (applied_effect IN ('auto_reply', 'human_reply', 'opt_out_reply', 'unsubscribed', 'bounced')),
    ADD COLUMN effect_attempts smallint NOT NULL DEFAULT 0 CHECK (effect_attempts >= 0),
    ADD COLUMN effect_error text,
    ADD CONSTRAINT instantly_events_vote_shape CHECK ((our_class IS NULL OR kind = 'reply') AND (NOT hard_bounce OR kind = 'bounced'));

-- A fact recorded before this migration had no native effect. Its effect counts as applied, so the first new fact of
-- an address does not replay its history. A reply with no class yet gets its effect once it has one.
UPDATE instantly_events SET applied_effect = CASE
        WHEN kind = 'unsubscribed' THEN 'unsubscribed'
        WHEN reply_nature = 'human' THEN 'human_reply'
        WHEN reply_nature = 'auto' THEN 'auto_reply'
    END
    WHERE kind = 'unsubscribed' OR reply_nature IN ('human', 'auto');

-- The reconcile pass reads these for each connection: a failed effect, and a reply no side voted on, which counts as
-- human once the key may not read emails.
CREATE INDEX instantly_events_effect_due ON instantly_events(connection_id)
    WHERE effect_attempts > 0 OR (reply_nature = 'unclassified' AND our_class IS NULL AND applied_effect IS NULL);

CREATE OR REPLACE FUNCTION instantly_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rank_old int := array_position(ARRAY['observed', 'record', 'event'], OLD.time_basis);
    rank_new int := array_position(ARRAY['observed', 'record', 'event'], NEW.time_basis);
BEGIN
    IF (NEW.id, NEW.organization_id, NEW.project_id, NEW.connection_id, NEW.lead_id, NEW.workspace_id,
        NEW.campaign_id, NEW.email, NEW.kind, NEW.label, NEW.fact_key, NEW.received_at)
       IS DISTINCT FROM
       (OLD.id, OLD.organization_id, OLD.project_id, OLD.connection_id, OLD.lead_id, OLD.workspace_id,
        OLD.campaign_id, OLD.email, OLD.kind, OLD.label, OLD.fact_key, OLD.received_at) THEN
        RAISE EXCEPTION 'instantly_events: the identity of a fact never changes';
    END IF;
    IF (OLD.push_key IS NOT NULL AND NEW.push_key IS DISTINCT FROM OLD.push_key)
       OR (OLD.provider_ref IS NOT NULL AND NEW.provider_ref IS DISTINCT FROM OLD.provider_ref)
       OR (OLD.message_id IS NOT NULL AND NEW.message_id IS DISTINCT FROM OLD.message_id)
       OR (OLD.step IS NOT NULL AND NEW.step IS DISTINCT FROM OLD.step) THEN
        RAISE EXCEPTION 'instantly_events: a provider id is set once';
    END IF;
    IF NEW.reply_nature IS DISTINCT FROM OLD.reply_nature AND OLD.reply_nature <> 'unclassified' THEN
        RAISE EXCEPTION 'instantly_events: only an unclassified reply is classified';
    END IF;
    IF OLD.our_class IS NOT NULL AND (NEW.our_class IS NULL
       OR array_position(ARRAY['auto', 'human', 'opt_out'], NEW.our_class)
          < array_position(ARRAY['auto', 'human', 'opt_out'], OLD.our_class)) THEN
        RAISE EXCEPTION 'instantly_events: a vote moves only to a safer class';
    END IF;
    IF OLD.hard_bounce AND NOT NEW.hard_bounce THEN
        RAISE EXCEPTION 'instantly_events: a hard bounce stays hard';
    END IF;
    IF rank_new < rank_old OR (rank_new = rank_old AND NEW.occurred_at <> OLD.occurred_at) THEN
        RAISE EXCEPTION 'instantly_events: a time changes only to a more exact one';
    END IF;
    RETURN NEW;
END $$;

ALTER TABLE exclusions DROP CONSTRAINT exclusions_origin_check;
ALTER TABLE exclusions ADD CONSTRAINT exclusions_origin_check
    CHECK (origin IN ('profile', 'unsubscribed', 'own_domain', 'manual', 'bounced'));
