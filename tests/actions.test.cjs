const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
const source=fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8')
function fixture() {
 const jobs=[],events=[]
 const state=vm.createContext({busyKeys:{},actionErrors:{},handledKeys:{},liveRefs:{},helper:'helper',preferences:{browserMappings:[]},console,
  NotificationLogic:{parseExecArgv:raw=>raw?JSON.parse(raw):null},isRestoredRow:()=>false,
  enqueuePopupFileJob:(argv,done,payload)=>jobs.push({command:argv[2],done,payload}),
  notifyCenter:()=>events.push('center'),removeKeys:()=>events.push('remove'),Util:{execArgv:()=>events.push('launch')}})
 state.service=state
 for(const name of ['activateGroup','commitHandled','finishAction']) {
  const start=source.indexOf(`  function ${name}(`)
  vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),state)
 }
 return {state,jobs,events,row:{key:'1-1',keys:['1-1'],originalId:1,app:'Test'}}
}
test('failed focus retains history; retry forgets only after focus succeeds',()=>{
 const {state,jobs,events,row}=fixture()
 state.activateGroup(row,'default');assert.equal(jobs[0].command,'focus')
 jobs.shift().done(false);assert.equal(jobs.length,0);assert.deepEqual(events,[]);assert.equal(state.handledKeys['1-1'],undefined);assert.ok(state.actionErrors['1-1']);assert.equal(state.busyKeys['1-1'],undefined)
 state.activateGroup(row,'default');jobs.shift().done(true);assert.equal(jobs[0].command,'forget');assert.deepEqual(events,[])
 jobs.shift().done(true);assert.equal(state.handledKeys['1-1'],true);assert.deepEqual(events,['center','remove'])
})
test('missing or throwing non-default callbacks do not discard notifications',()=>{
 for (const actions of [[],[{identifier:'read',invoke(){throw Error('closed')}}]]) {
  const {state,jobs,events,row}=fixture();state.liveRefs[1]={actions}
  state.activateGroup(row,'read');assert.equal(jobs.length,0);assert.deepEqual(events,[]);assert.ok(state.actionErrors['1-1'])
 }
})
test('live callback runs before forgetting; failed persistence retains history',()=>{
 const {state,jobs,events,row}=fixture();state.liveRefs[1]={actions:[{identifier:'read',invoke(){events.push('invoke')}}]}
 state.activateGroup(row,'read');assert.deepEqual(events,['invoke']);assert.equal(jobs[0].command,'forget')
 jobs.shift().done(false);assert.deepEqual(events,['invoke']);assert.equal(state.handledKeys['1-1'],undefined);assert.ok(state.actionErrors['1-1'])
})
test('shell-restarting commands retain their persist-before-launch ordering',()=>{
 const {state,jobs,events,row}=fixture();row.execArgv='["omarchy","restart","shell"]'
 state.activateGroup(row,'default');assert.equal(jobs[0].command,'forget');assert.deepEqual(events,[])
 jobs.shift().done(true);assert.deepEqual(events,['center','launch','remove'])
})
