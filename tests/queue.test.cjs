const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const Logic = require('../NotificationLogic.js')
const source = fs.readFileSync(path.join(__dirname, '../Service.qml'), 'utf8')
function load(state, name) {
  const start = source.indexOf('  function ' + name + '(')
  assert.notEqual(start, -1)
  vm.runInContext(source.slice(start, source.indexOf('\n  }', start) + 4), state)
}
function fixture() {
  const state = vm.createContext({NotificationLogic:Logic,Features:require("./qml-js.cjs")("Features.js"), popupFileQueue:[], helper:'/helper', lastError:'',
    console:{warn(){}}, runNextPopupFileJob(){}})
  state.service = state
  for (const name of ['popupQueueLimit', 'popupWriteLimit', 'popupQueueByteLimit', 'popupWriteByteLimit', 'popupPayloadLimit'])
    state[name] = Number(source.match(new RegExp('readonly property int ' + name + ': ([0-9]+)'))[1])
  for (const name of ['reportPersistenceError', 'rejectPopupFileJob', 'enqueuePopupJob', 'enqueueHelperJob', 'cancelPendingWrites']) load(state, name)
  // Raw payload strings stand in for serialized job data (the helper protocol carries JSON).
  state.put = (payload, key='1-1', done=null, history=false) => state.enqueuePopupJob({op:'write', payload, done, replaceKey:key, historyWrite:history, writeKey:key})
  state.enqueueHistoryRead = () => state.enqueueHelperJob('read', {history:true})
  return state
}
function bytes(state) { return state.popupFileQueue.reduce((sum, job) => sum + job.bytes, 0) }

test('blocked worker cannot retain an unbounded distinct-sender backlog', () => {
  const s = fixture()
  let rejected = 0
  for (let i=0; i<10000; i++) if (!s.put(JSON.stringify({appIcon:'/tmp/' + i + 'x'.repeat(4090)}), '1-' + i)) rejected++
  assert.equal(s.popupFileQueue.length, s.popupWriteLimit)
  assert.equal(rejected, 10000-s.popupWriteLimit)
  assert.ok(bytes(s) <= s.popupWriteByteLimit)
  assert.match(s.lastError, /queue is full/)
})

test('UTF-8, escape expansion and per-packet limits apply before retention; failures complete once', () => {
  const s = fixture()
  for (const text of ['ascii', 'å中🙂', '\ud800', '\udfff', '\0', '🙂'.repeat(20)]) assert.equal(Logic.utf8Bytes(text), Buffer.byteLength(text))
  let failed = 0
  assert.equal(s.put(JSON.stringify({body:'é'.repeat(70000)}), '1-1', ok => { assert.equal(ok, false); failed++ }), false)
  assert.equal(s.put(JSON.stringify({body:'\0'.repeat(25000)}), '1-2', ok => { assert.equal(ok, false); failed++ }), false)
  assert.equal(s.popupFileQueue.length, 0)
  assert.equal(failed, 2)
  for(let i=0; i<100; i++) s.put('🙂'.repeat(20000), '1-' + i)
  assert.ok(s.popupFileQueue.length < s.popupWriteLimit)
  assert.ok(bytes(s) <= s.popupWriteByteLimit)
})

test('writes cannot consume action/cleanup reserves, and every kind of work shares the hard bounds', () => {
  const s = fixture()
  for(let i=0; i<s.popupWriteLimit; i++) assert.equal(s.put('small', '1-' + i), true)
  for(let i=s.popupWriteLimit; i<s.popupQueueLimit; i++) assert.equal(s.enqueueHelperJob('delete', {key:'1-1'}), true)
  assert.equal(s.popupFileQueue.length, s.popupQueueLimit)
  let failed = 0
  assert.equal(s.enqueueHelperJob('focus', {}, ok => { assert.equal(ok, false); failed++ }), false)
  assert.equal(s.enqueueHistoryRead(), false)
  assert.equal(s.enqueueHelperJob('read', {}), false)
  assert.equal(failed, 1)
  assert.equal(s.popupFileQueue.length, s.popupQueueLimit)
  assert.ok(bytes(s) <= s.popupQueueByteLimit)
})

test('coalescing and cancellation preserve callback, read and cleanup barriers', () => {
  const s = fixture()
  s.put('old'); s.put('new')
  assert.equal(s.popupFileQueue.length, 1)
  assert.equal(s.popupFileQueue[0].payload, 'new')
  s.enqueueHistoryRead(); s.put('after read')
  s.enqueueHelperJob('delete', {key:'2-2'}); s.put('after delete')
  let cancelled = 0
  s.put('callback', '1-1', ok => { assert.equal(ok, false); cancelled++ }, true)
  s.put('after callback')
  assert.equal(s.popupFileQueue.length, 7)
  s.cancelPendingWrites(['1-1'], true)
  assert.equal(cancelled, 1)
  assert.equal(s.popupFileQueue.length, 6)
  s.cancelPendingWrites(['1-1'])
  assert.equal(cancelled, 1)
  assert.equal(s.popupFileQueue.length, 2)
  assert.equal(s.popupFileQueue[0].op, 'read')
  assert.equal(s.popupFileQueue[1].op, 'delete')
})

test('draining a write releases its pending slot and bytes; a read remains a barrier', () => {
  const s = fixture()
  const sent = []
  s.helperProc = {running:true, write(line) { sent.push(JSON.parse(line)) }}; s.helperRestart = {running:false, start() {}}
  s.runningJob = null; s.nextJobId = 0; s.acceptNormalizedImages = () => {}
  load(s, 'runNextPopupFileJob'); load(s, 'helperReplied')
  s.put('"first"'); s.enqueueHistoryRead(); s.put('"last"', '1-2')
  s.runNextPopupFileJob()
  assert.equal(sent[0].data, 'first')
  assert.equal(s.popupFileQueue.length, 2)
  // One job in flight: nothing else is sent until the helper answers.
  s.runNextPopupFileJob(); assert.equal(sent.length, 1)
  s.helperReplied(JSON.stringify({id:sent[0].id, ok:true, out:''}))
  assert.equal(sent[1].op, 'read')
  assert.equal(s.popupFileQueue.length, 1)
  s.helperReplied(JSON.stringify({id:999, ok:true, out:''})); assert.equal(sent.length, 2)
  s.helperReplied(JSON.stringify({id:sent[1].id, ok:true, out:''}))
  assert.equal(sent[2].data, 'last')
  assert.equal(bytes(s), 0)
})

test('rejected DND writes release their live object without catch-up recursion or callback retention', () => {
  const s = fixture()
  let writes=0, released=0
  s.writeHistoryFile = (row, done) => { writes++; done(false); return false }
  s.releaseSilenced = () => { released++ }
  load(s, 'writeSilenced')
  s.writeSilenced({summary:'newer'}, {originalId:1, summary:'older'})
  assert.equal(writes, 1); assert.equal(released, 1)
})

test('popup producers roll back rejected image generations and history producers carry cancellation identity', () => {
  const s = fixture()
  s.imagesDir='/tmp/images/'; s.nextImageWriteVersion=0; s.imageWriteVersions={}
  load(s, 'persistPopupFile'); load(s, 'writeHistoryFile')
  const row={originalId:1,timestamp:1000,app:'Chat',appIcon:'chat',body:'\0'.repeat(25000)}
  assert.equal(s.persistPopupFile(row), false)
  assert.deepEqual(Object.keys(s.imageWriteVersions), [])
  row.body='hello'
  assert.equal(s.writeHistoryFile(row), true)
  assert.equal(s.popupFileQueue[0].writeKey, '1000-1')
  assert.equal(s.popupFileQueue[0].historyWrite, true)
})

test('oversized fields/actions are rejected at intake and replacements without copying full metadata', () => {
  const n={appName:'Chat',appIcon:'chat',summary:'Hi',body:'Hello',desktopEntry:'chat',hints:{},actions:[]}
  assert.equal(Logic.notificationWithinLimits(n), true)
  for(const [name, length] of Object.entries({appName:257,appIcon:4097,summary:4097,body:16385,desktopEntry:513,image:12289}))
    assert.equal(Logic.notificationWithinLimits({...n,[name]:'x'.repeat(length)}), false, name)
  for(const [name,length] of Object.entries({'image-path':4097,'image_path':4097,'omarchy-glyph':65,'omarchy-exec-argv':8193}))
    assert.equal(Logic.notificationWithinLimits({...n,hints:{[name]:'x'.repeat(length)}}), false, name)
  assert.equal(Logic.notificationWithinLimits({...n,actions:Array(33).fill({identifier:'reply',text:'Reply'})}), false)
  assert.equal(Logic.notificationWithinLimits({...n,actions:[{identifier:'x'.repeat(257)}]}), false)
  assert.equal(Logic.notificationWithinLimits({...n,actions:[{identifier:'reply',text:'x'.repeat(513)}]}), false)
  const snapshot=Logic.snapshotOf({...n,appIcon:'x'.repeat(1000000),hints:{'image-path':'/'+'x'.repeat(1000000),'omarchy-exec-argv':'x'.repeat(1000000)}})
  assert.equal(snapshot.appIcon, ''); assert.equal(snapshot.image, ''); assert.equal(snapshot.execArgv, '')
})

test('history writes have cancellation keys but never expect a live-image result', () => {
  const s=fixture()
  s.helperProc={running:true, write() {}}; s.helperRestart={running:false, start() {}}; s.runningJob=null; s.nextJobId=0
  load(s, 'runNextPopupFileJob')
  s.put('{}', '1-1', () => {}, true)
  s.runNextPopupFileJob()
  assert.equal(s.runningImageWrite, false)
})

test('a sender closed during a DND write does not strand its callback', () => {
  const s=fixture()
  let released=0
  const notification={}
  Object.defineProperty(notification, 'appName', {get(){throw Error('QObject already destroyed')}})
  s.writeHistoryFile=(row, done)=>{done(true); return true}
  s.retainHistoryAction=()=>false
  s.releaseSilenced=()=>{released++}
  load(s, 'writeSilenced')
  s.writeSilenced(notification,{originalId:1})
  assert.equal(released,1)
})

test('popup and history writes with the same identity cannot replace each other', () => {
  const s=fixture()
  s.put('popup')
  s.put('history','1-1',null,true)
  assert.equal(s.popupFileQueue.length,2)
  assert.equal(s.popupFileQueue[0].historyWrite,false)
  assert.equal(s.popupFileQueue[1].historyWrite,true)
})

test('the hard byte budget also bounds control jobs before the job-count limit', () => {
  const s=fixture()
  for(let i=0;i<6;i++) assert.equal(s.put('x'.repeat(131000),'1-'+i),true)
  assert.equal(s.enqueuePopupJob({op:'focus',payload:'x'.repeat(131000)}),true)
  assert.equal(s.enqueuePopupJob({op:'focus',payload:'x'.repeat(131000)}),true)
  let failed=0
  assert.equal(s.enqueuePopupJob({op:'focus',payload:'x'.repeat(131000),done:ok=>{assert.equal(ok,false);failed++}}),false)
  assert.equal(s.popupFileQueue.length,8)
  assert.ok(bytes(s)<=s.popupQueueByteLimit)
  assert.equal(failed,1)
})

test('live-source and compositor backlogs reject new native objects without extending retention', () => {
  const s=fixture()
  s.liveRefs=Object.fromEntries(Array.from({length:256},(_,i)=>[i+1,{}]))
  s.pendingNotifications=Array(100).fill({})
  s.preferences={suppressFullscreen:true,monitor:'focused'}
  const errors=[]
  s.rejectNotification=(n,message)=>{n.tracked=false;errors.push(message)}
  load(s,'handleNotification'); load(s,'receiveNotification')
  const live={id:300,summary:'normal',tracked:false}
  s.handleNotification(live)
  assert.equal(live.tracked,false)
  assert.equal(Object.keys(s.liveRefs).length,256)
  const pending={id:301,summary:'normal',tracked:false,imageChanged:{connect(){}}}
  s.receiveNotification(pending)
  assert.equal(pending.tracked,false)
  assert.equal(s.pendingNotifications.length,100)
  assert.equal(errors.length,2)
})
