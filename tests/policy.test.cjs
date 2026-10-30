const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../Preferences.js');
const L = require('../NotificationLogic.js');
const row = (id, extra={}) => ({originalId:id,timestamp:1000+id,app:'Chat',desktopEntry:'chat',summary:'Message',body:'Hello',appIcon:'chat',urgency:1,execArgv:'',actionsJson:'[]',...extra});
test('X history removal defaults off and requires an explicit boolean',()=>{
 assert.equal(P.parse('{}').settings.closeButtonRemovesHistory,false)
 for(const value of [true,false]) assert.equal(P.parse(JSON.stringify({plugins:[{id:'foamy.notifications',closeButtonRemovesHistory:value}]})).settings.closeButtonRemovesHistory,value)
 for(const value of ['true',1,null]) assert.match(P.parse(JSON.stringify({plugins:[{id:'foamy.notifications',closeButtonRemovesHistory:value}]})).error,/Invalid closeButtonRemovesHistory/)
});
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
 const M=require('./qml-js.cjs')('Markup.js'), body=(b)=>M.render(L.sanitizeBody(b,'Chat',''));
 assert.equal(body('<img src="https://example.invalid/x">'),'');
 // Stock 4.5 allowlist: unknown tags survive as escaped text, never as live markup.
 for(const b of ['<im<img src="x">g src="x">','<x\n<img src="x">','<font color="#000">hidden</font>','<h1>big</h1>','<a href="https://x.test">link</a>'])
  assert.ok(!/<(img|font|h1|a)\b/i.test(body(b)), b);
 assert.equal(body('<b>bold</b> &amp;amp; <i>it</i>\nnext'),'<b>bold</b> &amp; <i>it</i><br/>next');
 assert.equal(M.oneLine('Tom &amp;amp; Jerry <b>x</b>'),'Tom & Jerry x');
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


test('DND critical silencing defaults off and rejects non-booleans',()=>{
 assert.equal(P.parse('{}').settings.dndSilenceCritical,false)
 const config=value=>JSON.stringify({plugins:[{id:'foamy.notifications',dndSilenceCritical:value}]})
 for(const value of [true,false]) assert.equal(P.parse(config(value)).settings.dndSilenceCritical,value)
 for(const value of ['true',1,null]) assert.match(P.parse(config(value)).error,/Invalid dndSilenceCritical/)
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
 for(const bad of ['[]','[{"label":"x","argv":["-rf"]}]','[{"label":" ","argv":["ls"]}]',JSON.stringify(Array(4).fill({label:'x',argv:['ls']})),'[{"label":"x","argv":["a\u0000b"]}]'])
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
