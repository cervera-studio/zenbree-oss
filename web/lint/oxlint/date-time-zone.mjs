// date-time-zone: the Intl.DateTimeFormat half of the Grit rule of the same name. `no-restricted-properties` in
// .oxlintrc.json flags toLocaleDateString and toLocaleTimeString with the same message.
// Flags `Intl.DateTimeFormat(...)` and `new Intl.DateTimeFormat(...)` whose arguments hold no
// `timeZone` member, except the zone read `Intl.DateTimeFormat().resolvedOptions()`.
// Gaps, as in the Grit rule: `timeZone: undefined` passes, since any `timeZone` member does;
// aliases such as `const { DateTimeFormat } = Intl` are missed; options passed through a variable or
// a spread are flagged even when they hold a `timeZone`.

const MESSAGE =
  "Date formatted in the process zone (UTC in the container), not in the user's. Format it through next-intl, which carries the user's zone: 'useFormatter()' in a client component, 'getFormatter()' on the server. Replace 'toLocaleDateString' and 'toLocaleTimeString' with the formatter. A raw Intl.DateTimeFormat names its 'timeZone': the user's from 'useTimeZone()' or 'getTimeZone()', or a fixed zone only when the zone is part of the data, such as a UTC calendar day.";

const isDateTimeFormat = (callee) =>
  callee.type === 'MemberExpression' &&
  !callee.computed &&
  callee.object.type === 'Identifier' &&
  callee.object.name === 'Intl' &&
  callee.property.name === 'DateTimeFormat';

/** Every node under `node`, depth first. */
function* walk(node) {
  for (const [key, value] of Object.entries(node)) {
    if (key === 'parent' || !value || typeof value !== 'object') continue;
    for (const child of Array.isArray(value) ? value : [value]) {
      if (!child || typeof child.type !== 'string') continue;
      yield child;
      yield* walk(child);
    }
  }
}

const isTimeZoneMember = (n) =>
  n.type === 'Property' &&
  !n.computed &&
  ((n.key.type === 'Identifier' && n.key.name === 'timeZone') ||
    (n.key.type === 'Literal' && n.key.value === 'timeZone'));

/** `Intl.DateTimeFormat().resolvedOptions()`: reads the zone, formats nothing. */
const readsZone = (node) =>
  node.arguments.length === 0 &&
  node.parent?.type === 'MemberExpression' &&
  node.parent.object === node &&
  node.parent.property.name === 'resolvedOptions' &&
  node.parent.parent?.type === 'CallExpression' &&
  node.parent.parent.callee === node.parent;

export default {
  meta: {
    type: 'problem',
    docs: { description: 'Disallow an Intl.DateTimeFormat that formats in the process zone.' },
    schema: [],
  },
  create(context) {
    const check = (node) => {
      if (!isDateTimeFormat(node.callee) || readsZone(node)) return;
      if (node.arguments.some((arg) => [arg, ...walk(arg)].some(isTimeZoneMember))) return;
      context.report({ node, message: MESSAGE });
    };
    return { CallExpression: check, NewExpression: check };
  },
};
