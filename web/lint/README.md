# Lint rules

`pnpm lint` runs Biome, then Oxlint with `.oxlintrc.json`, then `guard.mjs`. Biome formats and runs
its built-in rules. Oxlint runs only the rules that this folder owns: `.oxlintrc.json` turns off
every Oxlint category. Until #899 these rules were Grit plugins in Biome, which took 12 s or more
for a lint; Oxlint runs them in about 1 s.

## Effect rules

`.oxlintrc.json` loads [eslint-plugin-react-you-might-not-need-an-effect](https://github.com/nickjvandyke/eslint-plugin-react-you-might-not-need-an-effect)
through `jsPlugins`, and sets each of its rules to `error`. They flag effects that should be
render-time code, `useState` initializers, event handlers, `key` resets or `useSyncExternalStore`.
See [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect).

### When a rule fires

Fix the code. The diagnostic links the React page that shows the replacement: compute during
render, initialize in `useState`, act in the event handler, reset with `key`, or use
`useSyncExternalStore`. Moving the code into a helper or an alias until the rule goes quiet is not
a fix: the rules can't see through that, and review treats it as a suppression.

Do not suppress a rule yourself. `guard.mjs` runs in `pnpm lint` and fails on any `oxlint-disable`
or `eslint-disable` comment unless the user approved it in `APPROVED`. It also fails on each of
these, since each can switch a rule off:

- a blanket `biome-ignore lint` (also `-all` or `-start`), which turns off every Biome rule;
- a change to `biome.json` or `.oxlintrc.json` (pinned by hash);
- any other Biome or Oxlint config under `web/`;
- a change to the `lint` script;
- a `.eslintignore` in or above `web/`, or a tracked source file that a `.gitignore` rule matches (Oxlint skips both).

If you believe a hit is a false alarm, or a config needs a change, stop and ask the user, with the
code and why.

`no-reset-all-state-on-prop-change` runs from `oxlint/reset-all-state.mjs`, not from the plugin. The
plugin counts every read of a state value as a `useState`, so it never flags a component that
returns its state. Our rule counts the `useState` calls.

`effect-rules.test.ts` runs the rules through the real `.oxlintrc.json`. Add a case there when a
rule changes.

## Oxlint rules (`oxlint/`)

`oxlint/index.mjs` is a local Oxlint plugin named `zenbree`. `.oxlintrc.json` loads it through
`jsPlugins`. Each rule keeps the name of the Grit rule it replaced, and `guard.mjs` fails when a rule
in the plugin is not set to `error`.

## Tailwind values (`oxlint/tailwind-scale.mjs`)

Flags every arbitrary value in a class string: `utility-[value]`, `[property:value]` and the
`utility-(--variable)` shorthand. Colors (`#hex`, `rgb(`, `oklch(`, `var(--color-…)`, `color:`) and a
bare `var()` are always flagged, so `text-[var(--x)]` and `text-(--x)` fail: the theme token already
generates `text-x`. Arbitrary variants (`data-[state=open]:`, `has-[…]:`, `[&_svg]:`) are not values
and are not flagged, except a viewport breakpoint: `min-[760px]:` and `max-[759px]:` fail, because
the layout has one breakpoint and it is `phone:` / `desktop:` (`app/globals.css`). The rule keys on the utility shape inside string literals, so `messages[idx]`,
regexes and `f(--x)` are left alone.

These stay allowed, because no scale step or token can express them:

- `calc()`, `min()`, `max()`, `clamp()`, `env()` expressions, such as `pb-[max(1rem,env(safe-area-inset-bottom))]`. A `var()` is fine inside one.
- Viewport units (`h-[85dvh]`): the scale has no viewport steps.
- `grid-cols-[…]` and `grid-rows-[…]`: track lists are layouts, not values.
- `transition-[…]`: a property list, not a value.

### When it fires

Use a scale step (`pt-[18px]` is `pt-4.5`), an existing token's utility, or a stock utility. A new
`@theme` token is for a reused design role. A one-off value snaps to the nearest step: do not move it
into a token or `@utility` to silence the rule, and add no `oxlint-disable`. Suppressions go through
`guard.mjs` like the effect rules.

`lint/gen-tailwind-scale.mjs` generates the regexes in `tailwind-scale.patterns.json`; edit the
generator, not the output. They spell the allowlist as the complement of a prefix trie, because the
Grit version of the rule had no look-ahead. `oxlint/tailwind-scale.mjs` tests them on the source
text of each string literal and the raw text of each template chunk, and skips a module path and a
string type. `landing/lint/check.mjs` reads the same JSON to apply this rule to the landing, which
has no Oxlint and whose `.astro` files hold static `class="…"` attributes. The landing has its own
theme file, `landing/src/styles/theme.css`, and its breakpoints are `phone:` and `tablet:`.
`tailwind-scale.test.ts` lints the failing and passing strings in `tailwind-scale.probes.json`
through the real `.oxlintrc.json`, and probes each node shape. Add a case there when the allowlist
changes.

## Dates in the user's zone (`date-time-zone`)

Flags every `toLocaleDateString` and `toLocaleTimeString` call, and every `Intl.DateTimeFormat` whose
arguments name no `timeZone`. These calls format in the zone of the process, which is UTC in the web
container, so a server render shows UTC times and days to every user (#479). The zone read
`Intl.DateTimeFormat().resolvedOptions()` is allowed. `no-restricted-properties` in `.oxlintrc.json`
flags the two methods, and `oxlint/date-time-zone.mjs` flags an `Intl.DateTimeFormat` without
`timeZone`. Its header lists its gaps.

### When it fires

Format through next-intl, which carries the user's zone: `useFormatter()` in a client component or
`getFormatter()` on the server, then `format.dateTime(date, options)`. Code that needs a raw
`Intl.DateTimeFormat`, such as a pure helper or `formatToParts`, passes `timeZone`. Pass the user's
zone from `useTimeZone()` or `getTimeZone()`. Pass a fixed zone only when the zone is part of the
data, as with the UTC calendar days of the costs chart. Suppressions go through `guard.mjs` like the
other rules.

Vitest runs in `TZ=UTC` (`vite.config.ts`), like the container, so a test that formats in the
process zone sees UTC, not the zone of the laptop. `date-time-zone.test.ts` lints its probes
through the real `.oxlintrc.json`. Add a case there when the rule changes.
