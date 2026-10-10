const test = require('node:test')
const assert = require('node:assert/strict')
const B = require('../BrowserIdentity.js')
const P = require('../Preferences.js')

test('native app names and image paths cannot masquerade as browsers', () => {
  for (const row of [
    {app:'Chromecast Controller',desktopEntry:'chromecast'},
    {app:'Chat',appIcon:'/home/chrome/.cache/chat.png'},
    {app:'Chat',appIcon:'file:///tmp/firefox-avatar.png'},
    {app:'Bravery',desktopEntry:'bravery'}
  ]) assert.equal(B.browser(row),'')
  assert.equal(B.browser({app:'Google Chrome'}),'chrome')
  assert.equal(B.browser({app:'Browser',desktopEntry:'com.google.Chrome'}),'chrome')
  assert.equal(B.browser({app:'Browser',appIcon:'vivaldi-stable'}),'vivaldi')
})

test('only a browser leading origin identifies a website', () => {
  const row = body => ({app: 'Vivaldi', body})
  assert.equal(B.hostname(row(' TEAMS.MICROSOFT.COM\r\nNew message')), 'teams.microsoft.com')
  assert.equal(B.hostname(row('<a href="https://teams.microsoft.com/chat">Teams</a>\nNew message')), 'teams.microsoft.com')
  assert.equal(B.hostname(row('https://mail.example.com/\nNew message')), 'mail.example.com')
  for (const body of ['Message https://example.com', 'Message\nexample.com', 'example.com extra text', 'https://user@example.com', 'https://example.com:443/', '<a href="javascript:alert(1)">x</a>', 'bad..example.com', 'a'.repeat(64)+'.com'])
    assert.equal(B.hostname(row(body)), '', body)
  assert.equal(B.hostname({app:'Chat',body:'example.com\nMessage'}), '')
  assert.equal(B.hostname({app:'Firefox',body:'https://example.com/\nMessage'}), 'example.com')
})

test('favicon and browser grouping settings validate without changing duplicate semantics', () => {
  const config = extra => JSON.stringify({plugins:[{id:'foamy.notifications',...extra}]})
  assert.equal(P.parse(config({})).settings.useBrowserFavicons, true)
  assert.equal(P.parse(config({})).settings.browserGrouping, 'browser')
  assert.equal(P.parse(config({useBrowserFavicons:false,browserGrouping:'hostname'})).settings.browserGrouping, 'hostname')
  assert.match(P.parse(config({useBrowserFavicons:'false'})).error, /Invalid useBrowserFavicons/)
  assert.match(P.parse(config({browserGrouping:'url'})).error, /Invalid browserGrouping/)
  const row = (id,app='Vivaldi') => ({app,body:'example.com\nMessage',summary:'Hello',timestamp:1000,originalId:id})
  assert.equal(P.groups([row(1),row(2)],true,'browser').length, 1)
  assert.equal(P.groups([row(1),row(2)],true,'hostname').length, 1)
  assert.equal(P.groups([row(1),row(2)],true,'none').length, 2)
  assert.equal(P.groups([row(1,'Chat'),row(2,'Chat')],true,'none').length, 1)
  assert.equal(P.groups([row(1),{...row(2),body:'other.example.com\nMessage'}],true,'hostname').length, 2)
})
