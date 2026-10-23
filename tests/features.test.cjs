const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const load = require('./qml-js.cjs')
const features = load('Features.js')
const security = load('Security.js')
test('offers detect codes and meeting links without mistaking origins or build numbers for actions',()=>{
 assert.equal(features.offers({app:'Chat',summary:'Verification code: 482 913',body:''}).code,'482913')
 assert.equal(features.offers({app:'Chat',summary:'VS Code build 481234',body:'412 passed'}).code,'')
 assert.equal(features.offers({app:'Vivaldi',summary:'Hello',body:'<a href="https://teams.microsoft.com">Teams</a>\nMessage'}).link,'')
 assert.equal(features.offers({app:'Vivaldi',summary:'Meeting',body:'<a href="https://teams.microsoft.com">Teams</a>\nJoin https://meet.google.com/abc-defg-hij'}).meeting,true)
 assert.equal(features.offers({app:'Chat',summary:'Verification &lt;b&gt;482913&lt;/b&gt;',body:''}).code,'482913')
 assert.equal(features.offers({app:'Chat',summary:'OTP &#52;&#56;&#50;&#57;&#49;&#51;',body:''}).code,'482913')
})
test('redaction keeps identity but removes secrets from every persisted string and image hint',()=>{
 const row={timestamp:123,originalId:2,app:'482913',appIcon:'/tmp/482913.png',summary:'Your verification code',body:'482913',execArgv:'["open","482913"]',image:'/tmp/482913.png',actionsJson:'[]'}
 const saved=features.redact(row)
 assert.equal(saved.timestamp,123);assert.equal(saved.originalId,2)
 assert.doesNotMatch(JSON.stringify(saved),/482913/)
 assert.equal(saved.summary,'Verification code')
 assert.equal(row.body,'482913')
 assert.equal(features.redact({summary:'Build 482913',body:'Complete'}).body,'Complete')
})
test('external destinations reject command schemes, credentials, controls and encoded ambiguities',()=>{
 for(const value of ['file:///tmp/file','javascript:alert(1)','https://user:password@example.test','https://127.0.0.1','https://example.test/%250a','https://example.test/\nsecret','https://example.test/\\evil']) assert.equal(security.safeHttpUrl(value),'')
 assert.equal(security.safeHttpUrl('https://example.test/path?a=b'),'https://example.test/path?a=b')
})
test('snoozes expire and are bounded; source names come from leading browser identity only',()=>{
 assert.equal(features.source({app:'Vivaldi',body:'https://teams.microsoft.com\nHello'}),'web:teams.microsoft.com')
 assert.equal(features.source({app:'Chat',body:'https://teams.microsoft.com'}),'app:Chat')
 assert.deepEqual(JSON.parse(JSON.stringify(features.quietState({'*':2000,'app:Chat':3000,'app:Expired':999,'../escape':2000,'app:Far':100000000},1000))),{'*':2000,'app:Chat':3000})
 assert.equal(features.isSharing({'media.class':'Video/Source','media.name':'xdph-streaming-1'}),true)
 assert.equal(features.isSharing({'media.class':'Video/Source','media.name':'Camera'}),false)
})
test('source snooze archives matching cards and preserves unrelated cards and DND bypass',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8')
 const rows=[{app:'Chat',timestamp:1,originalId:1},{app:'Mail',timestamp:2,originalId:2}],hidden=[]
 const state=vm.createContext({Features:features,NotificationLogic:require('../NotificationLogic.js'),NotificationUrgency:{Critical:2},preferences:{dndSilenceCritical:false},doNotDisturb:false,snoozes:{},busyKeys:{},popupModel:{get:i=>rows[i],count:2},dismissPopup:i=>hidden.push(i),scheduleSettingsSave(){},Date})
 state.service=state
 for(const name of ['isQuiet','shouldArchiveSilenced','snoozeSource','shouldBypassDnd','resumeSnoozes']) {
  const start=source.indexOf('  function '+name+'(');vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),state)
 }
 assert.equal(state.snoozeSource('app:Chat',30),'ok');assert.deepEqual(hidden,[0])
 assert.equal(state.isQuiet(rows[0]),true);assert.equal(state.isQuiet(rows[1]),false)
 assert.equal(state.isQuiet({app:'omarchy-action',urgency:2}),false)
 state.resumeSnoozes();assert.equal(state.isQuiet(rows[0]),false)
 assert.equal(state.snoozeSource('app:Chat',-1),'invalid')
 state.preferences.dndSilenceCritical=true;state.snoozeSource('*',30)
 assert.equal(state.shouldArchiveSilenced({app:'notify-send',urgency:2}),true)
 assert.equal(state.shouldArchiveSilenced({app:'notify-send',urgency:2,transient:true}),false)
})
