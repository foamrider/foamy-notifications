const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../Preferences.js');
const L = require('../NotificationLogic.js');
const row = (id, extra={}) => ({originalId:id,timestamp:1000+id,app:'Chat',desktopEntry:'chat',summary:'Message',body:'Hello',appIcon:'chat',urgency:1,execArgv:'',actionsJson:'[]',...extra});
const config = entry => JSON.stringify({plugins:[{id:'foamy.notifications',...entry}]});
// Expected values come from the settings table in CONFIGURATION.md, not from Preferences.js.
test('boolean settings accept true and false and reject other types',()=>{
 assert.deepEqual(['closeButtonRemovesHistory','dndSilenceCritical','compact','showTimeoutIndicator','showImages','imageSize'].map(key=>P.parse('{}').settings[key]),[false,false,true,true,true,56]);
 for(const key of ['groupDuplicates','closeButtonRemovesHistory','dndSilenceCritical','offerSnoozeWhenSharing','suppressFullscreen','fullscreenCritical','showImages','compact','showTimeoutIndicator','useBrowserFavicons']) {
  for(const value of [true,false]) assert.equal(P.parse(config({[key]:value})).settings?.[key],value,`${key}: ${value}`);
  for(const value of ['true',1,null]) {
   const parsed=P.parse(config({[key]:value}));
   assert.equal(parsed.settings,null,`${key}: ${JSON.stringify(value)} was taken`);
   assert.match(parsed.error,new RegExp(`Invalid ${key}`));
  }
 }
});
test('number settings take their documented whole-number range',()=>{
 for(const [key,min,max] of [['lowTimeoutSec',1,120],['normalTimeoutSec',1,120],['criticalTimeoutSec',0,120],['maxVisible',1,10],['width',280,600],['restoreMaxAgeSec',0,3600],['maxWaitSec',1,3600],['imageSize',40,72]]) {
  for(const value of [min,max]) assert.equal(P.parse(config({[key]:value})).settings?.[key],value,`${key}: ${value}`);
  for(const value of [min-1,max+1,min+0.5,String(min)]) {
   const parsed=P.parse(config({[key]:value}));
   assert.equal(parsed.settings,null,`${key}: ${JSON.stringify(value)} was taken`);
   assert.match(parsed.error,new RegExp(`Invalid ${key}`));
  }
 }
});
test('structured settings are checked: JSON, enums, named monitor, browser mappings',()=>{
 assert.match(P.parse('{').error,/SyntaxError/);
 const mapping={origin:'outlook.office.com',windowClass:'vivaldi-x'};
 for(const entry of [{monitor:'named'},{monitor:'named',monitorName:''},{monitorName:'x'.repeat(129)},{monitorName:7},{monitor:'left'},{browserGrouping:'site'},
  {browserMappings:mapping},{browserMappings:Array(33).fill(mapping)},{browserMappings:[{...mapping,origin:'a/b'}]},{browserMappings:[{...mapping,windowClass:''}]},
  {browserMappings:[{...mapping,windowClass:'x'.repeat(257)}]},{browserMappings:[null]}])
  assert.equal(P.parse(config(entry)).settings,null,JSON.stringify(entry).slice(0,80));
 const named=P.parse(config({monitor:'named',monitorName:'x'.repeat(128)})).settings;
 assert.deepEqual([named?.monitor,named?.monitorName.length],['named',128]);
 assert.deepEqual(P.parse(config({browserMappings:[mapping]})).settings?.browserMappings,[mapping]);
 assert.equal(P.parse(config({browserMappings:Array(32).fill({...mapping,windowClass:'x'.repeat(256)})})).settings?.browserMappings.length,32);
 assert.equal(P.parse(config({browserGrouping:'none',monitor:'all'})).settings?.browserGrouping,'none');
});
test('normal reading times are bounded and critical persistence is explicit',()=>{
 const p=P.defaults();assert.equal(P.duration(p,1,0),8000);assert.equal(P.duration(p,2,0),0);
 assert.equal(P.duration(p,1,999999),120000);p.criticalTimeoutSec=20;assert.equal(P.duration(p,2,0),20000);
});
test('exact duplicates group while separate actions and senders remain distinct',()=>{
 let groups=P.groups([row(3),row(2),row(1,{execArgv:'["other"]'})],true);
 assert.deepEqual(groups.map(g=>g.keys.length),[2,1]);assert.equal(groups[0].key,'1003-3');
 assert.equal(P.groups([row(2),row(1,{actionsJson:'[{"identifier":"reply","text":"Reply"}]'})],true).length,2);
 assert.equal(P.groups([row(2),row(1)],false).length,2);
 assert.equal(P.groups([row(2,{image:'image://one'}),row(1,{image:'image://two'})],true).length,2);
});
test('monitor selection handles unplugging, pointer fallback and all outputs',()=>{
 const p=P.defaults(),names=['DP-1','eDP-1'];assert.deepEqual(P.targetNames(p,names,'DP-1','eDP-1'),['DP-1']);
 p.monitor='pointer';assert.deepEqual(P.targetNames(p,names,'DP-1','eDP-1'),['eDP-1']);
 p.monitor='named';p.monitorName='missing';assert.deepEqual(P.targetNames(p,names,'DP-1',''),['DP-1']);
 p.monitor='all';assert.deepEqual(P.targetNames(p,names,'DP-1',''),names);
});
test('snapshots keep primitive live actions and restore never exposes dead callbacks',()=>{
 const snap=L.snapshotOf({id:1,summary:'Hi',body:'hello',desktopEntry:'app',transient:true,actions:[{identifier:'default',text:'Open'},{identifier:'read',text:'Mark read'}]},1000);
 assert.deepEqual(JSON.parse(snap.actionsJson),[{identifier:'read',text:'Mark read'}]);assert.equal(snap.transient,true);
 assert.equal(L.popupEntry(snap,1).actionsJson,'[]');assert.equal(L.popupEntry(snap,1).desktopEntry,'app');
});
test('sender markup never manufactures an image tag and command arguments stay structured',()=>{
 const M=require('./qml-js.cjs')('Markup.js'), body=(b)=>M.render(L.sanitizeBody(b,'Chat',''));
 assert.equal(body('<img src="https://example.invalid/x">'),'');
 // Stock 4.5 allowlist: unknown tags survive as escaped text, never as live markup.
 for(const b of ['<im<img src="x">g src="x">','<x\n<img src="x">','<font color="#000">hidden</font>','<h1>big</h1>','<a href="https://x.test">link</a>'])
  assert.ok(!/<(img|font|h1|a)\b/i.test(body(b)), b);
 assert.equal(body('<b>bold</b> &amp;amp; <i>it</i>\nnext'),'<b>bold</b> &amp; <i>it</i><br/>next');
 assert.equal(M.oneLine('Tom &amp;amp; Jerry <b>x</b>'),'Tom & Jerry x');
 // A click command runs as an argv, never through a shell; anything malformed runs nothing.
 for(const bad of ['echo unsafe','','[]','{}','[1]','[""]','["-rf"]',JSON.stringify(['a\u0000b']),JSON.stringify(Array(33).fill('ls')),JSON.stringify(['ls','x'.repeat(4097)])])
  assert.equal(L.parseExecArgv(bad),null,bad.slice(0,60));
 assert.deepEqual(L.parseExecArgv('["printf","$(touch nope)"]'),['printf','$(touch nope)']);
 assert.deepEqual(L.parseExecArgv(JSON.stringify(['ls',...Array(31).fill('x'.repeat(4096))]))?.length,32);
});

test('raw providers never render; explicit local image hints remain usable',()=>{
 const n={image:'image://qsimage/123',hints:{'image-path':'file:///tmp/avatar.png'}}
 assert.equal(L.snapshotOf(n,1000).image,'file:///tmp/avatar.png')
 for(const hint of ['image-data','image_data','icon_data']) {
  assert.equal(L.snapshotOf({image:'image://qsimage/123',hints:{[hint]:[1,1,4,true,8,4,[1]]}},1000).image,'')
 }
 assert.equal(L.snapshotOf({image:'file:///tmp/picture%20%23%25.png'},1000).image,'file:///tmp/picture%20%23%25.png')
 assert.equal(L.themeIcon('image://qsimage/123'),'')
 assert.equal(L.themeIcon('/tmp/original.jpg'),'')
 assert.equal(L.themeIcon('org.example.App'),'org.example.App')
 const normalized='file:///tmp/images/1000-1-image-'+ 'a'.repeat(64)+'.png'
 assert.equal(L.normalizedImage(normalized,'/tmp/images/'),normalized)
 for(const bad of ['file:///tmp/original.jpg','image://qsimage/123',normalized.replace('1000-1','../1000-1'),normalized+'.jpg'])
  assert.equal(L.normalizedImage(bad,'/tmp/images/'),'')
})

test('DND critical silencing overrides trusted critical alerts without changing normal action feedback',()=>{
 for(const appName of ['omarchy-action','notify-send','Chat']) {
  assert.equal(L.shouldBypassDnd({appName,urgency:2},2,true),false)
  assert.equal(L.shouldBypassDnd({appName,urgency:2},2,false),appName!=='Chat')
 }
 assert.equal(L.shouldBypassDnd({appName:'omarchy-action',urgency:1},2,true),true)
 assert.equal(L.shouldBypassDnd({appName:'Chat',urgency:1},2,true),false)
})
test('omarchy-actions hint becomes up to three structured --action buttons, numbered before sender actions',()=>{
 const hint=JSON.stringify([{label:'Open',argv:['mpv','/tmp/a b.mp4']},{label:'Omacut',argv:['omacut','/tmp/a b.mp4']}]);
 const snap=L.snapshotOf({id:7,summary:'Recording saved',hints:{'omarchy-actions':hint},actions:[{identifier:'reply',text:'Reply'},{identifier:'settings',text:'Settings'}]},1);
 assert.deepEqual(JSON.parse(snap.execActions).map(a=>a.label),['Open','Omacut']);
 assert.deepEqual(L.deeds(snap,{codes:'123456',link:''}).map(d=>d.kind+':'+d.value),['exec:0','exec:1','action:reply','code:123456','action:settings']);
 for(const bad of ['[]','[{"label":"x","argv":["-rf"]}]','[{"label":" ","argv":["ls"]}]',JSON.stringify(Array(4).fill({label:'x',argv:['ls']})),JSON.stringify([{label:'x',argv:['a\u0000b']}])])
  assert.equal(L.parseExecActions(bad),null,bad);
 assert.equal(L.popupEntry({...snap,execActions:'garbage'},1).execActions,'');
 assert.equal(L.popupEntry(snap,1).execActions,snap.execActions);
});
test('duplicates with different --action targets stay separate cards',()=>{
 const base={originalId:1,timestamp:1,app:'Recorder',summary:'Saved',body:'',appIcon:'',image:'',urgency:1,execArgv:'',actionsJson:'[]'}
 const a={...base,execActions:JSON.stringify([{label:'Open',argv:['mpv','/tmp/a.mp4']}])}
 const b={...base,originalId:2,timestamp:2,execActions:JSON.stringify([{label:'Open',argv:['mpv','/tmp/b.mp4']}])}
 assert.equal(P.groups([a,b],true,'browser',{browser:()=>false,hostname:()=>''}).length,2)
 assert.equal(P.groups([a,{...a,originalId:3,timestamp:3}],true,'browser',{browser:()=>false,hostname:()=>''}).length,1)
});
test('history replay lists live cards and history once each, newest first, without sender buttons, bounded',()=>{
 const raw=[JSON.stringify({timestamp:3,originalId:3,summary:'c',actionsJson:'[{"identifier":"reply","text":"Reply"}]'}),
  'not json',JSON.stringify({timestamp:2,originalId:2,summary:'b from history'}),JSON.stringify({timestamp:1,originalId:1,summary:'a'})].join('\n');
 const live=[{timestamp:2,originalId:2,summary:'b on screen'}];
 const rows=L.historyRows(raw,live,1,100);
 assert.deepEqual(rows.map(r=>r.summary),['c','b on screen','a']);
 assert.ok(rows.every(r=>r.actionsJson==='[]'),JSON.stringify(rows.map(r=>r.actionsJson)));
 assert.deepEqual(L.historyRows(raw,live,1,2).map(r=>r.summary),['c','b on screen']);
});
