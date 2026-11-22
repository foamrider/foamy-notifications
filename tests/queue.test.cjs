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
  const state = vm.createContext({NotificationLogic:Logic,Features:require("./qml-js.cjs")("Features.js"), popupFileQueue:[], helper:'/helper', lastError:'', helperUnavailable:false,
    FoamyNative:require('../kit/node-loader.cjs').load('FoamyNative'),
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
// Run a method of the FoamyIpc handler (4-space indent, typed parameters) in its
// own context: some names exist both there and in the Service it calls.
function loadIpc(service, name) {
  const start = source.indexOf('    function ' + name + '(')
  assert.notEqual(start, -1, name)
  const text = source.slice(start, source.indexOf('\n    }', start) + 6)
    .replace(/\)\s*:\s*\w+\s*\{/, ') {').replace(/(\w+)\s*:\s*(string|int)\b/g, '$1')
  const context = vm.createContext({service})
  vm.runInContext(text, context)
  return context[name]
}
// A helper process stub that records each job line it is sent.
function withHelper(state) {
  const sent = []
  state.helperProc = {running:true, write(line) { sent.push(JSON.parse(line)) }}; state.helperRestart = {running:false, start() {}}
  state.runningJob = null; state.nextJobId = 0; state.acceptNormalizedImages = () => {}; state.deferredMoves = {}
  load(state, 'runNextPopupFileJob'); load(state, 'helperReplied')
  return sent
}

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

test('a helper reply hands its failure reason to the job', () => {
  const s = fixture()
  const sent = withHelper(s), done = []
  s.popupFileQueue = [{op:'focus', payload:'{}', done:(...args) => done.push(args)}]
  s.runNextPopupFileJob()
  s.helperReplied(JSON.stringify({id:sent[0].id, ok:false, out:'', error:'x', reason:'browser'}))
  assert.deepEqual(done, [[false, '', 'browser']])
})

test('draining a write releases its pending slot and bytes; a read remains a barrier', () => {
  const s = fixture()
  const sent = withHelper(s)
  s.put('"first"'); s.enqueueHistoryRead(); s.put('"last"', '1-2')
  s.runNextPopupFileJob()
  assert.equal(sent[0].data, 'first')
  assert.equal(s.popupFileQueue.length, 2)
  // A malformed reply is ignored: the job stays in flight and nothing else is sent.
  assert.doesNotThrow(() => s.helperReplied('{'))
  assert.equal(sent.length, 1)
  assert.notEqual(s.runningJob, null)
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

test('a refused popup write reports the limit; a history-only cancel ends the history write once and keeps the popup write', () => {
  const s = fixture()
  s.imagesDir='/tmp/images/'; s.nextImageWriteVersion=0; s.imageWriteVersions={}; s.waitingKeys={}; s.shownDeadlines={}
  load(s, 'persistPopupFile'); load(s, 'writeHistoryFile')
  const row={originalId:1,timestamp:1000,app:'Chat',appIcon:'chat',body:'\0'.repeat(25000)}
  assert.equal(s.persistPopupFile(row), false)
  assert.match(s.lastError, /128 KiB/)
  assert.equal(s.popupFileQueue.length, 0)
  row.body='hello'
  let done = 0
  assert.equal(s.persistPopupFile(row), true)
  assert.equal(s.writeHistoryFile(row, ok => { assert.equal(ok, false); done++ }), true)
  assert.equal(s.popupFileQueue.length, 2)
  s.cancelPendingWrites([Logic.imageStem(row)], true)
  assert.equal(done, 1)
  assert.equal(s.popupFileQueue.length, 1)
  assert.equal(s.popupFileQueue[0].op, 'write')
  assert.equal(JSON.parse(s.popupFileQueue[0].payload).history, undefined, 'the popup write was cancelled instead')
  s.cancelPendingWrites([Logic.imageStem(row)], true)
  assert.equal(done, 1, 'a cancelled write completed twice')
})

test('a refused rewrite keeps the image result of the write still queued for the same card', () => {
  const s = fixture()
  s.imagesDir='/tmp/images/'; s.nextImageWriteVersion=0; s.imageWriteVersions={}; s.waitingKeys={}; s.shownDeadlines={}; s.normalizedImages={}
  s.isRestoredRow = () => false
  const row={originalId:1,timestamp:1000,app:'Chat',appIcon:'foot',image:'file:///tmp/shot.png',summary:'First',body:'hello'}
  s.popupModel={count:1,get() { return row }}
  for (const name of ['persistPopupFile', 'acceptNormalizedImages', 'popupImageFor']) load(s, name)
  assert.equal(s.persistPopupFile(row), true)
  const first = JSON.parse(s.popupFileQueue[0].payload)
  // An in-place update of the text only, too large for the queue: the first write still runs.
  row.body='\0'.repeat(25000)
  assert.equal(s.persistPopupFile(row), false)
  assert.equal(s.popupFileQueue.length, 1)
  const normalized='file:///tmp/images/1000-1-image-'+'a'.repeat(64)+'.png'
  s.acceptNormalizedImages({key:'1000-1', version:first.imageVersion, sources:first.sources, appIcon:'foot', image:normalized})
  assert.equal(s.popupImageFor(row, 'image'), normalized, 'the queued write\'s image was dropped')
})

test('intake takes every field, hint and action at its limit, rejects one more, and drops oversized replacement values whole', () => {
  const n={appName:'Chat',appIcon:'chat',summary:'Hi',body:'Hello',desktopEntry:'chat',hints:{},actions:[]}
  assert.equal(Logic.notificationWithinLimits(n), true)
  const within = changes => Logic.notificationWithinLimits({...n, ...changes})
  for (const [name, limit] of Object.entries({appName:256, appIcon:4096, summary:4096, body:16384, desktopEntry:512, image:12288})) {
    assert.equal(within({[name]:'x'.repeat(limit)}), true, name + ' at its limit')
    assert.equal(within({[name]:'x'.repeat(limit + 1)}), false, name + ' over its limit')
  }
  for (const [name, limit] of Object.entries({'image-path':4096, 'image_path':4096, 'omarchy-glyph':64, 'omarchy-exec-argv':8192, 'omarchy-actions':8192})) {
    assert.equal(within({hints:{[name]:'x'.repeat(limit)}}), true, name + ' at its limit')
    assert.equal(within({hints:{[name]:'x'.repeat(limit + 1)}}), false, name + ' over its limit')
  }
  const action = {identifier:'reply', text:'Reply'}
  assert.equal(within({actions:Array(32).fill(action)}), true, '32 actions')
  assert.equal(within({actions:Array(33).fill(action)}), false, '33 actions')
  assert.equal(within({actions:[{identifier:'x'.repeat(256)}]}), true, 'identifier at its limit')
  assert.equal(within({actions:[{identifier:'x'.repeat(257)}]}), false, 'identifier over its limit')
  assert.equal(within({actions:[{identifier:'reply',text:'x'.repeat(512)}]}), true, 'text at its limit')
  assert.equal(within({actions:[{identifier:'reply',text:'x'.repeat(513)}]}), false, 'text over its limit')
  const snapshot=Logic.snapshotOf({...n,appIcon:'x'.repeat(1000000),hints:{'image-path':'/'+'x'.repeat(1000000),'omarchy-exec-argv':'x'.repeat(1000000)}})
  assert.equal(snapshot.appIcon, ''); assert.equal(snapshot.image, ''); assert.equal(snapshot.execArgv, '')
})

test('a history write reply is never read as an image result', () => {
  const s = fixture()
  const sent = withHelper(s), accepted = []
  s.acceptNormalizedImages = result => accepted.push(result)
  s.put('{}', '1-1', () => {}, true)
  s.helperReplied(JSON.stringify({id:sent[0].id, ok:true, out:''}))
  assert.deepEqual(accepted, [])
  assert.equal(s.lastError, '')
  // A popup write's reply is the image result.
  s.put('{}', '1-2')
  s.helperReplied(JSON.stringify({id:sent[1].id, ok:true, out:JSON.stringify({key:'1-2', version:1, sources:{}})}))
  assert.equal(accepted.length, 1)
  assert.equal(accepted[0].key, '1-2')
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

test('a history clear turns deferred archives into deletes so they cannot refill history', () => {
  const s = fixture()
  load(s, 'deleteDeferredArchives')
  s.deferredMoves = {'1-1':'archive','2-2':'delete','300-3':'archive'}
  s.deleteDeferredArchives(key => Number(key.split('-')[0]) <= 100)
  assert.deepEqual({...s.deferredMoves}, {'1-1':'delete','2-2':'delete','300-3':'archive'})
  // The real clear converts every deferred archive and queues the clear.
  s.liveKeys = {}; s.retainedHistory = {}; s.releaseHistoryAction = () => {}
  load(s, 'clearHistory')
  s.deferredMoves = {'4-4':'archive','5-5':'delete'}
  assert.equal(s.clearHistory(), true)
  assert.deepEqual({...s.deferredMoves}, {'4-4':'delete','5-5':'delete'})
  assert.equal(s.popupFileQueue[s.popupFileQueue.length - 1].op, 'clear')
})

test('a history clear the queue refuses changes nothing; once it has room the clear does all of it', () => {
  const s = fixture()
  let dismissed = 0, cancelled = 0
  s.centerActionsChanged = () => {}
  s.liveKeys = {1:'1000-1'}; s.liveRefs = {1:{tracked:true, dismiss() { dismissed++ }}}
  s.retainedHistory = {'1000-1':{originalId:1, timestamp:1000}}
  s.deferredMoves = {'2000-2':'archive'}
  for (const name of ['deleteDeferredArchives', 'releaseHistoryAction', 'clearHistory']) load(s, name)
  // A sender flood fills the queue with jobs a clear cannot cancel.
  for (let i = 0; i < s.popupQueueLimit; i++) s.enqueueHelperJob('delete', {key:'9-' + i})
  assert.equal(s.clearHistory(), false)
  assert.deepEqual(Object.keys(s.retainedHistory), ['1000-1'], 'the center lost an action')
  assert.equal(dismissed, 0)
  assert.deepEqual({...s.deferredMoves}, {'2000-2':'archive'})
  // A silenced notification's history write waits in the last slot: the clear
  // cancels it, which makes the room the clear needs.
  s.popupFileQueue = s.popupFileQueue.slice(0, -1)
  s.put('{}', '1000-1', ok => { if (!ok) cancelled++ }, true)
  assert.equal(s.clearHistory(), true)
  assert.equal(cancelled, 1)
  assert.equal(s.popupFileQueue.filter(job => job.historyWrite).length, 0)
  assert.deepEqual(Object.keys(s.retainedHistory), [])
  assert.equal(dismissed, 1)
  assert.deepEqual({...s.deferredMoves}, {'2000-2':'delete'})
  assert.equal(s.popupFileQueue[s.popupFileQueue.length - 1].op, 'clear')
})

test('refused moves run as one archive and one delete batch once the queue drains', () => {
  const s = fixture()
  const sent = withHelper(s)
  load(s, 'deferMove')
  s.put('"busy"')
  s.deferMove('1-1', 'archive'); s.deferMove('2-2', 'delete'); s.deferMove('3-3', 'archive')
  s.runNextPopupFileJob()
  assert.equal(sent.length, 1, 'a deferred move ran while a job was in flight')
  s.helperReplied(JSON.stringify({id:sent[0].id, ok:true, out:''}))
  assert.deepEqual([sent[1].op, sent[1].data], ['archive', {keys:['1-1', '3-3']}])
  assert.equal(sent.length, 2)
  s.helperReplied(JSON.stringify({id:sent[1].id, ok:true, out:''}))
  assert.deepEqual([sent[2].op, sent[2].data], ['delete', {keys:['2-2']}])
  s.helperReplied(JSON.stringify({id:sent[2].id, ok:true, out:''}))
  assert.equal(sent.length, 3, 'a deferred move ran twice')
  assert.deepEqual({...s.deferredMoves}, {})
  // Bounded: 2048 distinct keys are kept, a new one past that is dropped, a kept one can still become a delete.
  const b = fixture()
  b.deferredMoves = {}
  load(b, 'deferMove')
  for (let i = 0; i < 2049; i++) b.deferMove('1-' + i, 'archive')
  assert.equal(Object.keys(b.deferredMoves).length, 2048)
  assert.equal(b.deferredMoves['1-2048'], undefined)
  b.deferMove('1-0', 'delete')
  assert.equal(b.deferredMoves['1-0'], 'delete')
  assert.equal(Object.keys(b.deferredMoves).length, 2048)
})

test('a refused or failed history read never replays, so live popups keep their actions', () => {
  const s = fixture()
  let replays = 0
  s.historyReadQueued = false; s.replayCarryOver = []
  s.liveRowsForReplay = () => [{key:'1-1', actionsJson:'[{"identifier":"reply"}]'}]
  s.replayHistory = () => { replays++ }
  load(s, 'showRecentHistory')
  for (let i = 0; i < s.popupQueueLimit; i++) s.enqueueHelperJob('delete', {key: '9-' + i})
  // Full queue: enqueueHelperJob calls the callback synchronously with ok === false.
  assert.equal(s.showRecentHistory(), 'busy')
  assert.equal(replays, 0)
  assert.equal(s.historyReadQueued, false)
  assert.deepEqual(Array.from(s.replayCarryOver), [])
  // A read that is queued but later fails (helper exit, cancellation) replays nothing either.
  s.popupFileQueue = []
  assert.equal(s.showRecentHistory(), 'ok')
  s.popupFileQueue[0].done(false, '')
  assert.equal(replays, 0)
  s.showRecentHistory(); s.popupFileQueue[s.popupFileQueue.length - 1].done(true, '{"summary":"x"}\n')
  assert.equal(replays, 1)
})

test('history clears and center releases answer "ok", "invalid" and "busy" through the IPC', () => {
  const s = fixture()
  s.deferredMoves = {}; s.liveKeys = {}; s.retainedHistory = {}
  s.releaseHistoryAction = () => {}; s.removeKeys = () => {}
  for (const name of ['deleteDeferredArchives', 'releaseHistoryKeys', 'releaseHistoryBefore', 'clearHistory']) load(s, name)
  const clear = loadIpc(s, 'clear'), releaseHistory = loadIpc(s, 'releaseHistory'), releaseHistoryBefore = loadIpc(s, 'releaseHistoryBefore')
  assert.equal(clear(), 'ok')
  assert.equal(releaseHistory('1-1'), 'ok')
  assert.equal(releaseHistoryBefore('1000'), 'ok')
  assert.equal(releaseHistoryBefore('1234567890123456'), 'ok', '16 digits')
  // A malformed key or timestamp is refused before any work is queued.
  const queued = s.popupFileQueue.length
  for (const keys of ['../1', '1-1,', '1-1x', '1-1/../x', Array(101).fill('1-1').join(',')])
    assert.equal(releaseHistory(keys), 'invalid', keys.slice(0, 20))
  for (const timestamp of ['', '1e3', '-1', '12345678901234567'])
    assert.equal(releaseHistoryBefore(timestamp), 'invalid', timestamp)
  assert.equal(s.popupFileQueue.length, queued)
  // A full queue refuses them: "busy", so the center retries.
  s.popupFileQueue = []
  for (let i = 0; i < s.popupQueueLimit; i++) s.enqueueHelperJob('delete', {key: '9-' + i})
  assert.equal(clear(), 'busy')
  assert.equal(releaseHistory('2-2'), 'busy')
  assert.equal(releaseHistoryBefore('1000'), 'busy')
})

test('a dismissal converts only popup writes queued after the last history barrier', () => {
  const s = fixture()
  s.historyBarrierOps = JSON.parse(source.match(/readonly property var historyBarrierOps: (\[[^\]]*\])/)[1])
  s.deferMove = () => assert.fail('nothing is refused here')
  load(s, 'archivePopupFileFor')
  const row = {timestamp:1000, originalId:1}, key = Logic.imageStem(row)
  // No barrier: the queued popup write goes straight to history, no second job.
  s.put(JSON.stringify({entry:{}, sources:{}, imageVersion:1}), key)
  s.archivePopupFileFor(row)
  assert.deepEqual(s.popupFileQueue.map(j => [j.op, j.historyWrite]), [['write', true]])
  // Still on screen when history was cleared, dismissed afterwards: it must survive the clear.
  for (const barrier of s.historyBarrierOps) {
    s.popupFileQueue = []
    s.put(JSON.stringify({entry:{}, sources:{}, imageVersion:1}), key)
    s.enqueueHelperJob(barrier, barrier === 'drop' || barrier === 'forget' ? {keys:['9-9']} : {})
    s.archivePopupFileFor(row)
    assert.deepEqual(s.popupFileQueue.map(j => [j.op, !!j.historyWrite]), [['write', false], [barrier, false], ['archive', false]], barrier)
  }
  // A newer write after the barrier converts; the one before it stays a popup write.
  s.popupFileQueue = []
  s.put('{"entry":{}}', key); s.enqueueHelperJob('clear', {}); s.put('{"entry":{}}', key)
  s.archivePopupFileFor(row)
  assert.deepEqual(s.popupFileQueue.map(j => [j.op, !!j.historyWrite]), [['write', false], ['clear', false], ['write', true]])
})

test('an unavailable helper rejects jobs at once and completes each callback once', () => {
  const s = fixture()
  s.helperUnavailable = true
  const calls = []
  assert.equal(s.enqueueHelperJob('focus', {app:'x'}, (ok, out) => calls.push([ok, out])), false)
  assert.deepEqual(calls, [[false, '']])
  assert.equal(s.popupFileQueue.length, 0)
  assert.equal(s.lastError, s.FoamyNative.UNAVAILABLE_TEXT)
})

test('helper restarts back off from 1 s to 60 s and reset after a stable run', () => {
  const s = fixture()
  s.helperBackoffMs = 1000; s.helperStartedAt = 0
  for (const name of ['helperBackoffMaxMs', 'helperStableMs'])
    s[name] = Number(source.match(new RegExp('readonly property int ' + name + ': ([0-9]+)'))[1])
  load(s, 'nextHelperRestartDelay')
  // A helper that dies right after each start: never a 1 Hz loop.
  const delays = []
  let now = 1000
  for (let i = 0; i < 9; i++) { s.helperStartedAt = now; now += 50; delays.push(s.nextHelperRestartDelay(now)); now += delays[delays.length - 1] }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000])
  // One that ran for 30 s is restarted after 1 s again.
  s.helperStartedAt = now; now += 30000
  assert.equal(s.nextHelperRestartDelay(now), 1000)
  s.helperStartedAt = now + 1000; assert.equal(s.nextHelperRestartDelay(now + 1010), 2000, 'a quick death after it doubles again')
})
