const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const en = require('../i18n/en.json');
const no = require('../i18n/no.json');
const FoamyNative = require('../kit/node-loader.cjs').load('FoamyNative');
const tokens = s => [...s.matchAll(/%[1-9][0-9]*|\{\w+\}/g)].map(m => m[0]).sort();

test('every English key maps to itself; Norwegian has every key, translated, with the same placeholders', () => {
  // A missing Norwegian entry shows English to a Norwegian user.
  assert.deepEqual(Object.keys(no).sort(), Object.keys(en).sort());
  for (const key of Object.keys(en)) {
    assert.equal(en[key], key, `English source key: ${key}`);
    assert.ok(typeof no[key] === 'string' && no[key].trim() !== '', `empty Norwegian text: ${key}`);
    assert.deepEqual(tokens(no[key]), tokens(key), key);
  }
});

test('every string the plugin shows has an English entry', () => {
  const keys = new Map();
  const add = (key, where) => keys.set(key, keys.get(key) || where);
  const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  // Literal keys in the views.
  for (const directory of ['.', 'components']) {
    for (const name of fs.readdirSync(path.join(__dirname, '..', directory)).filter(name => name.endsWith('.qml'))) {
      const file = path.join(directory, name);
      for (const match of read(file).matchAll(/FoamyI18n\.t\("([^"]+)"/g)) add(match[1], file);
    }
  }
  const literals = keys.size;
  assert.ok(literals >= 17, `the FoamyI18n.t scan found only ${literals} keys`);
  // Strings that reach the card as data: action errors go through
  // FoamyI18n.t(root.error), the placeholder row through FoamyI18n.t(summary).
  // Each line's literals, in order; only those with a space are texts, which
  // skips "" and the "browser" reason.
  const service = read('Service.qml');
  const errors = new Set();
  for (const line of service.split('\n').filter(line => line.includes('finishAction('))) {
    for (const match of line.matchAll(/"((?:[^"\\]|\\.)*)"/g)) if (match[1].includes(' ')) errors.add(match[1]);
  }
  assert.ok(errors.size >= 8, `found only ${errors.size} finishAction errors`);
  for (const error of errors) add(error, 'Service.qml finishAction');
  const summaries = [...service.matchAll(/summary: "([^"]+)"/g)].map(match => match[1]);
  assert.ok(summaries.length >= 1, 'found no placeholder summary');
  for (const summary of summaries) add(summary, 'Service.qml summary');
  add(FoamyNative.UNAVAILABLE_TEXT, 'FoamyNative.UNAVAILABLE_TEXT');
  for (const [key, where] of keys) assert.ok(Object.hasOwn(en, key), `${where}: missing English key ${key}`);
});
