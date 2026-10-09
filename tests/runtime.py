"""Exercise real desktop-notification D-Bus traffic on a private session bus."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import struct
import zlib

# Optional real center checkout enables the cross-plugin archive/badge regression.
CENTER=os.environ.get('FOAMY_CENTER_SOURCE','')

SOURCE=Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    result=subprocess.run(['dbus-run-session','--','python3',__file__,'--inside'],timeout=55)
    raise SystemExit(result.returncode)
base=Path(tempfile.mkdtemp(prefix='foamy-notifications-runtime-'))
app=base/'app';app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons',target_is_directory=True)
(app/'Ui').symlink_to('/usr/share/omarchy/shell/Ui',target_is_directory=True)
# Offscreen Qt has no layer-shell backend. Keep the real service and D-Bus server,
# with presentation tested independently by render.py and the installed desktop.
shutil.copytree(SOURCE,app/'plugin',ignore=shutil.ignore_patterns('__pycache__'))
service=(SOURCE/'Service.qml').read_text()
service=service[:service.rfind('  Variants {')]+'}\n'
service=service.replace('Quickshell.screens.map(function(s) { return s.name })', '["TEST-1"]')
(app/'plugin/Service.qml').write_text(service)
home=base/'home';home.mkdir()
runtime=base/'runtime';runtime.mkdir(mode=0o700)
config=home/'.config/omarchy';config.mkdir(parents=True)
(config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','maxVisible':3}]}))
bin_dir=base/'bin';bin_dir.mkdir()
context_path=base/'context.json'
dispatch_path=base/'dispatches.jsonl'
context_path.write_text(json.dumps({'clients':[{'class':'Foamy Test','address':'0x123'}],'monitors':[{'name':'TEST-1','x':0,'y':0,'width':1920,'height':1080,'scale':1,'activeWorkspace':{'id':1}}],'cursorpos':{'x':100,'y':100}}))
(bin_dir/'hyprctl').write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
data=json.loads(Path(CONTEXT_PATH).read_text())
if sys.argv[1] == 'dispatch':
    with Path(DISPATCH_PATH).open('a') as stream: stream.write(json.dumps(sys.argv[2:])+'\\n')
command=next((arg for arg in sys.argv[1:] if arg in data),None)
print(json.dumps(data[command]) if command else "ok")
'''.replace('CONTEXT_PATH',repr(str(context_path))).replace('DISPATCH_PATH',repr(str(dispatch_path))))
(bin_dir/'hyprctl').chmod(0o755)
if CENTER:
    shutil.copytree(CENTER,app/'center',ignore=shutil.ignore_patterns('.git','__pycache__'))
    center_service=app/'center/Service.qml'
    center_service.write_text(center_service.read_text().replace('    function seed(count: int): string {', '    function focus(key: string): string { root.focusNotification(root.entries.find(function(row) { return row.key === key })); return "queued" }\n    function seed(count: int): string {'))
    candidate=os.environ.get('FOAMY_CENTER_SERVICE','')
    if candidate:shutil.copy(candidate,app/'center/Service.qml')
    plugins=config/'plugins';plugins.mkdir()
    (plugins/'foamy.notification-center').symlink_to(app/'center',target_is_directory=True)
    (plugins/'foamy.notifications').symlink_to(app/'plugin',target_is_directory=True)
    (config/'shell.json').write_text(json.dumps({'bar':{'layout':{'right':[{'id':'foamy.notification-center'}]}},'plugins':[{'id':'foamy.notifications','maxVisible':3}]}))
(bin_dir/'omarchy-shell').write_text('#!/bin/sh\n[ "$1" = "-q" ] && shift\nexec qs ipc -n -p '+str(app)+' call "$@"\n')
(bin_dir/'omarchy-shell').chmod(0o755)
# Exercise the installed stack delegate rather than a second copy of its bindings.
full_service=(SOURCE/'Service.qml').read_text()
card_start=full_service.index('          NotificationCard {',full_service.index('  Variants {'))
card_end=full_service.index('\n      }\n    }\n  }',card_start)
card_delegate=full_service[card_start:card_end].replace('NotificationCard {', 'Cards.NotificationCard {').replace('service.', 'notifications.').replace('popupWindow.screen.name', '"TEST-1"')
(app/'shell.qml').write_text('''import QtQuick
import QtTest
import qs.Commons
import Quickshell
import "plugin" as Plugin
import Quickshell.Io
import "plugin/components" as Cards
ShellRoot {
 Plugin.Service { id: notifications }
 FloatingWindow { id: testWindow; visible:true;implicitWidth:500;implicitHeight:600
  Cards.NotificationStack { id:popupColumn;x:20;y:20;width:420;spacing:8;groups:notifications.displayGroups
   delegate: STACK_DELEGATE
  }
  TestCase {id:input;name:"StackRuntimeInput";when:false}
 }
 Cards.NotificationCard {
  id: countdown
  property int progressUpdates: 0
  onRemainingMsChanged: progressUpdates++
  property var group: notifications.displayGroups[0] || null
  showTimeoutIndicator: notifications.preferences.showTimeoutIndicator
  timeoutMs: group ? notifications.durationFor(group.urgency, group.expireTimeout) : 0
  remainingMs: showTimeoutIndicator ? notifications.remainingFor(group) : 0
 }
 QtObject { id:testState; property var heldCard:null }
 IpcHandler {
  target: "countdown-test"
  function capture(name: string): void {
   popupColumn.grabToImage(function(result) { result.saveToFile(Qt.resolvedUrl(name+".png").toString().replace("file://","")) })
  }
  function hoverLast(): void {
   testState.heldCard=popupColumn.itemAtIndex(popupColumn.count-1)
   input.mouseMove(testState.heldCard,100,50)
  }
  function leave(): void { input.mouseMove(testWindow.contentItem,480,580) }
  function clickFirst(): void { input.mouseClick(popupColumn.itemAtIndex(0),100,50,Qt.LeftButton) }
  function stackState(): string {
   var settled=true
   for(var i=0;i<popupColumn.count;i++){var card=popupColumn.itemAtIndex(i);if(!card || card.entranceOffset!==0)settled=false}
   return JSON.stringify({count:popupColumn.count,settled:settled,held:popupColumn.holdPositions,
    y:testState.heldCard?testState.heldCard.mapToItem(popupColumn,0,0).y:-1,hovered:testState.heldCard?testState.heldCard.hovered:false,
    sameCard:popupColumn.count===1 && popupColumn.itemAtIndex(0)===testState.heldCard})
  }
  function state(): string { return JSON.stringify({updates:countdown.progressUpdates,remaining:countdown.remainingMs,progress:countdown.timeoutProgress,duration:countdown.timeoutMs,enabled:countdown.showTimeoutIndicator}) }
  function pause(mode: string): void {
   var key=notifications.displayGroups[0].key
   notifications.hoveredKeys["TEST-1|"+key]=mode === "hover"
   var busy={};if(mode === "busy")busy[key]=true;notifications.busyKeys=busy
  }
 }
}
'''.replace('STACK_DELEGATE',card_delegate).replace('ShellRoot {', 'import \"center\" as Center\nimport qs.Ui as Ui\nShellRoot { Center.Service {} Ui.Panel { ipcTarget: "foamy.notification-center" }' if CENTER else 'ShellRoot {'))
env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),XDG_STATE_HOME=str(home/'.local/state'),XDG_RUNTIME_DIR=str(runtime),QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion',PATH=str(bin_dir)+':'+os.environ['PATH'])
for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
log=(base/'runtime.log').open('w')
process=subprocess.Popen(['quickshell','-p',str(app),'--no-color'],env=env,stdout=log,stderr=log,start_new_session=True)
def ipc(target,*args):
    result=subprocess.run(['qs','ipc','-n','-p',str(app),'call',target,*args],env=env,capture_output=True,text=True,timeout=3)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip()
def state():return json.loads(ipc('foamy.notifications','state'))
def wait_for(predicate,message):
    end=time.monotonic()+5
    while time.monotonic()<end:
        try:
            if predicate():return
        except (ValueError,RuntimeError,subprocess.TimeoutExpired):pass
        time.sleep(.1)
    print('Failure state:',state(),flush=True)
    raise AssertionError(message+'\n'+(base/'runtime.log').read_text())
def send(summary,extra=(),body='Sample body'):
    return subprocess.run(['notify-send','-p','-a','Foamy Test','-u','critical',*extra,summary,body],env=env,capture_output=True,text=True,timeout=3).stdout.strip()
def dispatches():
    return dispatch_path.read_text().splitlines() if dispatch_path.exists() else []
try:
    wait_for(lambda:ipc('notifications','ping')=='ok','service startup')
    if CENTER:
        wait_for(lambda:state()['centerEnabled'],'center config loaded')
        ipc('foamy.notification-center','close')
    first=send('Duplicate');send('Duplicate')
    wait_for(lambda:len(state()['groups'])==1 and state()['groups'][0]['count']==2,'duplicate grouping')
    assert json.loads(ipc('countdown-test','state'))['progress']==0,'persistent popup showed a timeout'
    wait_for(lambda:not state()['busy'],'writes drain')
    if CENTER:
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==2,'center ingests duplicate group')
    ipc('notifications','invokeLast')
    wait_for(lambda:state()['popups']==0,'handled click removes duplicate group')
    if CENTER:
        time.sleep(.3)
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0 and json.loads(ipc('foamy.notification-center.test','state'))['unread']==0,'handled click removes center entry and badge')
        tombstones=(home/'.local/state/omarchy-notification-center/dismissed').read_text().splitlines()
        assert len(tombstones)==2 and len(set(tombstones))==2,'handled keys were removed more than once'
        replacement=send('Before replacement')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['newest']=='Before replacement','initial center entry')
        send('After replacement',('-r',replacement))
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['newest']=='After replacement','center receives in-place updates')
        ipc('foamy.notification-center.test','focus',state()['groups'][0]['key'])
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'center uses stock focus and removes only on success')
        assert not json.loads(ipc('foamy.notification-center.test','state'))['focusError']
        ipc('notifications','dismissAll')
        wait_for(lambda:not state()['busy'],'focus cleanup drains')
        # A failed/ambiguous target must keep the center entry available for retry.
        ctx=json.loads(context_path.read_text());ctx['clients'].append({'class':'Foamy Test','address':'0x456'})
        context_path.write_text(json.dumps(ctx))
        send('Ambiguous popup focus')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'popup failure ingested')
        ipc('notifications','invokeLast')
        wait_for(lambda:bool(state()['lastError']) and not state()['busy'],'popup failure reports error')
        assert state()['popups']==1
        assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'failed popup lost center history'
        assert len(list((home/'.local/state/omarchy/notifications').glob('*.json')))==1,'failed popup lost persistence'
        ctx['clients']=ctx['clients'][:1];context_path.write_text(json.dumps(ctx))
        ipc('notifications','invokeLast')
        wait_for(lambda:state()['popups']==0 and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'popup retry succeeds')
        saved_clients=ctx['clients'];ctx['clients']=[];context_path.write_text(json.dumps(ctx))
        send('Unavailable center focus')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'ambiguous entry ingested')
        key=state()['groups'][0]['key']
        ipc('foamy.notification-center.test','focus',key)
        wait_for(lambda:bool(json.loads(ipc('foamy.notification-center.test','state'))['focusError']),'ambiguous focus reports error')
        assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'failed focus removed notification'
        ctx['clients']=saved_clients;context_path.write_text(json.dumps(ctx))
        ipc('foamy.notification-center.test','focus',key)
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'focus retry succeeds')
        ipc('notifications','dismissAll');wait_for(lambda:not state()['busy'],'retry cleanup drains')
        time.sleep(.4)
        reads=json.loads(ipc('foamy.notification-center.test','state'))['listLoads']
        time.sleep(10.2)
        assert json.loads(ipc('foamy.notification-center.test','state'))['listLoads']==reads,'idle center still polls the archive'

    assert not list((home/'.local/state/omarchy/notifications/history').glob('*.json')),'handled click entered history'
    # Use Vivaldi's actual identity format through both consumers of the shared helper.
    ctx=json.loads(context_path.read_text());original_clients=ctx['clients']
    browser={'class':'vivaldi-stable','address':'0x100'}
    teams={'class':'vivaldi-teams.microsoft.com__-Profile_1','address':'0x101'}
    other_teams={'class':'vivaldi-teams.microsoft.com__-Profile_2','address':'0x102'}
    cfg=json.loads((config/'shell.json').read_text())
    for target in (['popup','center'] if CENTER else ['popup']):
        for ambiguous in [False,True]:
            ctx['clients']=[browser,teams]+([other_teams] if ambiguous else [])
            context_path.write_text(json.dumps(ctx))
            cfg['plugins'][0]['browserMappings']=[];(config/'shell.json').write_text(json.dumps(cfg))
            wait_for(lambda:not state()['settings']['browserMappings'],'clear browser mapping')
            send('Teams identity '+target,('-a','Vivaldi','-h','string:desktop-entry:vivaldi-stable'),body='teams.microsoft.com\nAlex: Hi')
            wait_for(lambda:state()['popups']==1 and not state()['busy'],'Teams popup ready')
            key=state()['groups'][0]['key']
            if CENTER:
                wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'Teams center entry ready')
            if target=='center':
                ipc('notifications','dismissAll')
                wait_for(lambda:state()['popups']==0 and not state()['busy'],'archive Teams popup')
            else:
                wait_for(lambda:json.loads(ipc('countdown-test','stackState'))['settled'],'Teams card ready for click')
            before=len(dispatches())
            def click_teams():
                if target=='popup':ipc('countdown-test','clickFirst')
                else:ipc('foamy.notification-center.test','focus',key)
            click_teams()
            if ambiguous and target=='popup':
                wait_for(lambda:bool(state()['lastError']) if target=='popup' else bool(json.loads(ipc('foamy.notification-center.test','state'))['focusError']),'ambiguous Teams target reports failure')
                assert len(dispatches())==before,'ambiguous Teams click dispatched focus'
                if CENTER:assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'ambiguous Teams click removed history'
                cfg['plugins'][0]['browserMappings']=[{'origin':'teams.microsoft.com','windowClass':teams['class']}]
                (config/'shell.json').write_text(json.dumps(cfg))
                wait_for(lambda:bool(state()['settings']['browserMappings']),'Teams mapping loaded')
                click_teams()
            wait_for(lambda:len(dispatches())==before+1,'Teams focus dispatched once')
            expected='0x101' if target=='popup' else '0x100'
            assert json.loads(dispatches()[-1])==['hl.dsp.focus({ window = "address:'+expected+'" })'],'incorrect focus fallback'
            if target=='popup':wait_for(lambda:state()['popups']==0 and not state()['busy'],'Teams popup handled')
            if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'Teams history handled')
            ipc('countdown-test','leave')
    # Both entry points must deliver the native callback without local window focus.
    for target in (['popup','center'] if CENTER else ['popup']):
        before=len(dispatches())
        action=subprocess.Popen(['notify-send','-a','Vivaldi','-u','critical','--wait','--action=default=Open','Teams live callback','teams.microsoft.com\nAlex: Hi'],env=env,stdout=subprocess.PIPE,text=True)
        wait_for(lambda:state()['popups']==1 and json.loads(ipc('countdown-test','stackState'))['settled'],'native Teams popup ready')
        if target=='center':
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'live callback archived')
            ipc('foamy.notification-center.test','focus',state()['groups'][0]['key'])
        else:ipc('countdown-test','clickFirst')
        output=action.communicate(timeout=4)[0].strip();assert output=='default',output
        wait_for(lambda:state()['popups']==0 and not state()['busy'],'native callback handled')
        assert len(dispatches())==before,'native callback unexpectedly focused a window'
        if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'native callback clears center')
    if CENTER:
        # Hiding a popup must not send NotificationClosed before the history click.
        cfg['plugins'][0]['normalTimeoutSec']=1;(config/'shell.json').write_text(json.dumps(cfg))
        wait_for(lambda:state()['settings']['normalTimeoutSec']==1,'short history test timeout')
        ipc('countdown-test','leave')
        for mode in ['expiry','dismiss','silenced','remove','clear','sender-close']:
            if mode=='silenced':ipc('notifications','setDnd','on')
            before=len(dispatches())
            action=subprocess.Popen(['notify-send','-a','Vivaldi','-u','normal','-t','0','--wait','--action=default=Open','Zendesk history '+mode,'Ticket update'],env=env,stdout=subprocess.PIPE,text=True)
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'history callback archived '+mode)
            if mode=='silenced':
                wait_for(lambda:state()['retainedActions']==1,'silenced callback retained')
                files=list((home/'.local/state/omarchy/notifications/history').glob('*-*.json'))
                key=next(p.stem for p in files if json.loads(p.read_text()).get('summary')=='Zendesk history '+mode)
                ipc('notifications','setDnd','off')
            else:
                key=state()['groups'][0]['key']
                if mode!='expiry':ipc('notifications','dismissAll')
            wait_for(lambda:state()['popups']==0 and state()['retainedActions']==1 and not state()['busy'],'popup gone but callback retained '+mode)
            assert action.poll() is None,'popup closure destroyed native callback '+mode
            if mode=='remove':
                ipc('foamy.notification-center.store','remove',key)
            elif mode=='clear':ipc('foamy.notification-center.test','clear')
            elif mode=='sender-close':
                subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.CloseNotification',key.split('-')[1]],env=env,check=True,capture_output=True,timeout=3)
                wait_for(lambda:state()['retainedActions']==0,'sender closure releases callback')
                assert ipc('foamy.notifications','invokeDefault',key)=='unavailable'
                ipc('foamy.notification-center.test','clear')
            else:ipc('foamy.notification-center.test','focus',key)
            output=action.communicate(timeout=4)[0].strip()
            assert output==('' if mode in ['remove','clear','sender-close'] else 'default'),(mode,output)
            wait_for(lambda:state()['retainedActions']==0 and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'history callback cleaned '+mode)
            assert len(dispatches())==before,'history action unexpectedly focused another browser window'
    ctx['clients']=original_clients;context_path.write_text(json.dumps(ctx))
    cfg['plugins'][0]['browserMappings']=[];(config/'shell.json').write_text(json.dumps(cfg))
    send('Dismiss me')
    wait_for(lambda:state()['popups']==1 and not state()['busy'],'new popup')
    ipc('notifications','dismissOne')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'dismiss drains')
    assert list((home/'.local/state/omarchy/notifications/history').glob('*.json')),'dismiss did not retain history'
    ipc('notifications','setDnd','on');send('Silenced')
    wait_for(lambda:not state()['busy'],'DND drains')
    assert state()['popups']==0,'DND showed popup'
    ipc('notifications','setDnd','off')
    for i in range(5):send('Overflow '+str(i))
    wait_for(lambda:state()['popups']==3 and len(state()['groups'])==3,'visible popup cap')
    ipc('notifications','dismissAll')
    action=subprocess.Popen(['notify-send','-a','Foamy Test','-u','critical','--wait','--action=default=Open','--action=read=Mark read','Action test','Sample body'],env=env,stdout=subprocess.PIPE,text=True)
    wait_for(lambda:len(state()['groups'])==1 and state()['groups'][0]['actions']==1,'native action labels')
    ipc('foamy.notifications','invokeAction',state()['groups'][0]['key'],'read')
    output=action.communicate(timeout=4)[0].strip();assert output=='read',output
    wait_for(lambda:state()['popups']==0,'native action handles popup')
    assert not state()['lastError'],state()['lastError']
    # Exercise the same image-path and structured action used by Omarchy screenshots.
    image=base/'screenshot.png'
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    image.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',64,40,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x43\x5d\x5b'*64)*40))+chunk(b'IEND',b''))
    marker=base/'image-action.txt'
    action_script=base/'image-action.py'
    action_script.write_text('from pathlib import Path\nimport sys\nPath('+repr(str(marker))+').write_text(sys.argv[1])\n')
    subprocess.run(['omarchy','notification','send','--app-name','Foamy Test','-u','critical','--image',str(image),'Screenshot saved to clipboard and file','Click this test image','--exec',sys.executable,str(action_script),str(image)],env=env,check=True,timeout=3)
    wait_for(lambda:state()['popups']==1 and not state()['busy'],'screenshot notification delivery')
    ipc('notifications','invokeLast')
    wait_for(lambda:marker.exists() and state()['popups']==0 and not state()['busy'],'screenshot default action')
    assert marker.read_text()==str(image),'screenshot action changed the image path'
    if CENTER:
        wait_for(lambda:not any('Screenshot saved to clipboard and file' in item.read_text() for item in (home/'.local/state/omarchy/notifications/history').glob('*.json')),'handled screenshot stays out of history')
    cfg=json.loads((config/'shell.json').read_text())
    settings=cfg['plugins'][0]
    settings.update({'suppressFullscreen':True,'fullscreenCritical':False,'criticalTimeoutSec':2,'normalTimeoutSec':1})
    ctx=json.loads(context_path.read_text());ctx['clients'][0].update({'fullscreen':2,'workspace':{'id':1}})
    context_path.write_text(json.dumps(ctx));(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['suppressFullscreen'] and state()['fullscreenMonitors']==['TEST-1'],'fullscreen state loads')
    send('Fullscreen suppressed')
    wait_for(lambda:not state()['busy'] and state()['pending']==0 and not state()['contextBusy'],'fullscreen archive drains')
    assert state()['popups']==0,'fullscreen suppression leaked a popup'
    settings['fullscreenCritical']=True;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['fullscreenCritical'],'critical exception loads')
    send('Critical allowed')
    wait_for(lambda:state()['popups']==1,'critical fullscreen exception')
    wait_for(lambda:state()['popups']==0,'configured critical expiry')
    settings['suppressFullscreen']=False;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not state()['settings']['suppressFullscreen'],'fullscreen suppression disabled')
    send('Normal expiry',('-u','normal'))
    wait_for(lambda:state()['popups']==1,'normal popup visible')
    def countdown():return json.loads(ipc('countdown-test','state'))
    wait_for(lambda:0<countdown()['progress']<1,'indicator receives shared clock ticks')
    before=countdown();time.sleep(.2);after=countdown()
    assert after['updates']-before['updates']>=5,'countdown still advances in coarse steps'
    assert after['remaining']<before['remaining'],'countdown stopped between frames'
    for mode in ['hover','busy']:
        ipc('countdown-test','pause',mode)
        before=countdown()['remaining'];time.sleep(.3)
        assert countdown()['remaining']==before,'indicator moved while '+mode+' paused expiry'
        assert state()['popups']==1,'paused popup expired'
    ipc('countdown-test','pause','resume')
    wait_for(lambda:state()['popups']==0,'configured normal expiry')
    assert countdown()['progress']==0,'indicator retained expired state'
    settings['showTimeoutIndicator']=False;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not countdown()['enabled'],'indicator setting updates live')
    send('Hidden indicator still expires',('-u','normal'))
    wait_for(lambda:state()['popups']==1,'disabled indicator popup visible')
    assert countdown()['progress']==0
    wait_for(lambda:state()['popups']==0,'disabled indicator retains normal expiry')
    settings.update({'groupDuplicates':False,'criticalTimeoutSec':0});(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not state()['settings']['groupDuplicates'],'disable grouping')
    send('Merge these');send('Merge these')
    wait_for(lambda:json.loads(ipc('countdown-test','stackState'))['count']==2,'separate cards displayed')
    settings['groupDuplicates']=True;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:json.loads(ipc('countdown-test','stackState'))['count']==1,'merged stack removes obsolete delegate')
    ipc('notifications','invokeLast');wait_for(lambda:state()['popups']==0,'merged stack cleanup')
    # Real mouse hover and D-Bus expiry: upper cards disappear without moving the third.
    settings['normalTimeoutSec']=2;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['normalTimeoutSec']==2,'stack timeout config')
    ipc('countdown-test','leave')
    send('Hold this third card',('-u','normal'))
    send('Second card expires',('-u','normal'))
    send('First card expires',('-u','normal'))
    def stack_state():return json.loads(ipc('countdown-test','stackState'))
    wait_for(lambda:stack_state()['count']==3 and stack_state()['settled'],'stack entrance settles for three cards')
    ipc('countdown-test','hoverLast')
    wait_for(lambda:stack_state()['held'] and stack_state()['hovered'],'mouse hover holds stack')
    held_y=stack_state()['y'];assert held_y>0
    ipc('countdown-test','capture','stack-before')
    wait_for(lambda:state()['popups']==1 and stack_state()['count']==1,'other cards still expire')
    after=stack_state()
    assert after['y']==held_y and after['hovered'] and after['sameCard'],'hovered card moved or was recreated'
    ipc('countdown-test','capture','stack-held');time.sleep(.1)
    ipc('countdown-test','leave')
    wait_for(lambda:not stack_state()['held'] and stack_state()['y']==0,'leaving stack closes gaps')
    ipc('countdown-test','capture','stack-closed')
    wait_for(lambda:state()['popups']==0,'held card resumes its timeout')
    settings['normalTimeoutSec']=1;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['normalTimeoutSec']==1,'restore timeout')
    # Invalid edits must leave the last valid configuration running.
    settings['normalTimeoutSec']=-1;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:bool(state()['configError']),'invalid configuration reported')
    assert state()['settings']['normalTimeoutSec']==1

    for name in ['stack-before','stack-held','stack-closed']:
        assert (app/(name+'.png')).exists(),'missing stack render: '+name
    print('PASS: stable third-card hover through real expiry and gap closure; timeout indicator progression, hover/busy pause, persistence and disabled expiry; center replacements, single removal, shared focus/retry, idle event-driven refresh; actual D-Bus delivery, duplicates, click handling, dismissal history, DND, overflow, native action callback, screenshot image delivery and structured action, configurable fullscreen/critical exceptions, expiry, invalid settings')
finally:
    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3);log.close()
    print('Runtime log:',base/'runtime.log')
