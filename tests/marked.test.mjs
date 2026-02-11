import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const markedModulePath = path.resolve(__dirname, '../server/public/vendor/marked.esm.js');
const { marked } = await import(pathToFileURL(markedModulePath));

test('marked renders headings and lists', () => {
  const markdown = '# Title\n\n- first\n- second';
  const html = marked.parse(markdown);

  assert.ok(html.includes('<h1>Title</h1>'), 'expected heading to render');
  assert.ok(html.includes('<ul>') && html.includes('<li>first</li>'), 'expected list items to render');
});

test('marked renders emphasis and code', () => {
  const markdown = '**bold** and `code` sample';
  const html = marked.parse(markdown);

  assert.ok(html.includes('<strong>bold</strong>'), 'expected bold text to render');
  assert.ok(html.includes('<code>code</code>'), 'expected inline code to render');
});

