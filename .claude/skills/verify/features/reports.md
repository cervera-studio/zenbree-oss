# Campaign report

The campaign report (#942) at `/campaigns/report` shows, for the active client and a range of days, what each campaign did: the people contacted, the messages sent, the people who replied and replied with interest, and the bounces, meetings and deals. Native campaigns and the Instantly campaigns of the client sit in one table. Every count is a number of people on the date of their first event, so a person who replies twice counts once. A count that Zenbree does not measure yet reads `Non noto`, never 0. The page only reads: it sends nothing and changes nothing.

## Sub-features

- `report-range` picks the days: the date fields `Dal` and `Al` with `button "Mostra"`, or the links `Ultimi 7 giorni`, `Ultimi 30 giorni` and `Ultimi 90 giorni` (the active one has `aria-current="page"`). The default is the last 30 days ending today in the user's time zone. The range lives in the URL (`?from=YYYY-MM-DD&to=YYYY-MM-DD`). A missing or wrong day falls back to the last 30 days, reversed days swap, and a range longer than 366 days keeps the last 366.
- `report-table` has the columns `Campagna`, `Contattati`, `Inviati`, `Risposte`, `Positivi`, `Rimbalzi`, `Riunioni` and `Deal`. The campaign cell has the name and a `Zenbree` or `Instantly` tag. `Risposte` reads `<n> su <k> contattati` and `Positivi` reads `<n> su <m> risposte`. `Inviati` adds `<e> email · <i> inviti · <m> messaggi` when LinkedIn sent something.
- `report-lineage` is the `Da dove vengono le persone` disclosure in the campaign cell: one line per list, source and signal, with that group's counts. A person deleted from a native campaign shows as `Persona eliminata`. The lines of a campaign add up to its totals.
- `report-variants` (#934) is the `Risultati per variante` disclosure in the cell of a native campaign that tests copy B: `Variante A`, `Variante B`, then `Fuori dal test` and `Non attribuite` when they count something. Each group lists `Persone nel gruppo oggi`, `Persone contattate`, `Messaggi inviati`, `Risposte` (`<n> su <k> persone contattate`) and `Risposte positive` (`<n> su <m> risposte`), with no percentage; `Non attribuite` shows both replies as plain counts. The groups add up to the row. A campaign with no test has no disclosure. The notes then add that Zenbree picks no winner and what the last two groups are.
- `report-notes` are the notes under the table. When the client has an Instantly campaign and the range reaches past the last complete read of Instantly, a note says `I conteggi di Instantly sono completi fino al <data>`; before the first complete read, it says they are not complete yet.

## How to get to it (user POV)

- Choose `link "Campagne"` in the sidebar, then `link "Report"` in the topbar, beside `button "Nuova campagna da una lista"`.
- The topbar path is `Campagne › Report campagne`; `link "Campagne"` goes back to the index.

## Driving it with bin/browser

Preconditions: a signed-in user whose client has campaigns that left draft, with sends and replies in the range. Make them with the setup SQL of [campaigns](campaigns.md) (`outreach_campaign_sends` rows with `delivered_at`, and `outreach_inbound_messages` rows of `kind = 'reply'` with `campaign_id`, `target_id` and a `label`). For Instantly rows, seed `instantly_events` as in [instantly](instantly.md), or drive `bin/fake-instantly`.

1. `bin/browser click link "Campagne"`, then `bin/browser click link "Report"`, and `bin/browser wait-url /campaigns/report`.
2. `ax` shows `heading "Report campagne"`, the range form and the table. Compare each row with `curl -sS -H "Authorization: Bearer $T" -H "X-Project-Id: <project>" "$VERIFY_API/v1/reports/campaigns?from=<from>&to=<to>&tz=Europe/Rome"`, and compare `counts.contacted`, `counts.human_replies`, `counts.positive_replies` and `counts.sent`. `$T` is a session JWT from `bin/browser eval "fetch('/api/auth/token').then(r => r.json())"`.
3. Give one person two replies on two days in the range. The `Risposte` count of that campaign grows by one, not two.
4. Choose `link "Ultimi 7 giorni"`. The URL carries the new days, the link has `aria-current`, and a reply older than 7 days leaves the count.
5. Open `Da dove vengono le persone` and check that the lines add up to the row.
6. Variant split (#934). Setup in psql on a campaign `<cid>` that left draft, with steps of channel email: give it a test, put new people in each group, seed one send for each of them, and seed a reply from one A person, the B person and an address of nobody:
   ```sql
   UPDATE outreach_campaigns SET steps = jsonb_set(steps, '{0,b}', '{"subject": "B", "body": "B"}') WHERE id = '<cid>';
   INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, source, variant)
   SELECT id, owner_id, v || n || '@variant.test', 'active', 'manual', nullif(v, 'outside')
     FROM outreach_campaigns, unnest(ARRAY['a', 'a', 'b', 'outside']) WITH ORDINALITY AS u(v, n)
    WHERE id = '<cid>';
   INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
                                        idempotency_key, delivered_at)
   SELECT campaign_id, owner_id, id, 1, 'email', 'sent', email, gen_random_uuid()::text, now() - interval '2 days'
     FROM outreach_campaign_targets WHERE campaign_id = '<cid>' AND email LIKE '%@variant.test';
   INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, from_address,
                                          campaign_id, target_id, label, received_at)
   SELECT c.owner_id, c.project_id, 'email', 'reply', gen_random_uuid()::text, e, c.id, t.id, 'interested',
          now() - interval '1 day'
     FROM outreach_campaigns c
    CROSS JOIN unnest(ARRAY['a1@variant.test', 'b3@variant.test', 'boss@variant.test']) e
     LEFT JOIN outreach_campaign_targets t ON t.campaign_id = c.id AND t.email = e
    WHERE c.id = '<cid>';
   ```
   The variant is final once set, so seed it on new people, never with `UPDATE`. People who were in the campaign before have no variant: they count under `Fuori dal test`. For the last 7 days, beside what those people did, the groups read A `2` in the group, `2` contacted, `1 su 2` replies, `1 su 1` positive; B `1`, `1`, `1 su 1`, `1 su 1`; outside `1`, `1`, `0 su 1`; not attributed `0`, `0`, `1`, `1` (the reply of nobody, as plain counts). Reload the report for the last 7 days and open `Risultati per variante`: compare each group with `variants` of the same `curl`, and check that each count summed over the groups equals the row's count, `sent` per channel too. Repeat for the 7 days before: the reply and send dates move out, `Persone nel gruppo oggi` stays. A campaign with no `b` has `variants: []` and no disclosure.
7. At 375 px, each campaign is a card with the column names, and the page has no sideways scroll.

Checks for the API: a `tz` that is not an IANA zone, `to` before `from`, and a range over 366 days each return 422 `REQUEST.INVALID_INPUT`. Another client's campaigns never show.
