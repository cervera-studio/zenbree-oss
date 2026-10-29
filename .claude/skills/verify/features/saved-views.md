# Saved views

A user names the filters of a list (its tab, search text and fit floor) as a saved view of the active client (#827). `Viste salvate` (`/views`) lists the client's views, opens one on its list with the filters applied, renames and deletes it. The sidebar entry shows how many views the client has.

## Sub-features

- `views-save` saves the open list's tab, search and fit floor from `button "Salva vista"`.
- `views-count` shows the client's count on `link "Viste salvate"` in the sidebar, live from the nav stream.
- `views-open` opens a view: the list with its tab pressed, the search filled and the fit chip on when the view has a floor.
- `views-rename` renames a view in place.
- `views-delete` deletes a view; the toast's `button "Annulla"` saves it again.
- `views-api` lists, creates, renames and deletes through `/v1/saved-views` (JWT or `sbk_` key).

## How to get to it (user POV)

- On a list (`/lists/<id>`), choose a tab and type in `searchbox "Cerca nella lista"`, then `button "Salva vista"` opens `dialog "Salva questa vista"` with `textbox "Nome"`.
- `link "Viste salvate"` in the sidebar (below `Liste`) opens `/views`. Each row has `link "Apri «<name>»"`, `button "Rinomina «<name>»"` and `button "Elimina «<name>»"`.
- `Report` has no entry: the sidebar never shows it.

## Driving it with bin/browser

Preconditions:

- `bin/stack up` has seeded `dev@zenbree.local` / `zenbree-dev-1234` (6 lists). Sign in as that user.
- No saved views: the sidebar reads `Viste salvate` with `0`, and `/views` says `Nessuna vista salvata. Filtra una lista, poi scegli «Salva vista».`

- **Save.**
  1. `bin/browser click link "Liste"`, then `bin/browser click link "Agenzie marketing Milano · SDR"`.
  2. `bin/browser click button "Consegnate 48"`, then `bin/browser fill searchbox "Cerca nella lista" "mar"`.
  3. `bin/browser click button "Salva vista"`, `bin/browser fill textbox "Nome" "Milano consegnate"`, `bin/browser click button "Salva"`.
  4. `bin/browser wait text "Vista salvata."`. psql: `SELECT name, tab, query, min_fit FROM saved_views` gives `Milano consegnate|delivered|mar|` (no floor). With `button "Fit oltre 80"` pressed before the save, `min_fit` is `80`. The sidebar count turns to `1` with no reload.
- **Open.** `bin/browser click link "Viste salvate"`, then `bin/browser click link "Apri «Milano consegnate»"`. The URL ends `?tab=delivered&q=mar` (and `&fit=<n>` for a view with a floor), `button "Consegnate 48"` is pressed and the search box holds `mar`.
- **Rename.** On `/views`, `bin/browser click button "Rinomina «Milano consegnate»"`, `bin/browser fill textbox "Nuovo nome" "Solo Milano"`, `bin/browser click button "Salva il nome"`. A toast reads `Vista rinominata.`; psql shows the new name.
- **Delete and undo.** `bin/browser click button "Elimina «Solo Milano»"`: the toast reads `«Solo Milano» eliminata.`, psql counts 0. `bin/browser click button "Annulla"` saves it again (a new id, same list, tab and query).
- **Phone.** `bin/browser screenshot out.png --width 390 --height 800` on `/views` and on the list: `document.documentElement.scrollWidth` equals `clientWidth` (390). The table becomes one card per view.
- **API.** With a bearer token, `DELETE /v1/saved-views/00000000-0000-0000-0000-000000000000` returns 404 `SAVED_VIEW.NOT_FOUND`; `POST` with a list of another client returns 404 `LIST.NOT_FOUND`.
- **Proof.** Save `bin/stack psql -tAc "SELECT name, tab, query, min_fit FROM saved_views"` and `bin/browser ax --out "$VERIFY_ARTIFACTS/views/views.ax.txt"`.

## Gotchas

- A view keeps only the tab, the search text and the fit floor. The chip names the floor in force: `Fit oltre 80` by default, `Fit oltre 50` for a view saved through the API with `min_fit` 50. The column set, sort and row selection are not saved.
- A view of a deleted list is neither listed nor counted.
- The tab bar hides `Escluse` and `Senza contatto` while empty; a view that names one opens on `Tutte` until a row matches.
- Driven 2026-10-16 at the #827 branch: every sub-feature but `views-api` through curl (only the 404 through the browser's token), light, dark and 390 px.
- Driven 2026-10-16 at c2551a38: empty state, save, open, rename, delete and undo, the sidebar count and the 404.
