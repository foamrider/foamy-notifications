// 4.5 notifications IPC parity: act, keyboard mode, numbered buttons.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
const source=fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8')
const Qt={Key_Escape:1,Key_Down:2,Key_J:3,Key_Tab:4,Key_Up:5,Key_K:6,Key_Backtab:7,Key_Return:8,Key_Enter:9,Key_Space:10,Key_Delete:11,Key_Backspace:12,Key_X:13}
function fixture(groups) {
 const events=[]
 const s=vm.createContext({Qt,NotificationLogic:require('../NotificationLogic.js'),Features:{offers:row=>row.offers||{codes:'',link:''}},
  displayGroups:groups,targetMonitors:['A'],fullscreenMonitors:[],preferences:{suppressFullscreen:false,fullscreenCritical:true},liveRefs:{},busyKeys:{},keyboardMode:false,keyboardKey:'',isRestoredRow:()=>false,
  keyboardIdle:{restart(){},stop(){}},activateGroup:(g,id)=>events.push('activate:'+g.key+':'+id),
  featureAction:(g,k,v)=>events.push(k+':'+v),dismissGroup:(g,x)=>events.push('dismiss:'+g.key+':'+x)})
 s.service=s
 for(const name of ['showsGroupOn','screenShowing','groupFor','newestGroup','deedsFor','clickable','runDeed','actOnNewest','startKeyboard','stopKeyboard','moveSelection','keyboardPressed']) {
  const start=source.indexOf(`  function ${name}(`);assert.notEqual(start,-1,name)
  vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),s)
 }
 return {s,events}
}
const card=(key,extra={})=>({key,keys:[key],originalId:Number(key.split('-')[1]),actionsJson:'[]',execArgv:'',execActions:'',...extra})
test('act dismisses a card with nothing to do, clicks a clickable one, presses a sole button',()=>{
 let f=fixture([card('1-1')]);assert.equal(f.s.actOnNewest(),true);assert.deepEqual(f.events,['dismiss:1-1:false'])
 f=fixture([card('1-1',{execArgv:'["omarchy-launch-x"]'})]);f.s.actOnNewest();assert.deepEqual(f.events,['activate:1-1:default'])
 f=fixture([card('1-1',{actionsJson:'[{"identifier":"reply","text":"Reply"}]'})]);f.s.actOnNewest();assert.deepEqual(f.events,['activate:1-1:reply'])
 f=fixture([]);assert.equal(f.s.actOnNewest(),false)
})
test('act with several choices hands the keyboard over; keys move, number and dismiss',()=>{
 const exec=JSON.stringify([{label:'Open',argv:['mpv','/tmp/x']},{label:'Omacut',argv:['omacut','/tmp/x']}])
 const f=fixture([card('2-2',{execActions:exec}),card('1-1')])
 f.s.actOnNewest();assert.equal(f.s.keyboardMode,true);assert.equal(f.s.keyboardKey,'2-2')
 assert.equal(f.s.keyboardPressed(Qt.Key_J,'j'),true);assert.equal(f.s.keyboardKey,'1-1')
 f.s.keyboardPressed(Qt.Key_Up,'');assert.equal(f.s.keyboardKey,'2-2')
 assert.equal(f.s.keyboardPressed(Qt.Key_X,'x'),false)
 f.s.keyboardPressed(0,'2');assert.deepEqual(f.events,['exec:1']);assert.equal(f.s.keyboardMode,false)
 f.s.startKeyboard('1-1');f.s.keyboardPressed(Qt.Key_Delete,'');assert.equal(f.events.pop(),'dismiss:1-1:true')
 f.s.keyboardPressed(Qt.Key_Return,'');assert.equal(f.events.pop(),'activate:1-1:default');assert.equal(f.s.keyboardMode,false)
 f.s.startKeyboard('');f.s.keyboardPressed(Qt.Key_Escape,'');assert.equal(f.s.keyboardMode,false)
 assert.equal(f.s.keyboardPressed(Qt.Key_Down,''),false,'keys are not taken outside keyboard mode')
})
test('stock IPC methods and the shortcut entry point exist on the notifications target',()=>{
 const block=source.slice(source.indexOf('    target: "notifications"'),source.indexOf('    target: "foamy.notifications"'))
 for(const fn of ['act(): string','keyboard(): string','invokeAction(n: int): string','count(): string','expand(): string','probe(): string','isDnd(): string','dndState(): string','showHistory(): string','dismissOne(): string','dismissAll(): string','invokeLast(): string'])
  assert.ok(block.includes('function '+fn),fn)
 assert.ok(source.includes('  function runShortcut(method)'))
 assert.match(source,/FoamyIpc \{\s+id: ipcHandler\s+target: "notifications"/)
})
test('keyboard focus goes to an output that shows the card, never to a fullscreen one hiding it',()=>{
 const f=fixture([card('2-2',{urgency:1}),card('1-1',{urgency:2})])
 f.s.targetMonitors=['A','B'];f.s.preferences.suppressFullscreen=true;f.s.fullscreenMonitors=['A']
 assert.equal(f.s.screenShowing(f.s.groupFor('2-2')),'B')
 assert.equal(f.s.screenShowing(f.s.groupFor('1-1')),'A','critical cards stay on a fullscreen output when allowed')
 f.s.fullscreenMonitors=['A','B']
 assert.equal(f.s.startKeyboard(''),true);assert.equal(f.s.keyboardKey,'1-1','normal cards hidden everywhere are skipped')
 f.s.keyboardPressed(Qt.Key_Up,'');assert.equal(f.s.keyboardKey,'1-1')
 f.s.preferences.fullscreenCritical=false;f.s.stopKeyboard();assert.equal(f.s.startKeyboard(''),false)
 assert.match(source,/takesKeyboard: service.keyboardMode && service.keyboardScreen === screen.name/)
})
