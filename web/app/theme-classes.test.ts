import { globSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { compile } from 'tailwindcss';
import { expect, it } from 'vite-plus/test';

// Tailwind drops a class whose colour is not in the theme without a warning: `bg-side` after
// the palette lost `side` renders transparent and nothing fails. Compiling every colour class
// the components use against theme.css turns that into a test failure.

const require = createRequire(import.meta.url);
const web = path.join(__dirname, '..');

const COLOUR_CLASS =
  /(?<![\w/.-])(?:bg|text|border(?:-[trblxyse])?|ring-offset|ring|outline|fill|stroke|from|to|via|divide|decoration|accent|caret|placeholder)-[a-z][\w-]*(?:\/\d+)?(?![\w-])/g;

function classesInSource(): Map<string, string> {
  const found = new Map<string, string>();
  const files = globSync(['app/**/*.tsx', 'components/**/*.{ts,tsx}'], { cwd: web }).filter(
    (file) => !/\.test\.tsx?$/.test(file),
  );
  for (const file of files) {
    const code = readFileSync(path.join(web, file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|\s)\/\/.*$/gm, '$1');
    for (const [name] of code.matchAll(COLOUR_CLASS)) {
      if (!found.has(name)) found.set(name, file);
    }
  }
  return found;
}

async function compiledCss(candidates: string[]): Promise<string> {
  const theme = readFileSync(path.join(__dirname, 'theme.css'), 'utf8').replace(
    '@import "tw-animate-css";',
    '',
  );
  const compiler = await compile(`@import "tailwindcss";\n${theme}`, {
    base: web,
    loadStylesheet: async (id) => {
      const file = require.resolve(id === 'tailwindcss' ? 'tailwindcss/index.css' : id);
      return { path: file, base: path.dirname(file), content: readFileSync(file, 'utf8') };
    },
  });
  return compiler.build(candidates);
}

it('every colour class in the components resolves to a theme colour', async () => {
  const classes = classesInSource();
  const css = await compiledCss([...classes.keys()]);
  const selector = (name: string) => `.${name.replace(/[/.]/g, (ch) => `\\${ch}`)}`;
  const dropped = [...classes].filter(([name]) => !css.includes(selector(name)));
  expect(dropped.map(([name, file]) => `${name} (${file})`)).toEqual([]);
});
