"""Real popup intake and GPU rendering, isolated from the user's notification server."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    raise SystemExit(subprocess.run(['dbus-run-session','--',sys.executable,__file__,'--inside'],timeout=55).returncode)
base = Path(tempfile.mkdtemp(prefix='foamy-popup-images-'))
app = base/'app';app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons',target_is_directory=True)
shutil.copytree(SOURCE,app/'plugin',ignore=shutil.ignore_patterns('.git','__pycache__'))
service = (SOURCE/'Service.qml').read_text()
start = service.index('          NotificationCard {',service.index('  Variants {'))
end = service.index('\n      }\n    }\n  }',start)
delegate = service[start:end].replace('NotificationCard {','Cards.NotificationCard {').replace('service.','notifications.').replace('popupWindow.screen.name','"TEST"')
(app/'plugin/Service.qml').write_text(service[:service.rfind('  Variants {')]+'}\n')
(app/'shell.qml').write_text('''import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import qs.Commons
import "plugin" as Plugin
import "plugin/components" as Cards
ShellRoot {
 Plugin.Service { id:notifications }
 FloatingWindow {
  id:window;visible:true;implicitWidth:600;implicitHeight:500;color:Color.background
  Cards.NotificationStack {
   id:popupColumn;x:20;y:20;width:520;spacing:8;groups:notifications.displayGroups
   delegate:DELEGATE
  }
 }
 TestCase { id:tests;name:"PopupImages";when:false }
 IpcHandler {
  target:"images-test"
  function state():string {
   var card=popupColumn.itemAtIndex(0)
   var image=card ? tests.findChild(card,"notificationImage"):null,icon=card ? tests.findChild(card,"notificationAppIcon"):null
   return JSON.stringify({count:notifications.popupModel.count,busy:notifications.popupFileQueue.length>0,
    image:image ? {source:String(image.source),status:image.status}:null,
    icon:icon ? {source:String(icon.source),status:icon.status}:null,
    title:card ? card.modelData.summary:"",
    error:notifications.lastError})
  }
  function capture(name:string,width:int,light:bool):void {
   popupColumn.width=width
   Color.background=light ? "#eff1f5":"#24273a";Color.foreground=light ? "#4c4f69":"#cad3f5";Color.accent=light ? "#1e66f5":"#8aadf4"
   captureTimer.name=name;captureTimer.restart()
  }
 }
 Timer {id:captureTimer;property string name:"";interval:200
  onTriggered:popupColumn.grabToImage(function(r){r.saveToFile(Qt.resolvedUrl(captureTimer.name+".png").toString().replace("file://",""))})
 }
}
'''.replace('DELEGATE',delegate))
home = base/'home';home.mkdir()
config = home/'.config/omarchy';config.mkdir(parents=True)
(config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','maxVisible':1}]}))
cli = base/'bin';cli.mkdir()
(cli/'hyprctl').write_text('#!/bin/sh\nprintf "[]\\n"\n');(cli/'hyprctl').chmod(0o755)
(cli/'omarchy-shell').write_text('#!/bin/sh\nexit 0\n');(cli/'omarchy-shell').chmod(0o755)
runtime = base/'runtime';runtime.mkdir(mode=0o700)
env = dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),XDG_CACHE_HOME=str(base/'cache'),XDG_STATE_HOME=str(base/'state'),XDG_RUNTIME_DIR=str(runtime),PATH=str(cli)+':'+os.environ['PATH'],QT_QPA_PLATFORM='wayland',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion')
env['WAYLAND_DISPLAY']=str(Path(os.environ['XDG_RUNTIME_DIR'])/os.environ['WAYLAND_DISPLAY'])
for k in ['QT_QUICK_BACKEND','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(k,None)
picture=base/'picture.png';icon=base/'progressive.jpg'
subprocess.run(['magick','-size','1440x900','gradient:#8aadf4-#a6da95',str(picture)],check=True,timeout=5)
subprocess.run(['magick','-size','64x64','xc:none','-fill','steelblue','-draw','circle 32,32 32,12','-interlace','Plane',str(icon)],check=True,timeout=5)
# Legacy on-screen images must be normalized before the first restored card renders.
legacy=base/'state/omarchy/notifications';legacy.mkdir(parents=True)
stamp=int(time.time()*1000)
(legacy/(str(stamp)+'-999.json')).write_text(json.dumps({'timestamp':stamp,'originalId':999,'id':999,'app':'Image test','appIcon':str(icon),'image':picture.as_uri(),'summary':'Restored file images','body':'Legacy pictures remain available.','urgency':2}))
log = (base/'runtime.log').open('w')
process = subprocess.Popen(['quickshell','-p',str(app),'--no-color'],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)

def ipc(method,*args):
    return subprocess.check_output(['qs','ipc','-n','-p',str(app),'call','images-test',method,*map(str,args)],env=env,text=True,timeout=3).strip()

def state():return json.loads(ipc('state'))

def wait(check,label):
    until=time.monotonic()+10
    while time.monotonic()<until:
        try:
            if check():return
        except (ValueError,subprocess.SubprocessError):pass
        if process.poll() is not None:raise AssertionError('Shell exited: '+label)
        time.sleep(.05)
    raise AssertionError('Timed out: '+label+' '+json.dumps(state()))

def send(title,hints='{}',icon='foot',replace=0):
    out=subprocess.check_output(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.Notify','Foamy image test',str(replace),icon,title,'Picture with working text and actions','[]',hints,'0'],env=env,text=True,timeout=3)
    return int(out.split('uint32 ')[1].split(',')[0])

def capture(name,width=520,light=False):
    ipc('capture',name,width,str(light).lower())
    wait(lambda:(app/(name+'.png')).exists(),'capture '+name)

try:
    wait(lambda:state()['title']=='Restored file images' and state()['image'] and state()['image']['status']==1 and state()['icon']['status']==1,'legacy images normalized before restore')
    assert state()['image']['source'].endswith('.png') and state()['icon']['source'].endswith('.png'),state()
    capture('images-restored')
    subprocess.run(['qs','ipc','-n','-p',str(app),'call','notifications','dismissAll'],env=env,check=True,capture_output=True,timeout=3)
    wait(lambda:state()['count']==0,'restore dismissed')
    identifier=send('File images retained',"{'image-path': <'"+picture.as_uri()+"'>}",str(icon))
    wait(lambda:state()['title']=='File images retained' and state()['image'] and state()['image']['status']==1 and state()['icon']['status']==1,'normalized real popup images')
    for role in ['image','icon']:
        assert '-'+('image' if role=='image' else 'appIcon')+'-' in state()[role]['source'],state()
        assert state()[role]['source'].endswith('.png')
    for width,label in [(520,'wide'),(280,'narrow')]:
        for light,theme in [(False,'dark'),(True,'light')]:capture('images-'+label+'-'+theme,width,light)
    # The dangerous data remains in Quickshell's provider, which the card never requests.
    for alias in ['image-data','image_data','icon_data']:
        identifier=send('Raw buffer safely omitted '+alias,"{'"+alias+"': <(4096, 4096, 16384, true, 8, 4, [byte 255])>}",replace=identifier)
        wait(lambda:state()['title']=='Raw buffer safely omitted '+alias and state()['image']['source']=='','truncated '+alias+' omitted')
        assert state()['icon']['source'].startswith('image://icon/'),state()
    capture('images-raw-fallback',280)
    identifier=send('Raw image with file fallback',"{'image-data': <(4096, 4096, 16384, true, 8, 4, [byte 255])>, 'image-path': <'"+picture.as_uri()+"'>}",replace=identifier)
    wait(lambda:state()['title']=='Raw image with file fallback' and state()['image']['status']==1,'raw buffer bypassed with local file')
    # File-backed allocation attacks fail in the bounded worker, leaving text and theme icon.
    data=bytearray(icon.read_bytes());frame=data.index(b'\xff\xc2');data[frame+5:frame+9]=(60000).to_bytes(2,'big')*2
    rejected=base/'rejected.jpg';rejected.write_bytes(data)
    identifier=send('Rejected file keeps text',"{'image-path': <'"+rejected.as_uri()+"'>}",replace=identifier)
    wait(lambda:state()['title']=='Rejected file keeps text' and state()['image']['source']=='','invalid file fallback')
    capture('images-rejected-file',280)
    assert process.poll() is None
    assert not state()['error'],state()
    print('PASS: actual popup file normalization, progressive JPEG icon, wide/narrow dark/light rendering, all raw-data aliases, truncated-buffer rejection, local-file fallback, replacements, rejected-image fallback')
finally:
    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3);log.close()
    print('Image captures:',app)
    print('Runtime log:',base/'runtime.log')
