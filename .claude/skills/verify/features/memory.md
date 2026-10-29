# Memory

A signed-in user edits the memory document the agent reads at the start of every chat, on Settings › Memoria. The user can lock the document so the agent cannot change it.

## Sub-features

- `memory-edit` edits the Markdown document; the header shows `N / 8.000 caratteri`.
- `memory-status` shows `Modifiche non salvate` while the text or lock differs from the saved copy, and `Salvato.` otherwise.
- `memory-save` saves the document and the lock together.
- `memory-lock` toggles `switch "Blocca la memoria"` in the aside.
- `memory-truncated` warns when the saved document is longer than what reaches the agent.
- `memory-sources` (#349) is the `Da dove arriva` card under the lock: the onboarding profile (the active client's, read together with Memory) and chat preferences, and `link "Rifai il profilo dal sito"` to `/onboarding?redo=1` ([onboarding](onboarding.md)).

## How to get to it (user POV)

- Choose `link "Impostazioni"` in the sidebar, then the tab `link "Memoria"`. It opens `/settings/memory`.

## Driving it with bin/browser

Preconditions:

- Seed and sign in a user past onboarding, as in the README.

- **Open.** `bin/browser click link "Impostazioni"`, `bin/browser click link "Memoria"`, then `bin/browser wait-url /settings/memory`. `ax` shows `textbox "Documento di memoria"`, `switch "Blocca la memoria"` and `button "Salva"` (disabled).
- **Edit.** `bin/browser fill textbox "Documento di memoria" "## Azienda"`, then `bin/browser wait text "Modifiche non salvate"`. `button "Salva"` is enabled.
- **Save.** `bin/browser click button "Salva"`, then `bin/browser wait text "Salvato."`.
- **Lock.** `bin/browser click switch "Blocca la memoria"`, `bin/browser click button "Salva"`, reload. `ax` shows `switch "Blocca la memoria" [checked]`.
- **Truncation.** Fill `textbox "Documento di memoria"` with more than 3,000 characters (for example 90 lines of `- nota numero NNN su un cliente importante`) and click `button "Salva"`. The `status` reads `Il documento è lungo: all'agente arrivano i primi 3.000 caratteri. Rimuovi ciò che non serve più.` It appears only after the save, never while typing.
- **Proof.** Save `bin/stack psql -tAc "SELECT body, locked FROM user_memory_doc"`, which prints `## Azienda|t`, and an `ax` dump. A user who never saved has no row at all.

## Gotchas

- The page shows `Salvato.` on a fresh load: nothing is unsaved.
- The textarea caps input at the saved `maxChars` (8000).
- The agent honouring the lock needs an LLM key and was not driven.
- Below 760 px the aside stacks under the document.
- Driven 2026-10-04 at 9c0ac03, including the truncation warning.
- Driven 2026-10-07 at 40a7ebc: the `Da dove arriva` card in light, dark and at 390 wide, and its link.
- Driven 2026-10-10 at dae0d57: every step, no drift.
- Driven 2026-10-16 at c2551a38: every step; the truncation status now writes `3.000`.
