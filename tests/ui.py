"""Run QtTest input tests inside Quickshell, which embeds its QML plugins."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile

source=Path(__file__).resolve().parents[1]
base=Path(tempfile.mkdtemp(prefix='foamy-notifications-ui-'))
app=base/'app';app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons',target_is_directory=True)
(app/'components').symlink_to(source/'components',target_is_directory=True)
(app/'NotificationLogic.js').symlink_to(source/'NotificationLogic.js')
test=(source/'tests/tst_card.qml').read_text().replace('import "../components"','import "components"').replace('Item {\n  width:', 'Item {\n  id: testRoot\n  property bool ready: false\n  Timer { interval: 400; running: true; onTriggered: testRoot.ready=true }\n  width:').replace('when: windowShown','when: testRoot.ready')
(app/'CardTests.qml').write_text(test)
(app/'avatar.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="16" fill="#5f9ea0"/></svg>')
(app/'fallback.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="#ed4247"/></svg>')
(app/'preview.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#5f9ea0"/><rect x="40" y="40" width="560" height="280" fill="#334455"/></svg>')
(app/'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot { FloatingWindow { visible: true; implicitWidth: 500; implicitHeight: 400; CardTests {} } }
''')
home=base/'home';home.mkdir();runtime=base/'runtime';runtime.mkdir(mode=0o700)
env=dict(os.environ,HOME=str(home),XDG_RUNTIME_DIR=str(runtime),QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion')
for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
process=subprocess.Popen(['dbus-run-session','--','quickshell','-p',str(app),'--no-color'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
try:output=process.communicate(timeout=15)[0]
except subprocess.TimeoutExpired:
    os.killpg(process.pid,signal.SIGTERM);output=process.communicate(timeout=3)[0]
print(output)
# completedChanged runs before QtTest counts cleanupTestCase: fifteen tests plus init.
assert process.returncode==0 and 'UI_RESULT 16 passed 0 failed' in output
