"""Save center preferences through the host API (shell.updateEntryInline), offscreen.

The fake shell stores exactly what the host's updateEntryInline would: {id, ...settings}
replaces the plugins[] entry, and an unchanged entry returns false without writing.
XDG_CONFIG_HOME points at a decoy, because the host always reads ~/.config.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import testhost
from testhost import hostenv

testhost.session(__file__, 60)
source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix='foamy-preferences-runtime-'))
app = base/'app'; app.mkdir()
home = base/'home'; home.mkdir()
runtime = base/'runtime'; runtime.mkdir(mode=0o700)
for name in ['Commons', 'Ui']:
    (app/name).symlink_to(hostenv.root()/'shell'/name, target_is_directory=True)
shutil.copytree(source, app/'plugin', ignore=shutil.ignore_patterns('.git', '__pycache__'))
service = (source/'Service.qml').read_text()
(app/'plugin/Service.qml').write_text(service[:service.index('  Variants {')]+'}\n')
(app/'shell.qml').write_text('''import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
 FileView { id: hostConfig; path: Quickshell.env("HOME") + "/.config/omarchy/shell.json"; watchChanges: true; onFileChanged: reload() }
 QtObject {
  id: fakeShell
  property bool refuse: false
  property int writes: 0
  function updateEntryInline(id, settings) {
   if (refuse || id !== "foamy.notifications") return false
   var config = JSON.parse(hostConfig.text()), dirty = false
   config.plugins.forEach(function(entry, index) {
    if (entry.id !== id) return
    var next = {id: id}
    for (var key in settings) if (key !== "id") next[key] = settings[key]
    if (JSON.stringify(entry) !== JSON.stringify(next)) { config.plugins[index] = next; dirty = true }
   })
   if (!dirty) return false
   writes++
   hostConfig.setText(JSON.stringify(config, null, 2) + "\\n")
   return true
  }
 }
 Plugin.Service { id: notifications; shell: fakeShell }
 IpcHandler { target: "preferences-test"
  function refuse(on: bool): void { fakeShell.refuse = on }
  function detach(): void { notifications.shell = null }
  function writes(): int { return fakeShell.writes }
 }
}''')
config = home/'.config/omarchy'; config.mkdir(parents=True)
stored = {'version': 1, 'bar': {'layout': {'right': [{'id': 'foamy.notification-center', 'compact': False}]}},
          'plugins': [{'id': 'other.service', 'value': 7}, {'id': 'foamy.notifications', 'browserGrouping': 'none', 'custom': 'preserve'}]}
(config/'shell.json').write_text(json.dumps(stored))
decoy = base/'xdg/omarchy'; decoy.mkdir(parents=True)
(decoy/'shell.json').write_text(json.dumps({'plugins': [{'id': 'foamy.notifications', 'compact': False, 'normalTimeoutSec': 99}]}))
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(base/'xdg'), XDG_STATE_HOME=str(home/'.local/state'), XDG_RUNTIME_DIR=str(runtime),
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QPA_PLATFORMTHEME='basic')
for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE']: env.pop(key, None)
# Host QML/data only: OMARCHY_PATH exposes shell/ and default/; omarchy-* are stubs.
testhost.make(base, app, env)
log = (base/'runtime.log').open('w')
proc = None

def ipc(target, *args): return subprocess.check_output(['qs', 'ipc', '-n', '-p', str(app), 'call', target, *args], env=env, text=True, timeout=2, stderr=subprocess.DEVNULL).strip()
def preferences(): return json.loads(ipc('foamy.notifications', 'preferences'))
def entry(): return [p for p in json.loads((config/'shell.json').read_text())['plugins'] if p['id'] == 'foamy.notifications'][0]
def wait(fn):
    end = time.monotonic()+5
    while time.monotonic() < end:
        try:
            if fn(): return
        except (ValueError, subprocess.SubprocessError): pass
        time.sleep(.05)
    raise AssertionError((base/'runtime.log').read_text())
def save(key, value):
    # Leading space: JSON stays out of the IPC CLI's option parser, as the center sends it.
    assert ipc('foamy.notifications', 'setPreference', key, ' '+json.dumps(value)) == 'saving'
    wait(lambda: not preferences()['saving'])
    return preferences()['saveError']
try:
    proc = subprocess.Popen(['qs', '-p', str(app), '--no-color'], env=env, stdout=log, stderr=log, start_new_session=True)
    wait(lambda: ipc('notifications', 'ping') == 'ok')
    wait(lambda: preferences()['settings']['normalTimeoutSec'] == 8)
    assert preferences()['settings']['compact'] is True, 'read the XDG_CONFIG_HOME decoy'
    # The full raw entry is written: unknown keys stay, defaults are not filled in.
    assert save('compact', False) == ''
    assert entry() == {'id': 'foamy.notifications', 'browserGrouping': 'none', 'custom': 'preserve', 'compact': False}, entry()
    assert json.loads((config/'shell.json').read_text())['plugins'][0] == stored['plugins'][0]
    wait(lambda: preferences()['settings']['compact'] is False)
    # Enabling duplicate grouping lifts the legacy browser override.
    assert save('groupDuplicates', True) == ''
    assert entry()['groupDuplicates'] is True and entry()['browserGrouping'] == 'browser', entry()
    # An unchanged value is stored already: the host reports no change, the save succeeds.
    writes = int(ipc('preferences-test', 'writes'))
    assert save('compact', False) == ''
    assert int(ipc('preferences-test', 'writes')) == writes
    assert ipc('foamy.notifications', 'setPreference', 'normalTimeoutSec', ' 0') == 'error: invalid value'
    assert ipc('foamy.notifications', 'setPreference', 'browserGrouping', ' "none"') == 'error: unknown setting'
    before = (config/'shell.json').read_text()
    ipc('preferences-test', 'refuse', 'true')
    assert save('showImages', False) == 'Could not save the setting'
    ipc('preferences-test', 'refuse', 'false')
    assert (config/'shell.json').read_text() == before
    # Saving never adds a missing entry.
    (config/'shell.json').write_text(json.dumps({**stored, 'plugins': [stored['plugins'][0]]}))
    wait(lambda: preferences()['settings']['compact'] is True)
    assert save('showImages', False) == 'Foamy Notifications is not enabled'
    ipc('preferences-test', 'detach')
    assert save('showImages', False) == 'Omarchy cannot save plugin settings'
    print('PASS center preferences save the full raw entry through shell.updateEntryInline; unchanged, refused, missing and detached saves')
finally:
    if proc and proc.poll() is None: os.killpg(proc.pid, signal.SIGTERM); proc.wait(timeout=3)
    log.close(); print('Evidence:', base)
    hostenv.check(env, (base/'runtime.log').read_text(), testhost.calls(base))
