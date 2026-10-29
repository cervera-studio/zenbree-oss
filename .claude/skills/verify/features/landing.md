# Landing

The standalone marketing page in `landing/` (Astro, static, served by nginx) has a hero and product mock, four steps, a "Perché Zenbree" block, an open-source section, seven FAQs and a closing contact link. Access requests use `mailto:supporto@zenbree.com`; sign-in and legal links derive from `PUBLIC_APP_URL`. The repository link points to `cervera-studio/zenbree-oss`.

## Sub-features

- `landing-copy` has no financial references in any language: no prices, credits, refunds, paid-service wording or free-trial promises. Check the product mock, closed FAQ answers, meta descriptions, JSON-LD and the four Open Graph images.
- `landing-hero` shows "Zenbree trova chi contattare" and "Richiedi l’accesso".
- `landing-steps` has four static cells under `#come` (`Descrivi`, `Approva il piano`, `Ricevi la lista`, `Contatta`). The plan snippet contains rows and steps, with no budget.
- `landing-open-source` describes self-hosting and the managed installation, with GitHub and contact links.
- `landing-faq` opens one answer at a time, with all answers closed on load.
- `landing-links` keeps access requests on the contact address and sign-in, terms and privacy on the app. It has no sign-up link or brief form.
- `landing-locale` serves Italian at `/` and the other languages at `/en/`, `/es/` and `/fr/`. A language switch keeps the selected section: `/en/#why` becomes `/#perche`; `#open-source` and `#faq` remain the same. `x-default` points to `/en/`.
- `landing-menu` is the sheet below 760 px, with four section links and sign-in. The theme and language controls sit beside its close button.
- `landing-theme` follows the OS until the toggle stores `localStorage.theme`. The preference survives reload and language changes.
- `landing-phone` fits a 390 px viewport without horizontal scroll or overflowing snippet text.
- `landing-app-redirect` sends unknown paths to the app with 308, preserving path and query string.

## Driving it with bin/browser

For static-page checks, build with `PUBLIC_APP_URL=http://localhost:<app-port> pnpm --dir landing build`, then serve `landing/dist` with `pnpm --dir landing preview --host 127.0.0.1 --port 14321`. Start an isolated browser with `VERIFY_ID=<task> bin/browser start`. Stop the browser and preview server after the drive. This proves the built landing without contacting the developer’s app.

For nginx redirects, use the verify stack and `bin/stack compose up -d --build landing`; the landing is on port 14321. Static Astro preview cannot prove nginx redirects or app authentication.

- **Copy and metadata.** Visit `/`, `/en/`, `/es/` and `/fr/`. Read body text including closed FAQ answers, description meta tags and JSON-LD. None mentions money, revenue, prices, credits, refunds or paid/free offers. Assert there is no `Offer`, `priceCurrency`, cost calculator or pricing section. Open the social images referenced by the page and inspect all four.
- **Structure.** Each locale has four navigation anchors with existing targets, four step cells, seven FAQ items and no form. On `/`, `document.querySelectorAll('#come ol > li').length` is `4`.
- **FAQ.** On load `document.querySelectorAll('details[name=faq][open]').length` is `0`. Click the first summary, then the second: exactly one item remains open, matching the last click.
- **Links.** Inspect all anchors. Header, hero, hosted-service and closing access links use `mailto:supporto@zenbree.com`; GitHub links use `https://github.com/cervera-studio/zenbree-oss`. Sign-in, terms and privacy use `PUBLIC_APP_URL`. No link goes to `/auth/sign-up`.
- **Locale.** On `/en/`, the header anchors are `["#how", "#why", "#open-source", "#faq"]`. Click "Why", open the header language menu and choose Italian: the location becomes `/#perche`. A switch at `#open-source` keeps that section. The document language, current-language label and hero follow the selected locale.
- **Language keyboard.** Focus the language summary; Enter opens it, ArrowDown/ArrowUp move through items and wrap, Escape closes it and restores focus to the summary. Enter on an item follows its link. A click outside closes the list.
- **Mobile menu.** With `--width 390 --height 844` on each command, click "Apri il menu" and assert `#site-menu` matches `:popover-open`. It holds four section links, sign-in, theme and language controls. Choosing "Open source" closes it with hash `#open-source`; Escape closes it too.
- **Responsive layout.** Check each locale at 390, 905, 1024 and 1440 px. `document.documentElement.scrollWidth <= window.innerWidth` must be true. Also compare descendant bounding rectangles with each `.shadow-snippet` card: no text leaves its card. Use `--width` for true 390 px; `viewport` has a 500 px minimum.
- **Theme and contrast.** On Italian and English, at 1440 and 390 px, test `light` and `dark`. Clear stored theme first, navigate, then `bin/browser contrast --reveal --scheme <scheme>` must exit 0 with no style below its floor. Reload between checks because `--reveal` opens details. Toggle dark, reload, switch language and confirm it stays dark; toggle light and confirm it overrides a dark OS preference.
- **App redirect.** On nginx, `curl -s -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost:14321/lists?x=1'` must print `308 <app-url>/lists?x=1`; `/` and `/en/` answer 200.
- **Proof.** Save the copy/metadata and structural read-backs, link destinations, locale/menu results, responsive measurements, full-page desktop/phone screenshots in light/dark, an open FAQ screenshot and social-image screenshots. Name skipped app or nginx cases explicitly.

## Gotchas

- `PUBLIC_APP_URL` is baked in at build time; rebuild after changing it.
- `bin/stack up` does not start the landing or check whether its image is current. Port 14321 is shared by verify landing instances, so do not start two at once.
- Theme is stored on the landing origin separately from the app. Clear `localStorage.theme` to return to OS preference.
- Pass `VERIFY_ID` on every browser command. Stop this run’s browser and server after the drive; a full-stack drive also requires `bin/stack down`.
