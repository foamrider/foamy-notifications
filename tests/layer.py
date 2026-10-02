"""Check the real Wayland popup surface on a private bus with temporary history."""
import os,json,shutil,tempfile,subprocess,sys,time,signal
from pathlib import Path
if '--inside' not in sys.argv:
 raise SystemExit(subprocess.run(['dbus-run-session','--','python3',__file__,'--inside'],timeout=20).returncode)
base=Path(tempfile.mkdtemp(prefix='foamy-native-motion-'));app=base/'app';app.mkdir();home=base/'home';home.mkdir()
for n in ['Commons','Ui']:(app/n).symlink_to('/usr/share/omarchy/shell/'+n,target_is_directory=True)
shutil.copytree(Path(__file__).resolve().parents[1],app/'plugin',ignore=shutil.ignore_patterns('.git','__pycache__'))
p=app/'plugin/Service.qml';s=p.read_text();s=s.replace('  id: service','''  id: service
  property var surfaces: ({})
  property var trace: []
  IpcHandler {target: "motion-review"
    function state(): string {
      return JSON.stringify({trace:service.trace,visible:Object.keys(service.surfaces).some(function(key){return service.surfaces[key].visible})})
    }
  }
''',1)
s=s.replace('      id: popupWindow','''      id: popupWindow
      Component.onCompleted: service.surfaces[screen.name]=popupWindow
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
finally:
 os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=3);log.close();print('Native evidence:',base)
