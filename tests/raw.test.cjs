const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const Logic = require('../NotificationLogic.js')
const source = fs.readFileSync(require('node:path').join(__dirname, '../Service.qml'), 'utf8')
function fixture(rows=[]) {
  const later=[], writes=[], cancelled=[]
  const s=vm.createContext({NotificationLogic:Logic,NotificationUrgency:{Critical:2},
    liveRefs:{},liveKeys:{},retainedHistory:{},pendingNotifications:[],busyKeys:{},handledKeys:{},lifetimes:{},
    updateSignals:JSON.parse(source.match(/readonly property var updateSignals: (\[[\s\S]*?\])/)[1]),
    preferences:{monitor:'focused',suppressFullscreen:false},doNotDisturb:false,
    Qt:{callLater:fn=>later.push(fn)},snapshotOf:n=>Logic.snapshotOf(n,1000),
    suppressForFullscreen:()=>false,refreshContext(){},scheduleRebuild(){},durationFor:()=>5000,
    popupModel:{get:i=>rows[i],get count(){return rows.length},setProperty(i,key,value){rows[i][key]=value},insert(i,row){rows.splice(i,0,row)}},
    cancelPendingWrites:keys=>cancelled.push(...keys),removePopupsByOriginalId(){},
    persistPopupFile:row=>{writes.push({kind:'popup',row});return true},writeHistoryFile:row=>writes.push({kind:'history',row}),
    reportPersistenceError:message=>writes.push({kind:'error',message}),rejectNotification:n=>{n.tracked=false},
    handleNotification:n=>writes.push({kind:'native',n})})
  s.service=s
  for(const name of ['shouldBypassDnd','shouldArchiveSilenced','detachRawNotification','receiveDetachedSnapshot','handleDetachedSnapshot','receiveNotification','refreshPopup']) {
    const start=source.indexOf('  function '+name+'(')
    vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),s)
  }
  return {s,rows,writes,cancelled,flush(){while(later.length)later.shift()()}}
}
function notification(image='image://qsimage/1') {
  const handlers={},names=JSON.parse(source.match(/readonly property var updateSignals: (\[[\s\S]*?\])/)[1])
  const signals=Object.fromEntries(names.map(name=>[name,{connect:handler=>(handlers[name] ||= []).push(handler)}]))
  return {id:1,appName:'Chat',appIcon:'chat',summary:'Hello',body:'Message',image,hints:{},urgency:1,actions:[{identifier:'reply',text:'Reply'}],tracked:false,
    ...signals,emit(name){(handlers[name] || []).slice().forEach(handler=>handler())},change(image){this.image=image;this.emit('imageChanged')}}
}
test('raw recognition excludes theme providers, file pictures and text-only senders',()=>{
  for(const image of ['image://qsimage/1','image://qsimage/1024/1'])assert.equal(Logic.hasRawImage({image}),true)
  for(const image of ['image://icon/chat','file:///tmp/avatar.png','chat',''])assert.equal(Logic.hasRawImage({image}),false)
})
test('raw intake preserves only bounded primitives and never enters native callback handling',()=>{
  const {s,writes,rows,flush}=fixture(),n=notification()
  n.hints={'omarchy-exec-argv':'["omarchy","menu"]','image-path':'/tmp/avatar.png'}
  s.receiveNotification(n);flush()
  assert.equal(n.tracked,false)
  assert.equal(writes[0].kind,'popup')
  assert.equal(rows[0].actionsJson,'[]')
  assert.equal(rows[0].execArgv,'["omarchy","menu"]')
  assert.equal(rows[0].image,'file:///tmp/avatar.png')
  assert.ok(Object.values(rows[0]).every(value=>typeof value!=='object'))
  assert.deepEqual(Object.keys(s.liveRefs),[])
})
test('context backlog contains raw snapshots only and keeps its hard bound',()=>{
  const {s,writes}=fixture();s.preferences.suppressFullscreen=true
  for(let i=0;i<110;i++){const n=notification();n.id=i+1;s.receiveNotification(n);assert.equal(n.tracked,false)}
  assert.equal(s.pendingNotifications.length,100)
  assert.ok(s.pendingNotifications.every(job=>job.snapshot && !job.notification))
  assert.equal(writes.filter(job=>job.kind==='error').length,10)
})
test('a native replacement awaiting compositor context becomes a primitive before release',()=>{
  const {s}=fixture();s.preferences.suppressFullscreen=true
  const n=notification('file:///tmp/avatar.png')
  s.receiveNotification(n);assert.equal(n.tracked,true)
  n.summary='Replacement';n.change('image://qsimage/2')
  assert.equal(n.tracked,false)
  assert.equal(s.pendingNotifications.length,1)
  assert.equal(s.pendingNotifications[0].notification,undefined)
  assert.equal(s.pendingNotifications[0].snapshot.summary,'Replacement')
  assert.equal(s.pendingNotifications[0].snapshot.actionsJson,'[]')
})
test('raw replacement releases visible/history references and queued writes even while an action is busy',()=>{
  const n=notification(),row=Logic.snapshotOf(n,10),{s,rows,cancelled,writes,flush}=fixture([row])
  s.liveRefs[1]=n;s.liveKeys[1]='10-1';s.retainedHistory['10-1']=row;s.busyKeys['10-1']=true;n.tracked=true;n.summary='Updated'
  s.refreshPopup(n,1,10)
  assert.equal(n.tracked,false)
  assert.deepEqual(Object.keys(s.liveRefs),[]);assert.deepEqual(Object.keys(s.retainedHistory),[])
  assert.deepEqual(cancelled,['10-1'])
  flush();assert.equal(rows[0].summary,'Updated');assert.equal(rows[0].actionsJson,'[]')
  assert.equal(writes.length,0)
})
test('raw snapshots preserve DND/fullscreen suppression, ephemeral policy and trusted bypass',()=>{
  for(const mode of ['dnd','fullscreen','transient','cli','critical-chat','trusted-cli','action']) {
    const {s,rows,writes,flush}=fixture();const n=notification()
    s.doNotDisturb=mode!=='fullscreen'
    if(mode==='fullscreen')s.suppressForFullscreen=()=>true
    if(mode==='transient')n.transient=true
    if(mode==='cli'||mode==='trusted-cli')n.appName='notify-send'
    if(mode==='trusted-cli'||mode==='critical-chat')n.urgency=2
    if(mode==='action')n.appName='omarchy-action'
    s.receiveNotification(n);flush()
    const visible=mode==='trusted-cli'||mode==='action'
    assert.equal(rows.length,visible?1:0,mode)
    assert.equal(writes.length,mode==='transient'||mode==='cli'?0:1,mode)
    if(writes.length)assert.equal(writes[0].kind,visible?'popup':'history',mode)
  }
})

test('grouped property signals detach a native replacement once without creating a second popup',()=>{
  const n=notification('file:///tmp/avatar.png'),row=Logic.snapshotOf(n,10),{s,rows,writes,flush}=fixture([row])
  s.receiveNotification(n);s.liveRefs[1]=n;s.liveKeys[1]='10-1';n.tracked=true
  // Qt publishes the group's new image value before it emits imageChanged.
  n.summary='Replacement';n.image='image://qsimage/2';s.refreshPopup(n,1,10)
  n.change('image://qsimage/2');flush()
  assert.equal(n.tracked,false);assert.equal(rows.length,1);assert.equal(rows[0].summary,'Replacement')
  assert.equal(writes.filter(job=>job.kind==='popup').length,1)
})

test('remaining replacement signals finish the detached snapshot without stale image hints or text',()=>{
  const n=notification('file:///tmp/avatar.png');n.hints={'image-path':'/tmp/avatar.png'}
  const row=Logic.snapshotOf(n,10),{s,rows,writes,flush}=fixture([row])
  s.receiveNotification(n);s.liveRefs[1]=n;s.liveKeys[1]='10-1';n.tracked=true
  n.image='image://qsimage/2';s.refreshPopup(n,1,10)
  assert.equal(n.tracked,false)
  n.hints={};n.body='Updated body';n.emit('hintsChanged');n.emit('bodyChanged');flush()
  assert.equal(rows.length,1);assert.equal(rows[0].image,'');assert.equal(rows[0].body,'Updated body')
  assert.equal(rows[0].actionsJson,'[]');assert.equal(writes.filter(job=>job.kind==='popup').length,1)
})


test('DND critical option archives raw critical alerts including CLI senders and respects transient hints',()=>{
 for(const appName of ['omarchy-action','notify-send','Chat']) for(const enabled of [false,true]) for(const dnd of [false,true]) {
  const {s,rows,writes,flush}=fixture(),n=notification()
  s.preferences.dndSilenceCritical=enabled;s.doNotDisturb=dnd;n.appName=appName;n.urgency=2
  s.receiveNotification(n);flush()
  const silenced=dnd && (enabled || appName==='Chat')
  assert.equal(rows.length,silenced?0:1)
  assert.equal(writes[0].kind,silenced?'history':'popup')
 }
 const {s,rows,writes,flush}=fixture(),n=notification()
 s.preferences.dndSilenceCritical=true;s.doNotDisturb=true;n.appName='omarchy-action';n.urgency=2;n.transient=true
 s.receiveNotification(n);flush()
 assert.equal(rows.length,0);assert.equal(writes.length,0)
})
