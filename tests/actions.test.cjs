const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
const source=fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8')
function fixture() {
 const jobs=[],events=[]
 const state=vm.createContext({popupFileQueue:[],runNextPopupFileJob(){},busyKeys:{},actionErrors:{},handledKeys:{},liveRefs:{},liveKeys:{},retainedHistory:{},deferredMoves:{},centerEnabled:true,historyLimit:100,centerActionLifetime:86400000,now:1000,pokes:0,helper:'helper',preferences:{browserMappings:[]},console,
  NotificationLogic:{...require('../NotificationLogic.js')},isRestoredRow:()=>false,
  enqueueHelperJob:(op,data,done)=>jobs.push({command:op,done:done||null,payload:JSON.stringify(data)}),
  notifyCenter:()=>events.push('center'),centerActionsChanged(){state.pokes++},removeKeys:()=>events.push('remove'),scheduleRebuild(){},Util:{execArgv:()=>events.push('launch')}})
 state.service=state
 state.actionNow=()=>state.now
 for(const name of ['cancelPendingWrites','retainHistoryAction','releaseHistoryAction','releaseHistoryKeys','releaseHistoryBefore','invokeLiveAction','invokeCenterDefault','centerRow','centerButtons','centerActionList','invokeCenterAction','expireCenterActions','activateGroup','dismissGroup','commitHandled','finishAction','pruneHandledKeys','deleteDeferredArchives']) {
  const start=source.indexOf(`  function ${name}(`)
  vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),state)
 }
 return {state,jobs,events,row:{key:'1-1',keys:['1-1'],originalId:1,app:'Test'}}
}
test('X removal forgets the duplicate group without invoking any action',()=>{
 const {state,jobs,events,row}=fixture()
 state.preferences.closeButtonRemovesHistory=true
 row.keys=['1-1','2-2'];row.execArgv='["must-not-launch"]'
 state.liveRefs[1]={actions:[{identifier:'default',invoke(){events.push('invoke')}}]}
 state.popupFileQueue=[{writeKey:'1-1',payload:'obsolete snapshot'},{writeKey:'2-2',payload:'obsolete duplicate'},{writeKey:'3-3',payload:'other notification'}]
 state.dismissGroup(row,true)
 assert.equal(jobs[0].command,'forget');assert.deepEqual(JSON.parse(jobs[0].payload).keys,row.keys)
 assert.deepEqual(events,[])
 state.dismissGroup(row,true);state.dismissGroup(row,false);assert.equal(jobs.length,1)
 jobs.shift().done(true)
 assert.deepEqual(events,['center','remove'])
 assert.equal(state.handledKeys['1-1'],true);assert.equal(state.handledKeys['2-2'],true)
 assert.deepEqual(Array.from(state.popupFileQueue,job=>job.writeKey),['3-3'])
})
test('failed X removal keeps snapshots and permits retry',()=>{
 const {state,jobs,events,row}=fixture()
 state.preferences.closeButtonRemovesHistory=true
 state.popupFileQueue=[{writeKey:'1-1',payload:'prior snapshot'}]
 state.dismissGroup(row,true);jobs.shift().done(false)
 assert.deepEqual(events,[]);assert.equal(state.popupFileQueue.length,1)
 assert.equal(state.handledKeys['1-1'],undefined);assert.ok(state.actionErrors['1-1'])
 assert.equal(state.busyKeys['1-1'],undefined)
 state.dismissGroup(row,true);jobs.shift().done(true)
 assert.deepEqual(events,['center','remove'])
})
test('right-click always archives and X archives when the option is off',()=>{
 for(const [enabled,close] of [[true,false],[false,false],[false,true]]) {
  const {state,jobs,row}=fixture();const removed=[]
  state.preferences.closeButtonRemovesHistory=enabled
  state.removeKeys=(keys,reason)=>removed.push({keys,reason})
  state.dismissGroup(row,close)
  assert.equal(jobs.length,0);assert.deepEqual(removed,[{keys:row.keys,reason:'dismiss'}])
 }
})
test('failed focus retains history; retry forgets only after focus succeeds',()=>{
 const {state,jobs,events,row}=fixture()
 state.activateGroup(row,'default');assert.equal(jobs[0].command,'focus')
 jobs.shift().done(false);assert.equal(jobs.length,0);assert.deepEqual(events,[]);assert.equal(state.handledKeys['1-1'],undefined);assert.ok(state.actionErrors['1-1']);assert.equal(state.busyKeys['1-1'],undefined)
 state.activateGroup(row,'default');jobs.shift().done(true);assert.equal(jobs[0].command,'forget');assert.deepEqual(events,[])
 jobs.shift().done(true);assert.equal(state.handledKeys['1-1'],true);assert.deepEqual(events,['center','remove'])
})
test('a windowless sender closes on click without an error or a history removal',()=>{
 const {state,jobs,events,row}=fixture();const removed=[]
 state.removeKeys=(keys,reason)=>removed.push({keys,reason})
 state.activateGroup(row,'default');jobs.shift().done(true,'none\n','')
 assert.deepEqual(removed,[{keys:row.keys,reason:'dismiss'}]);assert.equal(jobs.length,0);assert.deepEqual(events,[])
 assert.equal(state.actionErrors['1-1'],undefined);assert.equal(state.busyKeys['1-1'],undefined);assert.equal(state.handledKeys['1-1'],undefined)
})
test('only a browser focus miss suggests browserMappings',()=>{
 for(const [reason,mapping] of [['browser',true],['ambiguous',false],['',false]]) {
  const {state,jobs,events,row}=fixture()
  state.activateGroup(row,'default');jobs.shift().done(false,'',reason)
  assert.equal(/browserMappings/.test(state.actionErrors['1-1']),mapping,reason);assert.deepEqual(events,[])
 }
})
test('missing or throwing non-default callbacks do not discard notifications',()=>{
 for (const actions of [[],[{identifier:'read',invoke(){throw Error('closed')}}]]) {
  const {state,jobs,events,row}=fixture();state.liveRefs[1]={actions}
  state.activateGroup(row,'read');assert.equal(jobs.length,0);assert.deepEqual(events,[]);assert.ok(state.actionErrors['1-1'])
 }
})
test('live callback runs before forgetting; failed persistence retains history',()=>{
 const {state,jobs,events,row}=fixture();state.liveRefs[1]={actions:[{identifier:'read',invoke(){events.push('invoke')}}]}
 state.popupFileQueue=[{writeKey:'1-1',payload:'prior snapshot'}]
 state.activateGroup(row,'read');assert.deepEqual(events,['invoke']);assert.equal(jobs[0].command,'forget')
 jobs.shift().done(false);assert.deepEqual(events,['invoke']);assert.equal(state.handledKeys['1-1'],undefined);assert.ok(state.actionErrors['1-1'])
 assert.equal(state.popupFileQueue.length,1)
 state.activateGroup(row,'read');jobs.shift().done(true)
 assert.equal(state.popupFileQueue.length,0)
})
test('shell-restarting commands retain their persist-before-launch ordering',()=>{
 const {state,jobs,events,row}=fixture();row.execArgv='["omarchy","restart","shell"]'
 state.activateGroup(row,'default');assert.equal(jobs[0].command,'forget');assert.deepEqual(events,[])
 jobs.shift().done(true);assert.deepEqual(events,['center','launch','remove'])
})

function centerFixture(rows) {
 const f=fixture()
 f.state.popupModel={count:rows.length,get:i=>rows[i]}
 f.state.NotificationLogic.imageStem=row=>`${row.timestamp}-${row.originalId}`
 return f
}
test('center invokes only the exact live default action and handles one notification',()=>{
 const {state,jobs,events}=centerFixture([{timestamp:10,originalId:1}])
 state.liveRefs[1]={actions:[{identifier:'default',invoke(){events.push('invoke')}}]}
 assert.equal(state.invokeCenterDefault('9-1'),'unavailable')
 assert.equal(state.invokeCenterDefault('../bad'),'invalid')
 assert.deepEqual(events,[])
 assert.equal(state.invokeCenterDefault('10-1'),'invoked')
 assert.deepEqual(events,['invoke'])
 assert.equal(state.invokeCenterDefault('10-1'),'busy')
 assert.deepEqual(JSON.parse(jobs[0].payload).keys,['10-1'])
 jobs.shift().done(true)
 assert.deepEqual(events,['invoke','center','remove'])
})
test('center does not replay archived commands or restored and expired callbacks',()=>{
 for (const restored of [true,false]) {
  const {state,jobs,events}=centerFixture([{timestamp:10,originalId:1,execArgv:'["unsafe"]'}])
  state.isRestoredRow=()=>restored
  if(restored)state.liveRefs[1]={actions:[{identifier:'default',invoke(){events.push('wrong generation')}}]}
  assert.equal(state.invokeCenterDefault('10-1'),'unavailable')
  assert.deepEqual(events,[]);assert.equal(jobs.length,0)
 }
})

function retainedFixture() {
 const f=centerFixture([]),row={timestamp:10,originalId:1,app:'Vivaldi',transient:false}
 const ref={tracked:true,actions:[{identifier:'default',invoke(){f.events.push('invoke')}}],dismiss(){this.tracked=false;f.events.push('closed')}}
 f.state.liveRefs[1]=ref;f.state.liveKeys[1]='10-1'
 return {...f,row,ref}
}
test('history callback survives the popup and is released after successful handling',()=>{
 const {state,row,ref,jobs,events}=retainedFixture()
 assert.equal(state.retainHistoryAction(row,ref),true)
 assert.equal(state.invokeCenterDefault('10-1'),'invoked')
 assert.deepEqual(events,['invoke'])
 jobs.shift().done(true)
 assert.deepEqual(events,['invoke','center','remove','closed'])
 assert.equal(Object.keys(state.retainedHistory).length,0)
 assert.equal(state.liveRefs[1],undefined)
})
test('retention is bounded and cannot release a reused notification ID',()=>{
 const {state,row,ref,events}=retainedFixture();state.historyLimit=1
 state.retainHistoryAction(row,ref)
 const next={...row,timestamp:11,originalId:2},other={...ref}
 state.liveRefs[2]=other;state.liveKeys[2]='11-2';state.retainHistoryAction(next,other)
 assert.equal(ref.tracked,false);assert.deepEqual(Object.keys(state.retainedHistory),['11-2'])
 state.liveRefs[2]={...ref,tracked:true};state.liveKeys[2]='12-2'
 state.releaseHistoryAction('11-2')
 assert.equal(state.liveRefs[2].tracked,true);assert.equal(state.liveKeys[2],'12-2')
})
test('stock-only, transient, action-less and day-old notifications do not retain callbacks',()=>{
 for(const mode of ['stock','transient','none','old']) {
  const {state,row,ref}=retainedFixture()
  if(mode==='stock')state.centerEnabled=false
  if(mode==='transient')row.transient=true
  if(mode==='none')ref.actions=[]
  if(mode==='old')state.now=10+86400000
  assert.equal(state.retainHistoryAction(row,ref),false)
 }
})

const plain=value=>JSON.parse(JSON.stringify(value))
// Omarchy commands (a click and --action buttons) from a live notification:
// kept in memory, never read back from a file, for 24 hours.
function commandFixture() {
 const f=retainedFixture()
 f.ref.actions=[]
 f.row.execArgv='["omarchy-shell","-q","foamy.zendesk","show","4821"]'
 f.row.execActions=JSON.stringify([{label:'View in Zendesk',argv:['omarchy-shell','-q','foamy.zendesk','viewInZendesk','4821:5']},
  {label:'Mark as read',argv:['omarchy-shell','-q','foamy.zendesk','markRead','4821:5']}])
 f.row.actionsJson='[]'
 const launched=[];f.state.Util={execArgv:argv=>{launched.push(argv);f.events.push('launch')}}
 return {...f,launched}
}
test('command notifications keep their click and buttons for the center',()=>{
 const {state,row,ref,jobs,events,launched}=commandFixture()
 assert.equal(state.retainHistoryAction(row,ref),true);assert.equal(state.pokes,1)
 assert.deepEqual(plain(state.centerActionList()),{'10-1':['View in Zendesk','Mark as read']})
 assert.equal(state.invokeCenterAction('10-1','2'),'unavailable')
 assert.equal(state.invokeCenterAction('10-1','x'),'invalid')
 assert.equal(state.invokeCenterAction('10-1','1'),'invoked');assert.deepEqual(events,[])
 assert.equal(state.invokeCenterAction('10-1','0'),'busy')
 jobs.shift().done(true)
 assert.deepEqual(launched,[['omarchy-shell','-q','foamy.zendesk','markRead','4821:5']])
 assert.deepEqual(events,['center','launch','remove','closed'])
 assert.deepEqual(plain(state.centerActionList()),{})
})
test('a retained click command runs after handling is persisted',()=>{
 const {state,row,ref,jobs,events,launched}=commandFixture()
 state.retainHistoryAction(row,ref)
 assert.equal(state.invokeCenterDefault('10-1'),'invoked');assert.deepEqual(events,[])
 jobs.shift().done(true)
 assert.deepEqual(launched,[['omarchy-shell','-q','foamy.zendesk','show','4821']])
})
test('the center presses sender actions by index while the sender is live',()=>{
 const {state,row,ref,jobs,events}=retainedFixture()
 ref.actions.push({identifier:'reply',invoke(){events.push('reply')}})
 row.actionsJson=JSON.stringify([{identifier:'reply',text:'Reply'}])
 state.retainHistoryAction(row,ref)
 assert.deepEqual(plain(state.centerActionList()),{'10-1':['Reply']})
 assert.equal(state.invokeCenterAction('10-1','0'),'invoked');assert.deepEqual(events,['reply'])
 jobs.shift().done(true);assert.deepEqual(events,['reply','center','remove','closed'])
})
test('actions expire 24 hours after arrival',()=>{
 const {state,row,ref,events}=commandFixture()
 state.retainHistoryAction(row,ref)
 state.now=10+86400000-1;assert.deepEqual(Object.keys(state.centerActionList()),['10-1'])
 state.now=10+86400000
 assert.equal(state.invokeCenterAction('10-1','0'),'unavailable')
 assert.equal(state.invokeCenterDefault('10-1'),'unavailable')
 assert.deepEqual(plain(state.centerActionList()),{})
 assert.equal(Object.keys(state.retainedHistory).length,0);assert.equal(ref.tracked,false)
 assert.deepEqual(events,['closed'])
})
test('restored popups and released notifications offer the center nothing',()=>{
 const {state,row,ref}=commandFixture()
 state.popupModel={count:1,get:()=>row};state.isRestoredRow=()=>true
 assert.deepEqual(plain(state.centerActionList()),{})
 assert.equal(state.invokeCenterAction('10-1','0'),'unavailable')
 state.isRestoredRow=()=>false;state.popupModel={count:0,get:()=>null}
 state.retainHistoryAction(row,ref);state.releaseHistoryKeys(['10-1'])
 assert.deepEqual(plain(state.centerActionList()),{})
})
test('history cleanup also closes a callback awaiting its first history write',()=>{
 const {state,ref}=retainedFixture()
 state.releaseHistoryKeys(['10-1'])
 assert.equal(ref.tracked,false);assert.equal(state.liveRefs[1],undefined)
})

test('clearing history releases pending callbacks but preserves newer arrivals',()=>{
 const {state,ref}=retainedFixture()
 const newer={...ref,tracked:true};state.liveRefs[2]=newer;state.liveKeys[2]='11-2'
 state.releaseHistoryBefore(10)
 assert.equal(ref.tracked,false);assert.equal(newer.tracked,true)
 assert.equal(state.liveKeys[2],'11-2')
})


test('center delegates current commands with the same persist-before-launch ordering',()=>{
 const {state,jobs,events}=centerFixture([{timestamp:10,originalId:1,execArgv:'["tensaku-edit","/tmp/a picture.png"]'}])
 state.liveKeys[1]='10-1';state.liveRefs[1]={tracked:true,actions:[]}
 assert.equal(state.invokeCenterDefault('10-1'),'invoked');assert.deepEqual(events,[])
 assert.equal(state.invokeCenterDefault('10-1'),'busy')
 jobs.shift().done(false);assert.deepEqual(events,[]);assert.ok(state.actionErrors['10-1'])
 assert.equal(state.invokeCenterDefault('10-1'),'invoked')
 jobs.shift().done(true);assert.deepEqual(events,['center','launch','remove'])
})
test('center never runs a command from a detached or reused native identity',()=>{
 for(const mode of ['detached','reused','restored']) {
  const {state,jobs,events}=centerFixture([{timestamp:10,originalId:1,execArgv:'["unsafe"]'}])
  state.liveKeys[1]=mode==='reused'?'11-1':'10-1';state.liveRefs[1]={tracked:mode!=='detached',actions:[]}
  state.isRestoredRow=()=>mode==='restored'
  assert.equal(state.invokeCenterDefault('10-1'),'unavailable');assert.equal(jobs.length,0);assert.deepEqual(events,[])
 }
})
test('clearing before a cutoff releases every eligible live key, not just the first 100',()=>{
 const {state,jobs}=fixture()
 const released=[]
 for(let i=1;i<=150;i++){state.liveKeys[i]=`${i}-${i}`;state.liveRefs[i]={tracked:true,dismiss(){released.push(i)}};state.retainedHistory[`${i}-${i}`]={originalId:i,timestamp:i}}
 state.releaseHistoryBefore(1000)
 assert.equal(released.length,150)
 const drops=jobs.filter(j=>j.command==='drop').map(j=>JSON.parse(j.payload))
 assert.ok(drops.every(d=>!d.keys||d.keys.length<=100),'helper batches stay within 100 keys')
 assert.equal(drops.filter(d=>d.keys).reduce((n,d)=>n+d.keys.length,0),150)
 assert.ok(drops.some(d=>d.before===1000))
})
