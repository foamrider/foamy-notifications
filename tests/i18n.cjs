// Catalog lookups for pure model tests through the kit's FoamyI18nText.js,
// the substitution code FoamyI18n.qml itself calls.
const I18n = require('../kit/node-loader.cjs').load('FoamyI18nText');
const en = require('../i18n/en.json');
const no = require('../i18n/no.json');
const number = (v, d) => (d === undefined ? String(v) : Number(v).toFixed(d));
function text(key, language, values) {
  return I18n.translator(language === 'no' || language === 'nb' ? no : en, en, number).t(key, values);
}
module.exports = {text, no, en, norwegian: no, nb: no};
