"""Exercise feature delivery with real D-Bus traffic and a private PipeWire server."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

if '--inside' not in sys.argv:
    raise SystemExit(subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--inside', *sys.argv[1:]], timeout=30).returncode)
source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix='foamy-features-runtime-'))
app = base/'app'; app.mkdir()
home = base/'home'; home.mkdir()
runtime = base/'runtime'; runtime.mkdir(mode=0o700)
host = Path(os.environ.get('OMARCHY_TEST_ROOT', '/usr/share/omarchy'))
for name in ['Commons', 'Ui']:
    (app/name).symlink_to(host/'shell'/name, target_is_directory=True)
shutil.copytree(source, app/'plugin', ignore=shutil.ignore_patterns('.git', '__pycache__'))
service = (source/'Service.qml').read_text()
desktop = '--desktop' in sys.argv
if desktop:
    # Retain the real layer surfaces and capture the sharing prompt after detection.
    service = service.replace('  id: service', '  id: service\n  property var sharingTestCard: null', 1)
    service = service.replace('Component.onCompleted: service.sharingPromptHeight = implicitHeight', 'Component.onCompleted: { service.sharingPromptHeight = implicitHeight; service.sharingTestCard = sharingCard }', 1)
else:
    service = service[:service.index('  Variants {')]+'}\n'
(app/'plugin/Service.qml').write_text(service)
(app/'shell.qml').write_text('''import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
 Plugin.Service { id: notifications }
 IpcHandler { target: "feature-test"
  function snooze(): string { return notifications.snoozeSource("app:Chat", 30) }
  function offer(kind: string, value: string): void { notifications.featureAction(notifications.displayGroups[0], kind, value) }
  function state(): string { return JSON.stringify({sharing:notifications.sharingActive,offer:notifications.sharingOfferPending,clipboard:notifications.clipboardSecret,entries:notifications.popupModel.count}) }
  function capture(): void { notifications.sharingTestCard.grabToImage(function(result) { result.saveToFile(Qt.resolvedUrl("sharing.png").toString().replace("file://", "")) }) }
  function shortSnooze(): void { notifications.snoozes = {"app:Chat": Date.now()+500}; notifications.quietNow = Date.now() }
 }
}''')
config=home/'.config/omarchy';config.mkdir(parents=True)
(config/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','dndSilenceCritical':True}]}))
cli=base/'bin';cli.mkdir()
# The helper uses actual stdin and exit statuses; it cannot touch the real clipboard.
(cli/'wl-copy').write_text('#!/bin/bash\n[[ ${1:-} == --sensitive ]] || exit 2\ncat > "$HOME/copied"\n')
(cli/'wl-copy').chmod(0o755)
env=dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home/'.config'), XDG_STATE_HOME=str(home/'.local/state'), XDG_RUNTIME_DIR=str(runtime), PATH=str(cli)+':'+os.environ['PATH'], OMARCHY_PATH=str(host), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QPA_PLATFORMTHEME='basic')
for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
if desktop:
    env['QT_QPA_PLATFORM'] = 'wayland'
    env['QT_QUICK_BACKEND'] = 'rhi'
    env['WAYLAND_DISPLAY'] = str(Path(os.environ['XDG_RUNTIME_DIR']) / os.environ['WAYLAND_DISPLAY'])
pw_config=base/'pipewire.conf'
pw_config.write_text('''context.properties = { core.daemon = true core.name = pipewire-0 }
context.spa-libs = { support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-access args = { access.socket = { pipewire-0 = unrestricted } } }
]
''')
log=(base/'runtime.log').open('w')
pw=subprocess.Popen(['pipewire','-c',str(pw_config)],env=env,stdout=log,stderr=log,start_new_session=True)
proc=None

def start():return subprocess.Popen(['qs','-p',str(app),'--no-color'],env=env,stdout=log,stderr=log,start_new_session=True)
def ipc(target,*args):return subprocess.check_output(['qs','ipc','-n','-p',str(app),'call',target,*args],env=env,text=True,timeout=2,stderr=subprocess.DEVNULL).strip()
def state():return json.loads(ipc('foamy.notifications','state'))
def wait(fn):
    end=time.monotonic()+5
    while time.monotonic()<end:
        try:
            if fn():return
        except (ValueError,subprocess.SubprocessError):pass
        time.sleep(.05)
    raise AssertionError((base/'runtime.log').read_text())
def send(app,title,body='Message'):
    subprocess.run(['notify-send','-a',app,'-u','critical',title,body],env=env,check=True,timeout=2)
def files():return list([p for p in (home/'.local/state/foamy-notifications').glob('*/*.json')])
try:
    wait(lambda:(runtime/'pipewire-0').exists())
    proc=start();wait(lambda:ipc('notifications','ping')=='ok')
    send('Chat','Verification code','Use code 482913')
    wait(lambda:state()['popups']==1 and not state()['busy'])
    assert not any('482913' in p.read_text() for p in files()),'code leaked to persisted state'
    ipc('feature-test','offer','code','482913')
    wait(lambda:(home/'copied').exists())
    assert (home/'copied').read_text()=='482913'
    ipc('feature-test','snooze')
    wait(lambda:state()['popups']==0 and not state()['busy'])
    send('Chat','Held during snooze')
    wait(lambda:any('Held during snooze' in p.read_text() for p in files()) and not state()['busy'])
    assert state()['popups']==0
    send('Mail','Other sender')
    wait(lambda:state()['popups']==1 and not state()['busy'])
    ipc('notifications','dismissAll');wait(lambda:state()['popups']==0 and not state()['busy'])
    wait(lambda:'app:Chat' in json.loads((home/'.local/state/foamy-notifications/state.json').read_text()).get('snoozes',{}))
    os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=3)
    proc=start();wait(lambda:'app:Chat' in state()['snoozes'])
    ipc('feature-test','shortSnooze');wait(lambda:not state()['snoozes'])
    send('Chat','After snooze');wait(lambda:state()['popups']==1)
    # A protocol-native node exercises the actual Quickshell PipeWire model and tracker.
    subprocess.run(['pw-cli','create-node','spa-node-factory','{ factory.name = support.null-audio-sink node.name = foamy-test media.class = Video/Source media.name = xdph-streaming-foamy-test object.linger = true }'],env=env,check=True,timeout=3,stdout=subprocess.DEVNULL)
    wait(lambda:json.loads(ipc('feature-test','state'))['sharing'])
    assert json.loads(ipc('feature-test','state'))['offer']
    assert not state()['doNotDisturb'] and state()['popups']==1,'sharing must not silently mute or enter history'
    if desktop:
        time.sleep(.3)
        ipc('feature-test','capture');wait(lambda:(app/'sharing.png').exists())
    ipc('notifications','snoozeAll','30');wait(lambda:not json.loads(ipc('feature-test','state'))['offer'])
    send('notify-send','Critical alert during snooze')
    wait(lambda:any('Critical alert during snooze' in p.read_text() for p in files()) and not state()['busy'])
    assert state()['popups']==0
    print('PASS code copy through stdin and disk redaction; sender/global snooze, expiry and restart; private PipeWire sharing detection and opt-in prompt')
finally:
    for child in [proc,pw]:
        if child and child.poll() is None:os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=3)
    log.close();print('Evidence:',base)
