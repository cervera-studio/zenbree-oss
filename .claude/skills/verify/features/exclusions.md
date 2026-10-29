# Exclusions

A user keeps a do-not-contact list on Settings › Esclusioni. An entry is an email, a domain, a company name or a LinkedIn URL. Excluded companies and people are left out of search results, never enriched and never added to campaigns. A domain also covers its subdomains and every address at it.

## Sub-features

- `excl-add` adds one value of the selected type.
- `excl-invalid` rejects a value that does not fit the selected type.
- `excl-duplicate` reports a value that is already listed and adds nothing.
- `excl-remove` removes an entry. A toast offers `Annulla`, which adds it back.
- `excl-import` imports a CSV or TXT file, one value per line.
- `excl-api` lists, adds and deletes entries through `/v1/exclusions`. This route accepts a JWT or an `sbk_` key.

## How to get to it (user POV)

- Choose `link "Impostazioni"` in the sidebar, then the tab `link "Esclusioni"`. It opens `/settings/exclusions`.
- Choose `button "Importa CSV"` to pick a file. It is a hidden `input[type=file]` behind a label.
- Call `GET`, `POST` or `DELETE "$VERIFY_API/v1/exclusions[/<id>]"` with a bearer token.

## Driving it with bin/browser

Preconditions:

- Sign in a seeded user who is past onboarding, as described in the README.
- The list is empty: the page shows `Nessuna esclusione.`

- **Open.** Run `bin/browser click link "Impostazioni"`, `bin/browser click link "Esclusioni"`, then `bin/browser wait-url /settings/exclusions`.
- **Add a domain.** "Dominio" is the default type.
  1. `bin/browser fill textbox "Valore" "Competitor.IT"`
  2. `bin/browser click button "Aggiungi"`
  3. `bin/browser wait text "Aggiunta 1 esclusione."`

  The list shows `button "Rimuovi l'esclusione competitor.it"`: values are stored lowercased.
- **Duplicate.** Add `competitor.it` again. The status reads `Niente di nuovo da aggiungere.` and the row count stays 1.
- **Add an email.**
  1. `bin/browser select combobox "Tipo" "Email"`
  2. `bin/browser fill textbox "Valore" "ceo@competitor.it"`
  3. `bin/browser click button "Aggiungi"`
  4. `bin/browser wait text "ceo@competitor.it"`
- **Invalid value.**
  1. `bin/browser select combobox "Tipo" "Email"`
  2. `bin/browser fill textbox "Valore" "not-an-email"`
  3. `bin/browser click button "Aggiungi"`

  An alert reads `Il valore non è valido per il tipo selezionato.`
- **Import.**
  1. `printf 'acme.com\nceo@acme.com\n' > "$VERIFY_ARTIFACTS/exclusions/import.csv"`
  2. `bin/browser upload "input[type=file]" "$VERIFY_ARTIFACTS/exclusions/import.csv"`
  3. `bin/browser wait css "[role=status]"`

  The status reads `Aggiunte 2 esclusioni.` and psql shows `domain|acme.com` and `email|ceo@acme.com`.
- **Remove and undo.**
  1. `bin/browser click button "Rimuovi l'esclusione competitor.it"`, then `bin/browser wait-gone button "Rimuovi l'esclusione competitor.it"`. A toast reads `Esclusione «competitor.it» rimossa.` with `button "Annulla"`.
  2. `bin/browser click button "Annulla"`, then `bin/browser wait button "Rimuovi l'esclusione competitor.it"`. psql counts the value once again.
- **API errors.** With a bearer token, `POST /v1/exclusions` with `{"items":[{"kind":"email","value":"zz"}]}` returns 422 `EXCLUSIONS.INVALID_VALUE`. `DELETE /v1/exclusions/00000000-0000-0000-0000-000000000000` returns 404.
- **Proof.**
  1. Save `bin/stack psql -tAc "SELECT kind, value FROM exclusions ORDER BY created_at"`.
  2. Save `curl -sS -H "Authorization: Bearer $TOKEN" "$VERIFY_API/v1/exclusions"`. For `$TOKEN`, use an `sbk_` key from [api-keys](api-keys.md) or the session JWT.
  3. Save `bin/browser ax --out "$VERIFY_ARTIFACTS/exclusions/list.ax.txt"`.

### Origin column (#348)

The table has `columnheader "Origine"`. Each row shows where it came from: `Dal profilo` (a line of the profile's exclusions), `Disiscritta` (link unsubscribe or reply opt-out), `Il tuo dominio` (the domain of the profile's website, written when `POST /v1/onboarding/profile` saves a new website) and `Aggiunto a mano` (add form, CSV, API). Add by hand and expect `Aggiunto a mano`; `bin/stack psql -tAc "SELECT value, source, origin FROM exclusions"` shows the other origins. On a phone viewport the origin sits under the value.

## Gotchas

- The type is a native `<select>`. Use `bin/browser select`, not `click` on an option.
- The import recognises a header row only when a column name is in its list (`email`, `domain`, `dominio`, `website`, `sito`, `company`, `azienda`, `name`, `nome`, `linkedin` and similar, `web/lib/exclusions/csv.ts`); it then takes that column with its kind and skips the header. `;` works as a delimiter. Driven 2026-10-04: `email;name` then `boss@globex.it;Boss` imports one email. A header it does not know, such as `value,type`, is imported as a `company_name` value.
- `button "Aggiungi"` stays disabled until `textbox "Valore"` has text.
- Adding is idempotent. A duplicate value does not create a second row.
- Remove has no confirmation dialog; the undo toast is the safety net. Assert the row with psql, not the toast.
- A value that is not an email, domain or LinkedIn URL is guessed as a company name, so most free text imports successfully. A shared host such as `facebook.com` is rejected as a domain.
- Driven 2026-10-04 at 9c0ac03, every sub-feature. That excluded rows stay out of search, enrichment and campaigns needs an LLM key and vendor keys and was not driven.
- Driven 2026-10-10 at dae0d57: every step, no drift.
