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
import hostenv

from gi.repository import Gio, GLib

# Optional real center checkout enables the cross-plugin archive/badge regression.
CENTER=os.environ.get('FOAMY_CENTER_SOURCE','')

SOURCE=Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    result=subprocess.run(['dbus-run-session','--','python3',__file__,'--inside'],timeout=90)
    raise SystemExit(result.returncode)
base=Path(tempfile.mkdtemp(prefix='foamy-notifications-runtime-'))
app=base/'app';app.mkdir()
(app/'Commons').symlink_to(hostenv.root()/'shell/Commons',target_is_directory=True)
(app/'Ui').symlink_to(hostenv.root()/'shell/Ui',target_is_directory=True)
# Offscreen Qt has no layer-shell backend. Keep the real service and D-Bus server,
# with presentation tested independently by render.py and the installed desktop.
shutil.copytree(SOURCE,app/'plugin',ignore=shutil.ignore_patterns('__pycache__'))
service=(SOURCE/'Service.qml').read_text()
service=service[:service.index('  Variants {')]+'}\n'
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
    center_service.write_text(center_service.read_text().replace('    function seed(count: int): string {', '    function focus(key: string): string { root.focusNotification(root.entries.find(function(row) { return row.key === key })); return "queued" }\n    function liveActions(): string { return JSON.stringify(root.liveActions) }\n    function press(key: string, index: int): string { root.pressAction(root.entries.find(function(row) { return row.key === key }), index); return "queued" }\n    function seed(count: int): string {'))
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
  function rightClickFirst(): void { input.mouseClick(popupColumn.itemAtIndex(0),100,50,Qt.RightButton) }
  function deleteFirst(): void { notifications.startKeyboard(""); notifications.keyboardPressed(Qt.Key_Delete, "") }
  function enterFirst(): void { notifications.startKeyboard(""); notifications.keyboardPressed(Qt.Key_Return, "") }
  function closeFirst(): void { input.mouseClick(input.findChild(popupColumn.itemAtIndex(0),"dismissButton")) }
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
env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),XDG_STATE_HOME=str(home/'.local/state'),XDG_RUNTIME_DIR=str(runtime),QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion')
# Host QML only: omarchy-* are stubs, omarchy-shell reaches this test shell, no host script runs.
env.update(hostenv.make(base, app))
env['PATH']=str(bin_dir)+':'+env['PATH']
for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
log=(base/'runtime.log').open('w')
process=subprocess.Popen(['quickshell','-p',str(app),'--no-color'],env=env,stdout=log,stderr=log,start_new_session=True)
def ipc(target,*args):
    result=subprocess.run(['qs','ipc','-n','-p',str(app),'call',target,*args],env=env,capture_output=True,text=True,timeout=3)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip()
def state():return json.loads(ipc('foamy.notifications','state'))
def wait_for(predicate,message,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            if predicate():return
        except (ValueError,RuntimeError,subprocess.TimeoutExpired):pass
        time.sleep(.1)
    print('Failure state:',state(),flush=True)
    raise AssertionError(message+'\n'+(base/'runtime.log').read_text())
def send(summary,extra=(),body='Sample body'):
    sent=subprocess.run(['notify-send','-p','-a','Foamy Test','-u','critical',*extra,summary,body],env=env,capture_output=True,text=True,timeout=3)
    # A notify-send that cannot run (e.g. a foreign libnotify) must not read as a plugin timeout.
    assert sent.returncode==0 and sent.stdout.strip(),'notify-send failed: '+sent.stderr
    return sent.stdout.strip()
def omarchy_send(app_name, summary, body, image, argv):
    """What `omarchy notification send -u critical --image … --exec …` sends, over the test's
    private bus, without running a host script (the same hints: image-path, omarchy-exec-argv)."""
    quote = lambda text: "'" + str(text).replace('\\', '\\\\').replace("'", "\\'") + "'"
    hints = "{'urgency': <byte 2>, 'image-path': <" + quote(image) + ">, 'omarchy-exec-argv': <" + quote(json.dumps(argv)) + ">}"
    subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications',
        '--method','org.freedesktop.Notifications.Notify',app_name,'0','',summary,body,'[]',hints,'0'],env=env,check=True,timeout=3,stdout=subprocess.DEVNULL)

def omarchy_calls():
    log=base/'omarchy-calls.log'
    return [line[line.rfind('/')+1:] for line in log.read_text().splitlines()] if log.exists() else []

def dispatches():
    return dispatch_path.read_text().splitlines() if dispatch_path.exists() else []
try:
    wait_for(lambda:ipc('notifications','ping')=='ok','service startup')
    # A dismiss shortcut hides one visible card, not one member of a duplicate group.
    send('Keep older card')
    for _ in range(3): send('Dismiss grouped card')
    wait_for(lambda:state()['popups']==4 and len(state()['groups'])==2 and state()['groups'][0]['count']==3 and not state()['busy'],'grouped dismissal fixture')
    ipc('notifications','dismissOne')
    wait_for(lambda:state()['popups']==1 and len(state()['groups'])==1 and not state()['busy'],'whole newest group dismissed')
    history=home/'.local/state/foamy-notifications/history'
    assert len(list(history.glob('*.json')))==3,'dismissOne did not preserve all duplicate history'
    ipc('notifications','dismissOne')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'older card remains independently dismissible')
    assert ipc('notifications','dismissOne')=='none','empty dismissal should be harmless'
    ipc('notifications','clear')
    wait_for(lambda:not state()['busy'] and state()['queuedJobs']==0,'clear dismissal fixture')
    # Placeholders have no persisted key: every close path must remain local.
    cfg=json.loads((config/'shell.json').read_text())
    cfg['plugins'][0].update(closeButtonRemovesHistory=True,lowTimeoutSec=1)
    (config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['closeButtonRemovesHistory'] and state()['settings']['lowTimeoutSec']==1,'placeholder close settings')
    before=len(dispatches())
    for method in ['closeFirst','deleteFirst','enterFirst','dismissOne','dismissAll','expire']:
        ipc('notifications','showHistory')
        wait_for(lambda:state()['popups']==1 and len(state()['groups'])==1 and json.loads(ipc('countdown-test','stackState'))['settled'],'empty-history placeholder')
        if method in ['closeFirst','deleteFirst','enterFirst']: ipc('countdown-test',method)
        elif method!='expire': ipc('notifications',method)
        wait_for(lambda:state()['popups']==0 and not state()['busy'],'placeholder '+method)
        assert state()['lastError']=='',state()['lastError']
        assert not list(history.glob('*.json')),'placeholder entered history'
    assert len(dispatches())==before,'placeholder tried to focus an app'
    cfg['plugins'][0].update(closeButtonRemovesHistory=False,lowTimeoutSec=5)
    (config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not state()['settings']['closeButtonRemovesHistory'],'restore close settings')
    print('PASS: grouped dismissOne preserves history and unrelated cards; empty-history X, Delete, Enter, dismissOne, dismissAll and expiry never persist or focus')

    if CENTER:
        wait_for(lambda:state()['centerEnabled'],'center config loaded')
        ipc('foamy.notification-center','close')
    cfg=json.loads((config/'shell.json').read_text())
    assert state()['settings']['dndSilenceCritical'] is False
    ipc('notifications','setDnd','on')
    for app_name in ['omarchy-action','notify-send']:
        send('DND policy default '+app_name,('-a',app_name))
        wait_for(lambda:state()['popups']==1 and not state()['busy'],'default trusted critical bypass')
        ipc('notifications','dismissAll')
        wait_for(lambda:state()['popups']==0 and not state()['busy'],'default bypass cleanup')
    cfg['plugins'][0]['dndSilenceCritical']=True
    (config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['dndSilenceCritical'],'critical DND option reloads')
    history=home/'.local/state/foamy-notifications/history'
    for app_name in ['omarchy-action','notify-send','Foamy Test']:
        title='DND policy silenced '+app_name
        send(title,('-a',app_name))
        wait_for(lambda:any(json.loads(p.read_text()).get('summary')==title for p in history.glob('*.json')) and not state()['busy'],'critical alert reaches history')
        assert state()['popups']==0,'critical alert escaped DND'
    if CENTER:
        # Native app actions must still work directly from silenced history.
        action=subprocess.Popen(['notify-send','-a','notify-send','-u','critical','--wait','--action=default=Open','DND policy native action','Sample body'],env=env,stdout=subprocess.PIPE,text=True)
        wait_for(lambda:any(json.loads(p.read_text()).get('summary')=='DND policy native action' for p in history.glob('*.json')) and state()['retainedActions']==1,'silenced critical callback retained')
        key=next(p.stem for p in history.glob('*.json') if json.loads(p.read_text()).get('summary')=='DND policy native action')
        ipc('foamy.notification-center.test','focus',key)
        assert action.communicate(timeout=4)[0].strip()=='default'
        wait_for(lambda:state()['retainedActions']==0 and not state()['busy'],'critical history action handled')
    send('DND policy transient',('-a','omarchy-action','-h','boolean:transient:true'))
    time.sleep(.15)
    assert not any(json.loads(p.read_text()).get('summary')=='DND policy transient' for p in history.glob('*.json'))
    send('DND policy normal feedback',('-a','omarchy-action','-u','normal'))
    wait_for(lambda:state()['popups']==1,'normal action feedback still bypasses DND')
    ipc('notifications','dismissAll')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'normal feedback cleanup')
    ipc('notifications','setDnd','off')
    send('DND policy unmuted',('-a','omarchy-action'))
    wait_for(lambda:state()['popups']==1,'critical alert appears outside DND')
    ipc('notifications','dismissAll')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'unmuted alert cleanup')
    cfg['plugins'][0]['dndSilenceCritical']=False
    (config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not state()['settings']['dndSilenceCritical'],'default policy restored')
    if CENTER:
        ipc('foamy.notification-center.test','clear')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0 and not state()['busy'],'policy fixture cleanup')
    else:
        for entry in history.glob('*.json'):
            if json.loads(entry.read_text()).get('summary','').startswith('DND policy '):entry.unlink()
    # A sender without a window (omasnap's "copied" toast) has nothing to focus: the click closes the card quietly.
    before=len(dispatches())
    send('Windowless sender',('-a','omasnap'))
    wait_for(lambda:state()['popups']==1 and not state()['busy'],'windowless popup ready')
    ipc('notifications','invokeLast')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'windowless click closes the card')
    assert state()['lastError']=='',state()['lastError']
    assert len(dispatches())==before,'windowless click focused an app'
    if CENTER:
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'windowless click keeps center history')
        ipc('foamy.notification-center.test','clear')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0 and not state()['busy'],'windowless fixture cleanup')
    else:
        for entry in history.glob('*.json'):
            if json.loads(entry.read_text()).get('summary')=='Windowless sender':entry.unlink()
    prior_tombstones=set((home/'.local/state/omarchy-notification-center/dismissed').read_text().splitlines()) if CENTER else set()
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
        assert len(tombstones)==len(prior_tombstones)+2 and len(set(tombstones))==len(tombstones),'handled keys were removed more than once'
        # Exercise the actual card inputs and shared removal queue against real history.
        for enabled, click in [(False,'closeFirst'),(True,'rightClickFirst'),(True,'closeFirst')]:
            cfg=json.loads((config/'shell.json').read_text())
            cfg['plugins'][0]['closeButtonRemovesHistory']=enabled
            (config/'shell.json').write_text(json.dumps(cfg))
            wait_for(lambda:state()['settings']['closeButtonRemovesHistory']==enabled,'X preference reload')
            before=len(dispatches())
            actions=[subprocess.Popen(['notify-send','-a','Foamy Test','-u','critical','--wait','--action=default=Open','Close behavior '+click,'Sample body'],env=env,stdout=subprocess.PIPE,text=True) for _ in range(2)]
            wait_for(lambda:state()['popups']==2 and len(state()['groups'])==1 and state()['groups'][0]['count']==2 and not state()['busy'] and json.loads(ipc('countdown-test','stackState'))['settled'],'duplicate cards ready')
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==2,'close test history ingested')
            ipc('countdown-test',click)
            wait_for(lambda:state()['popups']==0 and not state()['busy'],'close test popup removed')
            if enabled and click=='closeFirst':
                wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0 and json.loads(ipc('foamy.notification-center.test','state'))['unread']==0,'X clears history and badge')
                assert not list((home/'.local/state/foamy-notifications/history').glob('*.json')),'X entered history'
                assert not list((home/'.local/state/foamy-notifications/popups').glob('*-*.json')),'X popup persisted'
                ipc('foamy.notification-center.test','reload')
                time.sleep(.3)
                assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'X entry reappeared after refresh'
            else:
                wait_for(lambda:state()['retainedActions']==2,'hidden callbacks retained')
                assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==2,'hide removed history'
                assert all(action.poll() is None for action in actions),'hide closed sender callbacks'
                ipc('foamy.notification-center.test','clear')
                wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'close test cleanup')
            for action in actions: assert action.communicate(timeout=4)[0].strip()=='','close invoked default action'
            assert len(dispatches())==before,'close focused an app'
            ipc('countdown-test','leave')
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
        assert len(list((home/'.local/state/foamy-notifications/popups').glob('*.json')))==1,'failed popup lost persistence'
        ctx['clients']=ctx['clients'][:1];context_path.write_text(json.dumps(ctx))
        ipc('notifications','invokeLast')
        wait_for(lambda:state()['popups']==0 and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'popup retry succeeds')
        saved_clients=ctx['clients']
        # Missing-window focus is intentionally silent in the current center; query failures are retryable errors.
        send('Unavailable center focus')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'ambiguous entry ingested')
        key=state()['groups'][0]['key']
        context_path.write_text('{')
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

    assert not list((home/'.local/state/foamy-notifications/history').glob('*.json')),'handled click entered history'
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
            calls=omarchy_calls()
            click_teams()
            wait_for(lambda:len(dispatches())==before+1,'Teams focus dispatched once')
            expected='0x101' if target=='popup' else '0x100'
            assert json.loads(dispatches()[-1])==['hl.dsp.focus({ window = "address:'+expected+'" })'],'incorrect focus fallback'
            # Omarchy's focus-app picks the site's app window through its literal class
            # pattern (the newest of one per browser profile), never a browser tab.
            asked=[l for l in omarchy_calls()[len(calls):] if l.startswith('omarchy-hyprland-focus-app ^')]
            assert asked==(['omarchy-hyprland-focus-app ^(chrome|vivaldi|msedge|brave)-teams\\.microsoft\\.com__'] if target=='popup' else []),asked
            if target=='popup':wait_for(lambda:state()['popups']==0 and not state()['busy'] and not state()['lastError'],'Teams popup handled')
            if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'Teams history handled')
            ipc('countdown-test','leave')
            if ambiguous and target=='popup':
                # A browserMappings entry names the exact window, asked for before the site pattern.
                cfg['plugins'][0]['browserMappings']=[{'origin':'teams.microsoft.com','windowClass':other_teams['class']}]
                (config/'shell.json').write_text(json.dumps(cfg))
                wait_for(lambda:bool(state()['settings']['browserMappings']),'Teams mapping loaded')
                send('Teams mapped',('-a','Vivaldi','-h','string:desktop-entry:vivaldi-stable'),body='teams.microsoft.com\nAlex: Hi')
                wait_for(lambda:state()['popups']==1 and not state()['busy'],'mapped Teams popup ready')
                wait_for(lambda:json.loads(ipc('countdown-test','stackState'))['settled'],'mapped Teams card ready for click')
                before=len(dispatches());calls=omarchy_calls()
                ipc('countdown-test','clickFirst')
                wait_for(lambda:len(dispatches())==before+1,'mapped Teams focus dispatched once')
                assert json.loads(dispatches()[-1])==['hl.dsp.focus({ window = "address:0x102" })'],'mapping ignored'
                asked=[l for l in omarchy_calls()[len(calls):] if l.startswith('omarchy-hyprland-focus-app ^')]
                assert asked==['omarchy-hyprland-focus-app ^vivaldi-teams\\.microsoft\\.com__-Profile_2$'],asked
                wait_for(lambda:state()['popups']==0 and not state()['busy'],'mapped Teams popup handled')
                if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'mapped Teams history handled')
                ipc('countdown-test','leave')
    # A browser notification whose site has an app window: Foamy focuses that window
    # and never hands the click to the browser, which would open the site in a new
    # tab (no activation token reaches it). The sender only sees its notification close.
    ctx['clients']=[browser,teams];context_path.write_text(json.dumps(ctx))
    cfg['plugins'][0]['browserMappings']=[];(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:not state()['settings']['browserMappings'],'clear browser mapping for live clicks')
    for target in (['popup','center'] if CENTER else ['popup']):
        before=len(dispatches())
        action=subprocess.Popen(['notify-send','-a','Vivaldi','-u','critical','--wait','--action=default=Open','Teams app window','teams.microsoft.com\nAlex: Hi'],env=env,stdout=subprocess.PIPE,text=True)
        wait_for(lambda:state()['popups']==1 and json.loads(ipc('countdown-test','stackState'))['settled'],'live Teams popup ready')
        if target=='center':
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'live Teams archived')
            ipc('foamy.notification-center.test','focus',state()['groups'][0]['key'])
        else:ipc('countdown-test','clickFirst')
        output=action.communicate(timeout=4)[0].strip();assert output=='',output
        assert [json.loads(d) for d in dispatches()[before:]]==[['hl.dsp.focus({ window = "address:0x101" })']],dispatches()[before:]
        wait_for(lambda:state()['popups']==0 and not state()['busy'],'live Teams click handled')
        if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'live Teams click clears center')
    # Without an app window for the site, both entry points deliver the native
    # callback without local window focus.
    ctx['clients']=[browser];context_path.write_text(json.dumps(ctx))
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
    # A focus-app that hangs (killed by its 5 s deadline) or is missing (an older
    # Omarchy) still hands the click to the sender, although the site has an app
    # window: the card is not left busy and no focus Process outlives the click.
    ctx['clients']=[browser,teams];context_path.write_text(json.dumps(ctx))
    focus_app=Path(env['OMARCHY_PATH'])/'bin/omarchy-hyprland-focus-app'
    focus_app_text=focus_app.read_text()
    hang_pid=base/'focus-app-hang.pid'
    for broken in ['hang','missing']:
        if broken=='hang':
            focus_app.write_text('#!/bin/sh\necho $$ > '+str(hang_pid)+'\nexec sleep 60\n');focus_app.chmod(0o755)
        else:focus_app.unlink()
        before=len(dispatches())
        action=subprocess.Popen(['notify-send','-a','Vivaldi','-u','critical','--wait','--action=default=Open','Teams focus '+broken,'teams.microsoft.com\nAlex: Hi'],env=env,stdout=subprocess.PIPE,text=True)
        wait_for(lambda:state()['popups']==1 and json.loads(ipc('countdown-test','stackState'))['settled'],'broken focus popup ready '+broken)
        clicked=time.monotonic()
        ipc('countdown-test','clickFirst')
        if broken=='hang':
            time.sleep(1);assert state()['cardsBusy']==1 and state()['siteFocusProcesses']==1,'focus-app still running'
        output=action.communicate(timeout=12)[0].strip();assert output=='default',(broken,output)
        assert broken=='missing' or time.monotonic()-clicked>=4.5,'the hang ended before its deadline'
        wait_for(lambda:state()['popups']==0 and state()['cardsBusy']==0 and state()['siteFocusProcesses']==0 and not state()['busy'],'broken focus handled '+broken)
        assert len(dispatches())==before,'broken focus-app focused a window'
        if broken=='hang':
            pid=int(hang_pid.read_text())
            try:os.kill(pid,0);alive=True
            except ProcessLookupError:alive=False
            assert not alive,'the hung focus-app outlived its deadline'
        if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'broken focus clears center '+broken)
        ipc('countdown-test','leave')
    focus_app.write_text(focus_app_text);focus_app.chmod(0o755)
    print('PASS: a hung or missing focus-app falls back to the sender callback; no busy card, no leaked Process')
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
                files=list((home/'.local/state/foamy-notifications/history').glob('*-*.json'))
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
    # Raw pixels close the native notification, so both click paths use app focus.
    ctx['clients']=original_clients;context_path.write_text(json.dumps(ctx))
    raw_bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    raw_closed=[]; raw_actions=[]
    def raw_signal(connection,sender,path,interface,name,arguments):
        (raw_closed if name=='NotificationClosed' else raw_actions).append(arguments.unpack()[0])
    raw_bus.signal_subscribe(None,'org.freedesktop.Notifications',None,'/org/freedesktop/Notifications',None,Gio.DBusSignalFlags.NONE,raw_signal)
    def pump_signals():
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
    for target in (['popup','center-visible','center-history','center-silenced'] if CENTER else ['popup']):
        before=len(dispatches())
        ipc('notifications','setDnd','on' if target=='center-silenced' else 'off')
        args=GLib.Variant('(susssasa{sv}i)',('Foamy Test',0,'foot','Raw focus '+target,'Text survives native closure',
            ['default','Open','reply','Reply'],{'urgency':GLib.Variant('y',2),'image-data':GLib.Variant('(iiibiiay)',(1,1,4,True,8,4,bytes([40,80,120,255])))},0))
        identifier=raw_bus.call_sync('org.freedesktop.Notifications','/org/freedesktop/Notifications','org.freedesktop.Notifications','Notify',args,GLib.VariantType.new('(u)'),Gio.DBusCallFlags.NONE,3000,None).unpack()[0]
        wait_for(lambda:state()['tracked']==state()['retainedActions']==0 and not state()['busy'],'raw native source released')
        pump_signals();assert identifier in raw_closed,'raw sender was not told of closure'
        if target=='popup':
            wait_for(lambda:state()['popups']==1 and json.loads(ipc('countdown-test','stackState'))['settled'],'raw popup ready')
            assert state()['groups'][0]['actions']==0
            ipc('countdown-test','clickFirst')
            wait_for(lambda:state()['popups']==0 and not state()['busy'],'raw popup app-focus fallback')
        else:
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'raw text reaches center')
            if target=='center-history':ipc('notifications','dismissAll')
            center_rows=list((home/'.local/state/foamy-notifications/history' if target=='center-silenced' else home/'.local/state/foamy-notifications/popups').glob('*.json'))
            if target=='center-history':
                wait_for(lambda:state()['popups']==0 and not state()['busy'],'raw popup archived')
                center_rows=list((home/'.local/state/foamy-notifications/history').glob('*.json'))
            key=next(p.stem for p in center_rows if json.loads(p.read_text()).get('summary')=='Raw focus '+target)
            assert ipc('foamy.notifications','invokeDefault',key)=='unavailable'
            ipc('foamy.notification-center.test','focus',key)
        if CENTER:wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'raw fallback clears center only after focus')
        wait_for(lambda:len(dispatches())==before+1,'raw click focuses sending app once')
        pump_signals();assert identifier not in raw_actions,'raw callback survived native closure'
        ipc('notifications','dismissAll');ipc('notifications','setDnd','off')
        wait_for(lambda:not state()['busy'],'raw focus cleanup')
    ctx['clients']=original_clients;context_path.write_text(json.dumps(ctx))
    cfg['plugins'][0]['browserMappings']=[];(config/'shell.json').write_text(json.dumps(cfg))
    send('Dismiss me')
    wait_for(lambda:state()['popups']==1 and not state()['busy'],'new popup')
    ipc('notifications','dismissOne')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'dismiss drains')
    assert list((home/'.local/state/foamy-notifications/history').glob('*.json')),'dismiss did not retain history'
    ipc('notifications','setDnd','on');send('Silenced')
    wait_for(lambda:not state()['busy'],'DND drains')
    assert state()['popups']==0,'DND showed popup'
    ipc('notifications','setDnd','off')
    # Overflow waits in the queue rather than going to history.
    for i in range(5):send('Overflow '+str(i))
    wait_for(lambda:state()['popups']==5 and len(state()['groups'])==3 and len(state()['queue'])==2,'visible popup cap queues the rest')
    ipc('notifications','dismissAll')
    wait_for(lambda:state()['popups']==0 and not state()['queue'] and not state()['busy'],'dismissAll also clears the queue')
    # A normal notification followed by three criticals (a reported "it vanished").
    def in_history(summary):
        return any(json.loads(path.read_text()).get('summary')==summary for path in (home/'.local/state/foamy-notifications/history').glob('*.json'))
    def sequence():
        normal=send('Non-critical notification',('-u','normal'))
        return normal,[send('Critical notification '+str(i)) for i in (1,2,3)]
    # With the default limit (5) all four show.
    cfg=json.loads((config/'shell.json').read_text());cfg['plugins'][0].pop('maxVisible',None)
    (config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['maxVisible']==5,'default visible limit is 5')
    sequence()
    wait_for(lambda:len(state()['groups'])==4 and not state()['queue'] and not state()['busy'],'the default limit shows all four')
    assert not in_history('Non-critical notification'),'the normal notification went to history'
    ipc('notifications','dismissAll')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'default sequence dismissed')
    # With a limit of 3 one slot stays for the normal one: two criticals show with
    # it, and the oldest critical waits and shows when one is dismissed.
    cfg['plugins'][0]['maxVisible']=3;(config/'shell.json').write_text(json.dumps(cfg))
    wait_for(lambda:state()['settings']['maxVisible']==3,'visible limit 3')
    normal,criticals=sequence()
    def ids(keys):return [key.split('-')[1] for key in keys]
    wait_for(lambda:ids(g['key'] for g in state()['groups'])==[criticals[2],criticals[1],normal] and ids(state()['queue'])==[criticals[0]] and not state()['busy'],'the normal one and two criticals show, the oldest critical waits')
    archived=lambda:ids(path.stem for path in (home/'.local/state/foamy-notifications/history').glob('*.json'))
    assert normal not in archived() and criticals[0] not in archived(),'a shown or waiting notification went to history'
    assert not set(criticals)&set(archived()),'a critical went to history'
    ipc('notifications','dismissOne')
    # The newest critical leaves; the waiting card joins the bottom of the stack.
    wait_for(lambda:ids(g['key'] for g in state()['groups'])==[criticals[1],normal,criticals[0]] and not state()['queue'],'the waiting card shows when a critical is dismissed')
    wait_for(lambda:criticals[2] in archived(),'the dismissed critical is in history')
    ipc('notifications','dismissAll')
    wait_for(lambda:state()['popups']==0 and not state()['busy'],'overflow sequence dismissed')
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
    omarchy_send('Foamy Test','Screenshot saved to clipboard and file','Click this test image',image,[sys.executable,str(action_script),str(image)])
    wait_for(lambda:state()['popups']==1 and not state()['busy'],'screenshot notification delivery')
    ipc('notifications','invokeLast')
    wait_for(lambda:marker.exists() and state()['popups']==0 and not state()['busy'],'screenshot default action')
    assert marker.read_text()==str(image),'screenshot action changed the image path'
    if CENTER:
        wait_for(lambda:not any('Screenshot saved to clipboard and file' in item.read_text() for item in (home/'.local/state/foamy-notifications/history').glob('*.json')),'handled screenshot stays out of history')
    if CENTER:
        ipc('foamy.notification-center.test','clear')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'clear unrelated history before editor scenarios')
        # Use a long-lived fake editor so the center must release its launch process.
        # It is omasnap, the editor 4.5 names and the center's opener prefers, so the
        # stub (first on PATH) shadows an installed one on every launch path.
        editor_marker=base/'screenshot-editor.txt'
        editor=bin_dir/'omasnap'
        editor.write_text('#!/bin/sh\nprintf "%s" "$1" > '+str(editor_marker)+'\nsleep 1\n')
        editor.chmod(0o755)
        spaced_image=base/'screenshot with spaces.png';shutil.copy(image,spaced_image)
        for mode in ['visible','history','reload','missing','launch-failure']:
            editor_marker.unlink(missing_ok=True)
            omarchy_send('omarchy-action','Screenshot editor '+mode,'Click to edit',spaced_image,['omasnap',str(spaced_image)])
            wait_for(lambda:state()['popups']==1 and not state()['busy'],'screenshot ready '+mode)
            key=state()['groups'][0]['key']
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'screenshot archived '+mode)
            if mode!='visible':
                ipc('notifications','dismissAll')
                wait_for(lambda:state()['popups']==0 and not state()['busy'],'screenshot outlives popup '+mode)
            # Within 24 h the live command still runs from the center, like the
            # popup ('history'); a reload releases it, and the center's fixed
            # opener (with its failure feedback) takes over.
            if mode in ['reload','missing','launch-failure']:
                os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3)
                process=subprocess.Popen(['quickshell','-p',str(app),'--no-color'],env=env,stdout=log,stderr=log,start_new_session=True)
                wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'screenshot survives shell reload')
                assert ipc('foamy.notifications','invokeDefault',key)=='unavailable'
                assert ipc('foamy.notifications','centerActions')=='{}','a reload must release live commands'
            if mode=='missing':spaced_image.unlink()
            if mode=='launch-failure':editor.write_text('#!/bin/sh\nexit 3\n')
            ipc('foamy.notification-center.test','focus',key)
            if mode in ['missing','launch-failure']:
                wait_for(lambda:bool(json.loads(ipc('foamy.notification-center.test','state'))['focusError']),'screenshot launch failure reports feedback')
                assert json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'failed screenshot launch lost history'
                shutil.copy(image,spaced_image)
                editor.write_text('#!/bin/sh\nprintf "%s" "$1" > '+str(editor_marker)+'\nsleep 1\n')
                ipc('foamy.notification-center.test','focus',key)
            wait_for(lambda:editor_marker.exists() and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'center launches screenshot editor '+mode)
            assert editor_marker.read_text()==str(spaced_image),'center changed screenshot filename'
            wait_for(lambda:state()['popups']==0 and not state()['busy'],'screenshot cleanup '+mode)
    if CENTER:
        # Omarchy click commands and --action buttons stay pressable from the
        # center after the popup (in Foamy's memory only); a press runs once.
        button_marker=base/'button.txt'
        button=bin_dir/'button-stub';button.write_text('#!/bin/sh\nprintf "%s" "$1" > '+str(button_marker)+'\n');button.chmod(0o755)
        def send_buttons(summary):
            hints="{'urgency': <byte 1>, 'omarchy-exec-argv': <'"+json.dumps(['button-stub','click'])+"'>, 'omarchy-actions': <'"+json.dumps([{'label':'View in Zendesk','argv':['button-stub','view']},{'label':'Mark as read','argv':['button-stub','read']}])+"'>}"
            subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications',
                '--method','org.freedesktop.Notifications.Notify','Zendesk','0','',summary,'Anna: a reply','[]',hints,'0'],env=env,check=True,timeout=3,stdout=subprocess.DEVNULL)
            wait_for(lambda:state()['popups']==1 and not state()['busy'],'button notification '+summary)
            key=state()['groups'][0]['key']
            wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'button notification archived '+summary)
            ipc('notifications','dismissAll')
            wait_for(lambda:state()['popups']==0 and not state()['busy'],'button notification leaves the screen '+summary)
            return key
        ipc('foamy.notification-center.test','clear')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'clear before button scenarios')
        key=send_buttons('Buttons')
        assert json.loads(ipc('foamy.notifications','centerActions'))=={key:['View in Zendesk','Mark as read']}
        ipc('foamy.notification-center.store','peerChanged','actions')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','liveActions'))=={key:['View in Zendesk','Mark as read']},'center reads the live buttons')
        ipc('foamy.notification-center.test','press',key,'1')
        wait_for(lambda:button_marker.exists() and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'center button runs once and clears the entry')
        assert button_marker.read_text()=='read'
        wait_for(lambda:ipc('foamy.notifications','centerActions')=='{}' and state()['retainedActions']==0,'pressed buttons are released')
        button_marker.unlink()
        key=send_buttons('Click')
        ipc('foamy.notification-center.test','focus',key)
        wait_for(lambda:button_marker.exists() and json.loads(ipc('foamy.notification-center.test','state'))['entries']==0,'center click runs the live click command')
        assert button_marker.read_text()=='click'
        # A sender that withdraws its notification after the popup left takes its
        # buttons with it: the center is told, without any other change.
        closing=subprocess.Popen(['notify-send','-a','Zendesk','-u','normal','-t','0','--wait','--action=default=Open','--action=read=Mark read','Withdrawn','Ticket update'],env=env,stdout=subprocess.PIPE,text=True)
        wait_for(lambda:state()['popups']==1 and not state()['busy'],'withdrawn notification shown')
        key=state()['groups'][0]['key']
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','state'))['entries']==1,'withdrawn notification archived')
        ipc('notifications','dismissAll')
        wait_for(lambda:state()['popups']==0 and state()['retainedActions']==1,'withdrawn notification retained')
        wait_for(lambda:json.loads(ipc('foamy.notification-center.test','liveActions')).get(key)==['Mark read'],'center shows the retained button')
        subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.CloseNotification',key.split('-')[1]],env=env,check=True,capture_output=True,timeout=3)
        assert closing.communicate(timeout=4)[0].strip()==''
        wait_for(lambda:key not in json.loads(ipc('foamy.notification-center.test','liveActions')),'sender closure refreshes the center buttons')
        ipc('foamy.notification-center.test','clear')
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
    print('PASS: stable third-card hover through real expiry and gap closure; timeout indicator progression, hover/busy pause, persistence and disabled expiry; center replacements, single removal, shared focus/retry, idle event-driven refresh; raw-image native closure and popup/visible/archived/DND center focus fallbacks; actual D-Bus delivery, duplicates, click handling, dismissal history, DND, overflow, native action callback, screenshot image delivery, structured action and center editor parity through expiry/reload with missing-file and launch-failure retries, configurable fullscreen/critical exceptions, expiry, invalid settings')
finally:
    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3);log.close()
    print('Runtime log:',base/'runtime.log')
    hostenv.check(env, (base/'runtime.log').read_text(), (base/'omarchy-calls.log').read_text() if (base/'omarchy-calls.log').exists() else '')
