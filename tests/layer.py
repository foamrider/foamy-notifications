"""Check the real Wayland popup surface on a private bus with temporary history."""
import os,json,shutil,tempfile,subprocess,sys,time,signal
from pathlib import Path
import hostenv
if '--inside' not in sys.argv:
 raise SystemExit(subprocess.run(['dbus-run-session','--','python3',__file__,'--inside'],timeout=45).returncode)
base=Path(tempfile.mkdtemp(prefix='foamy-native-motion-'));app=base/'app';app.mkdir();home=base/'home';home.mkdir()
for n in ['Commons','Ui']:(app/n).symlink_to(hostenv.root()/'shell'/n,target_is_directory=True)
shutil.copytree(Path(__file__).resolve().parents[1],app/'plugin',ignore=shutil.ignore_patterns('.git','__pycache__'))
p=app/'plugin/Service.qml';s=p.read_text();s=s.replace('  id: service','''  id: service
  property var surfaces: ({})
  property var trace: []
  IpcHandler {target: "motion-review"
    function state(): string {
      return JSON.stringify({trace:service.trace,visible:Object.keys(service.surfaces).some(function(key){return service.surfaces[key].visible})})
    }
    // Per output: the room under the bar and the height the stack needs.
    function fit(): string {
      var out={}
      for(var key in service.surfaces){var w=service.surfaces[key];if(w.selected)out[key]={room:w.height-w.stack.y-Style.space(8),need:w.stack.settledHeight(),cards:w.stack.count}}
      return JSON.stringify(out)
    }
  }
''',1)
s=s.replace('      id: popupWindow','''      id: popupWindow
      Component.onCompleted: service.surfaces[screen.name]=popupWindow
      readonly property var stack: popupColumn
      property bool capturedEntry: false
      property bool capturedExit: false
      FrameAnimation {running: popupWindow.visible; onTriggered: {
        var children=popupColumn.contentItem.children
        for(var i=0;i<children.length;i++) {
          var card=children[i]
          if(!("entranceOffset" in card))continue
          if(service.trace.length<500) service.trace.push({opacity:card.opacity,offset:card.entranceOffset,retired:card.retired,visible:popupWindow.visible})
          var name=""
          if(!card.retired && card.opacity>0.2 && card.opacity<0.9 && !popupWindow.capturedEntry){name="entrance";popupWindow.capturedEntry=true}
          if(card.retired && card.opacity>0.1 && card.opacity<0.9 && !popupWindow.capturedExit){name="exit";popupWindow.capturedExit=true}
          if(name)popupWindow.contentItem.grabToImage(function(result){result.saveToFile(Qt.resolvedUrl(name+".png").toString().replace("file://",""))})
        }
      }}
''')
p.write_text(s)
(app/'shell.qml').write_text('import QtQuick\nimport Quickshell\nimport "plugin" as Plugin\nShellRoot { Plugin.Service {} }\n')
config=home/'.config/omarchy';config.mkdir(parents=True);(config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','normalTimeoutSec':1}]}))
env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),XDG_STATE_HOME=str(home/'.local/state'),QT_QPA_PLATFORM='wayland',QT_QPA_PLATFORMTHEME='basic',QT_QUICK_BACKEND='rhi')
log=(base/'log').open('w');proc=subprocess.Popen(['qs','-p',str(app),'--no-color'],env=env,stdout=log,stderr=log,start_new_session=True)
def ipc(target,*args):return subprocess.check_output(['qs','ipc','-n','-p',str(app),'call',target,*args],env=env,text=True,stderr=subprocess.DEVNULL,timeout=2).strip()
try:
 end=time.monotonic()+5
 while True:
  try:
   if ipc('notifications','ping')=='ok':break
  except Exception:pass
  assert time.monotonic()<end,'startup';time.sleep(.1)
 subprocess.run(['notify-send','-a','Foamy animation check','-u','normal','Animation B','Synthetic notification on an isolated bus'],env=env,check=True,timeout=2)
 time.sleep(2)
 state=json.loads(ipc('motion-review','state'));(base/'trace.json').write_text(json.dumps(state))
 assert any(0<r['opacity']<1 and r['offset']>0 and not r['retired'] for r in state['trace']), 'no entrance frames'
 assert any(0<r['opacity']<1 and r['retired'] and r['visible'] for r in state['trace']), 'window hid before exit completed'
 assert not state['visible'],'empty notification layer remains mapped'
 print('PASS real layer-shell: gentle entrance, visible final-card fade, automatic surface close')
 # Cards that do not fit the real screen wait in the overflow queue, not in history.
 (config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','maxVisible':10,'criticalTimeoutSec':0}]}))
 def service():return json.loads(ipc('foamy.notifications','state'))
 end=time.monotonic()+5
 while service()['settings']['maxVisible']!=10:assert time.monotonic()<end,'config';time.sleep(.1)
 for i in range(20):subprocess.run(['notify-send','-a','Foamy screen-space check','-u','critical','Tall card '+str(i),'First line\nSecond line\nThird line'],env=env,check=True,timeout=2)
 end=time.monotonic()+8;last=None;steady=0
 while steady<10:
  now=(service()['visibleLimit'],len(service()['groups']),len(service()['queue']),json.loads(ipc('motion-review','fit')))
  steady=steady+1 if now==last and not service()['busy'] else 0;last=now
  assert time.monotonic()<end,'screen-space queue did not settle: '+repr(now);time.sleep(.1)
 limit,shown,waiting,fit=last
 history=home/'.local/state/foamy-notifications/history'
 assert shown+waiting==20 and waiting>0,last
 assert not any(json.loads(f.read_text())['summary'].startswith('Tall card') for f in history.glob('*.json')),'screen-space overflow went to history'
 assert all(v['need']<=v['room'] for v in fit.values()),fit
 if shown<10:assert limit==shown,last
 ipc('notifications','dismissOne')
 end=time.monotonic()+5
 while not (len(service()['groups'])==shown and len(service()['queue'])==waiting-1):assert time.monotonic()<end,'a freed slot shows the next card';time.sleep(.1)
 ipc('notifications','dismissAll')
 print('PASS real layer-shell screen space: '+str(shown)+' of 20 cards fit '+json.dumps(fit)+', the rest wait (limit '+str(limit)+')')
finally:
 os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=3);log.close();print('Native evidence:',base)
