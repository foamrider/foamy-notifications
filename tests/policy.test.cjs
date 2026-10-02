const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../Preferences.js');
const L = require('../NotificationLogic.js');
const row = (id, extra={}) => ({originalId:id,timestamp:1000+id,app:'Chat',desktopEntry:'chat',summary:'Message',body:'Hello',appIcon:'chat',urgency:1,execArgv:'',actionsJson:'[]',...extra});
test('invalid configuration retains a clear error instead of silently changing behavior',()=>{
 assert.match(P.parse('{').error,/SyntaxError/);
 assert.equal(P.parse(JSON.stringify({plugins:[{id:'foamy.notifications',maxVisible:0}]})).settings,null);
 assert.equal(P.parse(JSON.stringify({plugins:[{id:'foamy.notifications',monitor:'named'}]})).settings,null);
 assert.equal(P.parse(JSON.stringify({plugins:[{id:'foamy.notifications',suppressFullscreen:'false'}]})).settings,null);
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
 assert.equal(L.styledBody('<img src="https://example.invalid/x">','Chat',''),'');
 const nested='<im<img src="x">g src="x">';
 assert.equal(L.styledBody(nested,'Chat',''),nested);
 assert.ok(!L.styledBody('<x\n<img src="x">','Chat','').endsWith('<img src="x">'));
 assert.equal(L.parseExecArgv('echo unsafe'),null);assert.deepEqual(L.parseExecArgv('["printf","$(touch nope)"]'),['printf','$(touch nope)']);
});

test('shared image settings are optional and bounded',()=>{
 const config=e=>JSON.stringify({plugins:[{id:'foamy.notifications',...e}]});
 assert.equal(P.defaults().showImages,true);
 assert.equal(P.defaults().imageSize,56);
 assert.equal(P.parse(config({imageSize:73})).settings,null);
 assert.equal(P.parse(config({imageSize:39})).settings,null);
 assert.equal(P.parse(config({showImages:'true'})).settings,null);
 assert.equal(P.parse(config({showImages:false,imageSize:64})).settings.imageSize,64);
 assert.equal(P.groups([row(2,{image:'image://one'}),row(1,{image:'image://two'})],true).length,2);
});

test('compact defaults on and accepts only boolean overrides',()=>{
 const config=compact=>JSON.stringify({plugins:[{id:'foamy.notifications',compact}]});
 assert.equal(P.parse('{}').settings.compact,true);
 assert.equal(P.parse(config(false)).settings.compact,false);
 assert.equal(P.parse(config(true)).settings.compact,true);
 for(const invalid of ['false',0,null]) assert.match(P.parse(config(invalid)).error,/Invalid compact/);
});

test('timeout indicator defaults on and rejects non-boolean overrides',()=>{
 const config=showTimeoutIndicator=>JSON.stringify({plugins:[{id:'foamy.notifications',showTimeoutIndicator}]});
 assert.equal(P.parse('{}').settings.showTimeoutIndicator,true);
 assert.equal(P.parse(config(false)).settings.showTimeoutIndicator,false);
 for(const invalid of ['false',0,null]) assert.match(P.parse(config(invalid)).error,/Invalid showTimeoutIndicator/);
});
