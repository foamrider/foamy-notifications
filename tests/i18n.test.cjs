const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const I = require('./i18n.cjs');
const manifest = require('../manifest.json');
const tokens = s => [...s.matchAll(/%[1-9][0-9]*|\{\w+\}/g)].map(m => m[0]).sort();
for (const [key, value] of Object.entries(I.en)) {
  assert.equal(value, key, `English source key: ${key}`);
  if (Object.hasOwn(I.no, key)) assert.deepEqual(tokens(I.no[key]), tokens(value), key);
}
assert.equal(manifest.barWidget?.defaults?.language, undefined);
assert.equal((manifest.barWidget?.schema || []).some(f => f.key === 'language'), false);
assert.equal(I.text('Unknown %1 {name}', 'no', ['$& %2']), '$& %2 {name}'.replace(/^/, 'Unknown '));
assert.equal(I.text('constructor', 'no'), 'constructor');
const ui = fs.readFileSync(path.join(__dirname, '../kit/FoamyI18n.qml'), 'utf8');
assert.ok(ui.includes('Qt.createQmlObject'), '4.0 must not statically require a 4.5 QML type');
// Every literal display key needs an English source; other catalogs may fall back.
for (const directory of ['..', '../components']) {
  for (const name of fs.readdirSync(path.join(__dirname, directory)).filter(name => name.endsWith('.qml'))) {
    const source = fs.readFileSync(path.join(__dirname, directory, name), 'utf8');
    for (const match of source.matchAll(/FoamyI18n\.t\("([^"]+)"/g)) {
      assert.ok(Object.hasOwn(I.en, match[1]), `${name}: missing English key ${match[1]}`);
    }
  }
}
