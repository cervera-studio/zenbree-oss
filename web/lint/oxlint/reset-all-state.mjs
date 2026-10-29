// no-reset-all-state-on-prop-change: a port of the Grit rule of the same name. The
// plugin's own rule counts every read of a state value as a useState, so a component that returns
// its state (all of them) is never flagged; this one counts the useState calls, as the Grit rule did.
// Flags a useEffect whose deps name a prop of an enclosing component and whose callback only calls
// useState setters with that state's own initial value (the same syntax tree as the useState
// argument), covering every useState of that component. Reported on the useEffect call.
// Known gaps, as in the Grit rule: props are recognised by name from the parameter list only;
// "every useState is reset" is checked by setter name; a custom hook is skipped only as
// `function useX`, found by the Grit rule's regex on the source text. The Grit rule also meant to
// skip `const useX =`, but that pattern never matched: it flagged a hook arrow and a component
// nested in one, and so does this rule.

const SETTER = /^set[A-Z]\w*$/;
// The Grit rule's test on the source text: it misses `function*` and a comment before the name.
const HOOK = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+use[A-Z]/;
const FUNCTION = new Set(['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression']);

/** `name(...)` or `React.name(...)`. */
const isReactCall = (node, name) =>
  node?.type === 'CallExpression' &&
  ((node.callee.type === 'Identifier' && node.callee.name === name) ||
    (node.callee.type === 'MemberExpression' &&
      !node.callee.computed &&
      node.callee.object.type === 'Identifier' &&
      node.callee.object.name === 'React' &&
      node.callee.property.name === name));

/** Every node under `node`, depth first; `skip(child)` stops the descent into a child. */
function* walk(node, skip = () => false) {
  for (const [key, value] of Object.entries(node)) {
    if (key === 'parent' || !value || typeof value !== 'object') continue;
    for (const child of Array.isArray(value) ? value : [value]) {
      if (!child || typeof child.type !== 'string' || skip(child)) continue;
      yield child;
      yield* walk(child, skip);
    }
  }
}

// Source positions and the parent link, which differ between two equal pieces of code.
const POSITION = new Set(['parent', 'start', 'end', 'range', 'loc']);

/** The same code, compared as syntax trees: layout, comments and a trailing comma do not count. */
function same(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  const keys = Object.keys(a).filter((k) => !POSITION.has(k));
  return (
    keys.length === Object.keys(b).filter((k) => !POSITION.has(k)).length &&
    keys.every((k) => same(a[k], b[k]))
  );
}

const self = (node, skip) => [node, ...walk(node, skip)];

/** Identifiers used as values: not the name in `a.name`. */
const identifierNames = (nodes, skip) =>
  new Set(
    nodes.flatMap((n) =>
      self(n, skip)
        .filter(
          (c) =>
            c.type === 'Identifier' &&
            !(
              c.parent?.type === 'MemberExpression' &&
              c.parent.property === c &&
              !c.parent.computed
            ),
        )
        .map((c) => c.name),
    ),
  );

const isHook = (fn, text) => HOOK.test(text(fn));

/** setter name -> the useState argument, or null when it has none. */
function stateSetters(body) {
  const states = new Map();
  for (const n of self(body)) {
    if (n.type !== 'VariableDeclarator' || n.id.type !== 'ArrayPattern') continue;
    const setter = n.id.elements[1];
    if (setter?.type !== 'Identifier' || !isReactCall(n.init, 'useState')) continue;
    states.set(setter.name, n.init.arguments[0] ?? null);
  }
  return states;
}

const isUndefined = (node) => node?.type === 'Identifier' && node.name === 'undefined';

function resets(call, states) {
  if (!states.has(call.callee.name)) return false;
  const initial = states.get(call.callee.name);
  const [arg] = call.arguments;
  if (call.arguments.length === 0) return initial === null || isUndefined(initial);
  return same(arg, initial) || (initial === null && isUndefined(arg));
}

/** The effect resets all the state of `component` when one of its props changes. */
function resetsAll(component, fn, deps, text) {
  if (isHook(component, text)) return false;
  const props = identifierNames(component.params, (c) => c.type === 'TSTypeAnnotation');
  if (![...identifierNames([deps])].some((name) => props.has(name))) return false;

  const states = stateSetters(component.body);
  const setterCalls = (skip) =>
    self(fn.body, skip).filter(
      (n) =>
        n.type === 'CallExpression' && n.callee.type === 'Identifier' && SETTER.test(n.callee.name),
    );
  const synchronous = setterCalls((c) => FUNCTION.has(c.type));
  if (!synchronous.some((c) => states.has(c.callee.name))) return false;

  const calls = setterCalls(() => false);
  if (!calls.filter((c) => c.arguments.length <= 1).every((c) => resets(c, states))) return false;
  const called = new Set(calls.map((c) => c.callee.name));
  return [...states.keys()].every((setter) => called.has(setter));
}

/** Like Grit's `within`, any enclosing function can be the component. */
function flagged(effect, text) {
  const [fn, deps] = effect.arguments;
  if (fn?.type !== 'ArrowFunctionExpression' || deps?.type !== 'ArrayExpression') return false;
  for (let n = effect.parent; n; n = n.parent)
    if (FUNCTION.has(n.type) && resetsAll(n, fn, deps, text)) return true;
  return false;
}

export default {
  meta: {
    type: 'suggestion',
    docs: {
      description: 'Disallow resetting all state in an effect when a prop changes.',
      url: 'https://react.dev/learn/you-might-not-need-an-effect#resetting-all-state-when-a-prop-changes',
    },
    schema: [],
  },
  create(context) {
    const text = (node) => context.sourceCode.getText(node);
    return {
      CallExpression(node) {
        if (isReactCall(node, 'useEffect') && flagged(node, text))
          context.report({
            node,
            message:
              "Avoid resetting all state when a prop changes. Instead, if the prop is a key, pass it as 'key' so React will reset the component's state. See https://react.dev/learn/you-might-not-need-an-effect",
          });
      },
    };
  },
};
