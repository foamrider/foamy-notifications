"""Flood real private-bus notifications while the actual persistence worker is stalled."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from gi.repository import Gio, GLib

SOURCE = Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    raise SystemExit(subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--inside'], timeout=60).returncode)
base = Path(tempfile.mkdtemp(prefix='foamy-popup-queue-'))
app = base/'app'; app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons', target_is_directory=True)
shutil.copytree(SOURCE, app/'plugin', ignore=shutil.ignore_patterns('.git', '__pycache__'))
service = (SOURCE/'Service.qml').read_text()
# Keep the real service/server/queue; presentation has separate desktop coverage.
(app/'plugin/Service.qml').write_text((service[:service.rfind('  Variants {')] + '}\n').replace('tracked:Object.keys(service.liveRefs).length', 'nativeTracked:server.trackedNotifications.values.length, tracked:Object.keys(service.liveRefs).length'))
(app/'shell.qml').write_text('import Quickshell\nimport "plugin" as Plugin\nShellRoot { Plugin.Service {} }\n')
helper = app/'plugin/bin/notification-helper.py'
helper.rename(helper.with_name('notification-helper-real.py'))
stall = base/'stall'; started = base/'started'
context_stall = base/'context-stall'; context_started = base/'context-started'
# Stall selected writes/context queries, not startup restoration. The timeout also protects failed tests.
helper.write_text('''import os,sys,time
from pathlib import Path
stall=Path(STALL)
if sys.argv[1] == 'context' and Path(CONTEXT_STALL).exists():
    Path(CONTEXT_STARTED).touch()
    deadline=time.monotonic()+15
    while Path(CONTEXT_STALL).exists() and time.monotonic()<deadline: time.sleep(.02)
if sys.argv[1] == 'write' and stall.exists():
    Path(STARTED).touch()
    deadline=time.monotonic()+30
    while stall.exists() and time.monotonic()<deadline: time.sleep(.02)
    if stall.exists(): raise SystemExit('test worker stall timed out')
os.execv(sys.executable,[sys.executable,str(Path(__file__).with_name('notification-helper-real.py')),*sys.argv[1:]])
'''.replace('CONTEXT_STALL', repr(str(context_stall))).replace('CONTEXT_STARTED', repr(str(context_started))).replace('STALL', repr(str(stall))).replace('STARTED', repr(str(started))))
home = base/'home'; home.mkdir()
config = home/'.config/omarchy'; config.mkdir(parents=True)
(config/'shell.json').write_text(json.dumps({'bar':{'layout':{'right':[{'id':'foamy.notification-center'}]}},'plugins':[{'id':'foamy.notifications','maxVisible':3}]}))
runtime = base/'runtime'; runtime.mkdir(mode=0o700)
cli = base/'bin'; cli.mkdir()
(cli/'hyprctl').write_text('#!/bin/sh\nprintf "[]\\n"\n'); (cli/'hyprctl').chmod(0o755)
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home/'.config'), XDG_STATE_HOME=str(base/'state'),
           XDG_RUNTIME_DIR=str(runtime), PATH=str(cli)+':'+os.environ['PATH'], QT_QPA_PLATFORM='offscreen',
           QT_QUICK_BACKEND='software', QT_QPA_PLATFORMTHEME='basic', QT_STYLE_OVERRIDE='Fusion')
for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE']: env.pop(key, None)
log = (base/'runtime.log').open('w')
process = subprocess.Popen(['quickshell','-p',str(app),'--no-color'], env=env, stdout=log, stderr=log, start_new_session=True)

def ipc(target, method, *args):
    return subprocess.check_output(['qs','ipc','-n','-p',str(app),'call',target,method,*map(str,args)], env=env, text=True, stderr=subprocess.DEVNULL, timeout=3).strip()

def state(): return json.loads(ipc('foamy.notifications', 'state'))

def wait(check, label):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            if check(): return
        except (ValueError, subprocess.SubprocessError): pass
        if process.poll() is not None: raise AssertionError('Shell exited: '+label)
        time.sleep(.04)
    raise AssertionError(label+' '+json.dumps(state()))

def send(title, icon='foot', replace=0):
    result=subprocess.check_output(['gdbus','call','--session','--dest','org.freedesktop.Notifications',
        '--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.Notify',
        'Foamy queue test',str(replace),icon,title,'Working text','[]',"{'urgency': <byte 2>}",'0'], env=env, text=True, timeout=3)
    return int(result.split('uint32 ')[1].split(',')[0])

# A persistent sender delivers valid 4 MiB pixel buffers without command-line size limits.
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
raw = GLib.Variant('(iiibiiay)', (1024,1024,4096,True,8,4,bytes([40,80,120,255]) * (1024*1024)))
def send_buffer(title, alias='image-data', replace=0, file_hint=''):
    hints = {'urgency':GLib.Variant('y',2)}
    if alias: hints[alias] = raw
    if file_hint: hints['image-path'] = GLib.Variant('s',file_hint)
    arguments = GLib.Variant('(susssasa{sv}i)', ('Foamy queue test',replace,'foot',title,'Working text',['default','Open','reply','Reply'],hints,0))
    return bus.call_sync('org.freedesktop.Notifications','/org/freedesktop/Notifications','org.freedesktop.Notifications','Notify',arguments,GLib.VariantType.new('(u)'),Gio.DBusCallFlags.NONE,3000,None).unpack()[0]

def rss():
    return int(next(line.split()[1] for line in Path('/proc/'+str(process.pid)+'/status').read_text().splitlines() if line.startswith('VmRSS:')))

try:
    wait(lambda:ipc('notifications','ping') == 'ok' and not state()['busy'], 'startup')
    ipc('notifications','setDnd','on')
    stall.touch(); send('Stalled DND write')
    wait(started.exists, 'worker is stalled')
    for i in range(180): send('DND flood '+str(i), '/tmp/'+'x'*4080)
    first=state(); first_rss=rss()
    for i in range(180,360): send('DND flood '+str(i), '/tmp/'+'x'*4080)
    second=state(); second_rss=rss()
    assert first['queuedJobs'] == second['queuedJobs'] == 64, (first,second)
    assert second['queuedBytes'] <= 786432 and second['tracked'] <= 65, second
    assert 'queue is full' in second['lastError'], second
    # Clearing cancels pending DND callbacks; the one running write precedes durable clearing.
    ipc('notifications','clear')
    wait(lambda:state()['queuedJobs'] == 1 and state()['tracked'] <= 1, 'cancelled DND callbacks release')
    stall.unlink()
    wait(lambda:not state()['busy'] and state()['tracked'] == 0, 'clear drains without catch-up retries')
    assert not list((base/'state/omarchy/notifications/history').glob('*.json'))
    ipc('notifications','setDnd','off')
    send('Oversized icon', 'x'*5000)
    wait(lambda:'sender limits' in state()['lastError'], 'oversized metadata rejection')
    assert state()['popups'] == state()['tracked'] == state()['queuedJobs'] == 0
    ident=send('Before oversized replacement')
    wait(lambda:not state()['busy'] and state()['popups'] == 1, 'valid popup')
    send('Oversized replacement', 'x'*5000, ident)
    wait(lambda:state()['tracked'] == 0, 'oversized replacement releases live source')
    assert state()['queuedJobs'] == 0
    ipc('notifications','dismissAll')
    wait(lambda:not state()['busy'], 'old popup cleanup')
    started.unlink(); stall.touch(); send('Stalled popup write')
    wait(started.exists, 'popup worker is stalled')
    for i in range(160): send('Popup flood '+str(i))
    popup_first=state()
    for i in range(160,320): send('Popup flood '+str(i))
    popup_second=state()
    assert popup_first['queuedJobs'] == popup_second['queuedJobs'] <= 128, (popup_first,popup_second)
    assert popup_second['queuedBytes'] <= 1048576 and popup_second['popups'] <= 3, popup_second
    stall.unlink()
    wait(lambda:not state()['busy'], 'bounded popup writes and archives drain')
    ipc('notifications','dismissAll')
    wait(lambda:not state()['busy'] and state()['tracked'] == 0, 'post-flood cleanup')
    send('Normal delivery after overload')
    wait(lambda:not state()['busy'] and state()['popups'] == 1, 'normal delivery recovers')
    ipc('notifications','dismissAll'); ipc('notifications','clear')
    wait(lambda:not state()['busy'] and state()['nativeTracked']==0, 'native cleanup')
    assert state()['centerEnabled']
    # Small provider URLs must not hide native pixels behind either callback pool.
    for alias in ['image-data','image_data','icon_data']:
        send_buffer('Raw callback omitted '+alias, alias, file_hint='/tmp/missing-fallback.png')
        wait(lambda:not state()['busy'] and state()['nativeTracked']==0, 'raw release '+alias)
        assert state()['tracked']==state()['retainedActions']==0 and state()['groups'][0]['actions']==0, state()
    ipc('notifications','dismissAll'); ipc('notifications','clear')
    wait(lambda:not state()['busy'], 'raw cleanup')
    for mode in ['visible','history','silenced']:
        ipc('notifications','setDnd','on' if mode=='silenced' else 'off')
        identifier=send_buffer('File-only callback '+mode, alias='')
        wait(lambda:state()['tracked']==1 and not state()['busy'], 'file-only callback '+mode)
        if mode=='history':
            ipc('notifications','dismissAll')
            wait(lambda:state()['retainedActions']==1, 'file-only history callback')
        if mode=='silenced': assert state()['retainedActions']==1
        send_buffer('Raw replacement '+mode, replace=identifier)
        wait(lambda:state()['nativeTracked']==state()['tracked']==state()['retainedActions']==0 and not state()['busy'], 'raw replacement releases '+mode)
        files=list((base/'state/omarchy/notifications').glob('*.json')) if mode=='visible' else list((base/'state/omarchy/notifications/history').glob('*.json'))
        assert any(json.loads(p.read_text())['summary']=='Raw replacement '+mode for p in files), files
        if mode=='visible': assert state()['popups']==1 and state()['groups'][0]['actions']==0, state()
        ipc('notifications','dismissAll'); ipc('notifications','clear')
        wait(lambda:not state()['busy'], 'replacement cleanup')
    # Even a blocked first write must release native replacements before it finishes.
    ipc('notifications','setDnd','on'); started.unlink(); stall.touch()
    identifier=send_buffer('Awaiting first history write', alias='')
    wait(started.exists, 'raw test worker stalled')
    send_buffer('Raw during first write', replace=identifier)
    wait(lambda:state()['nativeTracked']==state()['tracked']==0, 'release while worker stalled')
    for i in range(50): send_buffer('Large raw flood '+str(i))
    wait(lambda:state()['nativeTracked']==0, 'first raw flood releases')
    raw_first=rss()
    for i in range(50,100): send_buffer('Large raw flood '+str(i))
    wait(lambda:state()['nativeTracked']==0, 'second raw flood releases')
    raw_second=rss(); raw_state=state()
    assert raw_state['tracked']==raw_state['retainedActions']==0 and raw_state['queuedJobs']<=64, raw_state
    assert raw_second-raw_first < 20*1024, (raw_first,raw_second)
    stall.unlink()
    wait(lambda:not state()['busy'] and state()['nativeTracked']==0, 'raw writes drain')
    assert any(json.loads(p.read_text())['summary']=='Raw during first write' for p in (base/'state/omarchy/notifications/history').glob('*.json'))
    print('Raw 4 MiB buffer plateau:',json.dumps({'rssKiB':[raw_first,raw_second],'nativeTracked':raw_state['nativeTracked'],'queuedJobs':raw_state['queuedJobs']}))
    ipc('notifications','dismissAll'); ipc('notifications','clear'); ipc('notifications','setDnd','off')
    wait(lambda:not state()['busy'], 'context test cleanup')
    context_stall.touch()
    cfg=json.loads((config/'shell.json').read_text());cfg['plugins'][0]['suppressFullscreen']=True
    (config/'shell.json').write_text(json.dumps(cfg))
    wait(lambda:state()['settings']['suppressFullscreen'] and context_started.exists(), 'context worker stalled')
    identifier=send_buffer('Awaiting compositor context',alias='')
    wait(lambda:state()['pending']==1, 'native context backlog')
    send_buffer('Raw replacement awaiting context',replace=identifier)
    send_buffer('Raw initial awaiting context')
    wait(lambda:state()['pending']==2 and state()['nativeTracked']==0, 'context retains primitives only')
    context_stall.unlink()
    wait(lambda:state()['pending']==0 and not state()['busy'], 'detached context delivery')
    files=list((base/'state/omarchy/notifications').glob('*.json'))
    assert {json.loads(p.read_text())['summary'] for p in files}=={'Raw replacement awaiting context','Raw initial awaiting context'}, files
    assert process.poll() is None
    log.flush()
    output=(base/'runtime.log').read_text()
    for unexpected in ['Invalid notification image result','callback failed','ReferenceError','TypeError','ERROR']:
        assert unexpected not in output, output
    print('PASS: real stalled-worker DND/popup floods, byte/count plateaus, cancellation, oversized intake/replacement rejection, cleanup, raw-pixel release/retention replacements, primitive context backlog and recovery')
    print('DND plateau:', json.dumps({'jobs':second['queuedJobs'],'bytes':second['queuedBytes'],'tracked':second['tracked'],'rssKiB':[first_rss,second_rss]}))
    print('Popup plateau:', json.dumps({'jobs':popup_second['queuedJobs'],'bytes':popup_second['queuedBytes'],'popups':popup_second['popups']}))
finally:
    stall.unlink(missing_ok=True)
    context_stall.unlink(missing_ok=True)
    if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
    log.close()
    print('Runtime log:', base/'runtime.log')
