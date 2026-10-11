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

SOURCE = Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    raise SystemExit(subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--inside'], timeout=60).returncode)
base = Path(tempfile.mkdtemp(prefix='foamy-popup-queue-'))
app = base/'app'; app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons', target_is_directory=True)
shutil.copytree(SOURCE, app/'plugin', ignore=shutil.ignore_patterns('.git', '__pycache__'))
service = (SOURCE/'Service.qml').read_text()
# Keep the real service/server/queue; presentation has separate desktop coverage.
(app/'plugin/Service.qml').write_text(service[:service.rfind('  Variants {')] + '}\n')
(app/'shell.qml').write_text('import Quickshell\nimport "plugin" as Plugin\nShellRoot { Plugin.Service {} }\n')
helper = app/'plugin/bin/notification-helper.py'
helper.rename(helper.with_name('notification-helper-real.py'))
stall = base/'stall'; started = base/'started'
# Stall only writes, not startup restoration. The timeout also protects failed tests.
helper.write_text('''import os,sys,time
from pathlib import Path
stall=Path(STALL)
if sys.argv[1] == 'write' and stall.exists():
    Path(STARTED).touch()
    deadline=time.monotonic()+30
    while stall.exists() and time.monotonic()<deadline: time.sleep(.02)
    if stall.exists(): raise SystemExit('test worker stall timed out')
os.execv(sys.executable,[sys.executable,str(Path(__file__).with_name('notification-helper-real.py')),*sys.argv[1:]])
'''.replace('STALL', repr(str(stall))).replace('STARTED', repr(str(started))))
home = base/'home'; home.mkdir()
config = home/'.config/omarchy'; config.mkdir(parents=True)
(config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','maxVisible':3}]}))
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
    assert process.poll() is None
    log.flush()
    output=(base/'runtime.log').read_text()
    for unexpected in ['Invalid notification image result','callback failed','ReferenceError','TypeError','ERROR']:
        assert unexpected not in output, output
    print('PASS: real stalled-worker DND/popup floods, byte/count plateaus, cancellation, oversized intake/replacement rejection, cleanup and recovery')
    print('DND plateau:', json.dumps({'jobs':second['queuedJobs'],'bytes':second['queuedBytes'],'tracked':second['tracked'],'rssKiB':[first_rss,second_rss]}))
    print('Popup plateau:', json.dumps({'jobs':popup_second['queuedJobs'],'bytes':popup_second['queuedBytes'],'popups':popup_second['popups']}))
finally:
    stall.unlink(missing_ok=True)
    if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
    log.close()
    print('Runtime log:', base/'runtime.log')
