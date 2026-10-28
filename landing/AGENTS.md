# Landing — agent notes

Read the root [AGENTS.md](../AGENTS.md) first. This file covers `landing/` only: a static Astro site served by nginx. [README.md](README.md) lists the commands.

## Copy

- Copy lives in `src/i18n/<language>.ts`, one file per language of `paths` in `src/content.ts`. Italian is served at `/`, the others under their code (`/en/`, `/es/`, `/fr/`). English is the product's base language: `x-default` points to it.
- Every file is typed by `Content` in `src/content.ts`. A key missing from one of them fails `pnpm check`. To add copy, add the field to `Content`, then to every language file, then use it in a component under `src/components/`.
- Section ids are in each language too: `sections` in `Content`. The header links, the section ids and the language menu read them, so a language switch keeps the section in view.
- Links to the app derive from `PUBLIC_APP_URL` (a build argument) in `src/content.ts`. Changing it needs a rebuild.

## Styles and the lint

- Styles are Tailwind classes on the element. Tokens live in `src/styles/theme.css`. The breakpoints are the variants `tablet:` and `phone:`.
- `lint/check.mjs` runs in `pnpm lint`. It bans:
  - an arbitrary Tailwind value (use a scale step, a token or a stock utility);
  - a `<style>` element or a `style=` attribute in an `.astro` file;
  - any `.css` file under `src` except `styles/theme.css`;
  - anything in `theme.css` except `@import`, `@theme`, `@font-face`, `@custom-variant`, `@utility` and the dark `@media` block;
  - an `@utility` that is not in `APPROVED_UTILITIES`.
- Never edit `APPROVED_UTILITIES` or the checks. Fix the markup. If you think a hit is a false alarm, or you need a new `@utility`, stop and ask the user.

## Checks

- `./harness/init.sh landing --fast` runs `pnpm lint` and `pnpm check`.
- `pnpm test` runs `lint/check.test.mjs`. `pnpm build` writes `dist/`.
- A source file in `src/` stays within 400 lines (`harness/size_check.py`).
