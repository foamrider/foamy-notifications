"""Render the actual popup card at wide/narrow sizes with synthetic messages."""
import os
import signal
from pathlib import Path
import sys
import subprocess
import tempfile

source=Path(__file__).resolve().parents[1]
base=Path(tempfile.mkdtemp(prefix='foamy-notifications-render-'))
app=base/'app';app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons',target_is_directory=True)
(app/'plugin').symlink_to(source,target_is_directory=True)
(app/'avatar.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#435d5b"/><text x="32" y="42" text-anchor="middle" fill="#d9ede5" font-size="28" font-family="sans-serif">AJ</text></svg>')
(app/'screenshot.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#172535"/><rect x="55" y="40" width="530" height="280" rx="12" fill="#dfe6ed"/><path d="M55 65H585" stroke="#647997" stroke-width="40"/><rect x="75" y="100" width="75" height="190" fill="#aebed0"/><path d="M185 125H540M185 165H540M185 205H540M185 245H420" stroke="#819ab1" stroke-width="14"/></svg>')
(app/'shell.qml').write_text('''import QtQuick
import Quickshell
import qs.Commons
import "plugin/components"
ShellRoot {
  FloatingWindow {
    id: window
    visible: true
    implicitWidth: 500; implicitHeight: 360
    color: "#151521"
    NotificationCard {
      id: card
      x: 20; y: 20
      width: 420
      image: Qt.resolvedUrl("avatar.svg").toString()
      app: "Vivaldi"; appIcon: "vivaldi"; summary: "Alex · Design review"
      body: "The updated files are ready. Have a look when you have a moment."
      timestamp: 1790930400000
      actions: [{identifier:"open",text:"Open Teams"},{identifier:"read",text:"Mark as read"}]
      count: 3
      timeoutMs: 8000; remainingMs: 8000
    }
    Timer {
      id: step
      interval: 500; running: true; repeat: false
      property int index: 0
      onTriggered: {
        if (card.implicitHeight > 330) throw new Error("Card overflow")
        card.grabToImage(function(result) {
          if (!result.saveToFile(Qt.resolvedUrl("capture-"+step.index+".png").toString().replace("file://",""))) throw new Error("Capture failed")
          step.index++
          card.remainingMs = step.index % 3 === 0 ? 8000 : step.index % 3 === 1 ? 4000 : 1000
          if (step.index===1) { card.width=280; card.urgency=2; card.app="System";card.summary="Backup needs attention";card.body="Connect your backup drive to continue." }
          else if (step.index===2) { card.image="";card.actions=[];card.app="A very long application name that must not overlap the clock";card.summary="A_long_unbroken_title_that_needs_to_fit_in_a_narrow_notification_without_overflow";card.body="<b>Bold text</b> and a deliberately long notification body that must wrap cleanly without covering the actions or the bottom border." }
          else if (step.index===3) { card.width=420;card.image=Qt.resolvedUrl("screenshot.svg").toString();card.app="Files";card.summary="Transfer complete";card.body="";card.count=1;card.urgency=1;Color.background="#eff1f5";Color.foreground="#4c4f69";Color.accent="#1e66f5";Color.urgent="#d20f39" }
          else if (step.index===4) { card.image="";card.width=390;card.urgency=2;card.app="omarchy-action";card.summary="Process crashed: xdg-desktop-portal-hyprland";card.body="Click to diagnose with AI";card.actions=[];Color.background="#202030";Color.foreground="#bdcafa";Color.accent="#a8bfff";Color.urgent="#f7768e" }
          else if (step.index===5) { card.width=280;card.actions=[{identifier:"reply",text:"Reply"},{identifier:"read",text:"Mark as read"},{identifier:"settings",text:"Innstillinger"},{identifier:"snooze",text:"Snooze"},{identifier:"open",text:"Open the original message in the application"}];card.actionsExpanded=true }
          else if (step.index===6) { card.width=390;card.urgency=1;card.app="omarchy-action";card.appIcon="";card.glyph="󰚩";card.summary="Screenshot saved to clipboard and file";card.body="Edit with Super + Alt + , (or click this)";card.image=Qt.resolvedUrl("screenshot.svg").toString();card.actions=[] }
          else if (step.index===7) { card.width=280 }
          else if (step.index===8) { card.image=Qt.resolvedUrl("missing-image.png").toString() }
          else if (step.index===9) { card.image=Qt.resolvedUrl("avatar.svg").toString();card.showImages=false }
          else if (step.index===10) { card.compact=false;card.showImages=true;card.width=390;card.app="Vivaldi";card.summary="Alex · Design review";card.body="The updated files are ready. Have a look when you have a moment.";card.actions=[{identifier:"open",text:"Open Teams"},{identifier:"read",text:"Mark as read"}] }
          else if (step.index===11) { card.width=280;Color.background="#eff1f5";Color.foreground="#4c4f69";Color.accent="#1e66f5";Color.urgent="#d20f39" }
          else { console.log("PASS: compact and roomy modes,  wide, narrow, urgent, long text, actions, empty body, light theme, shared avatars/screenshots, failed/disabled images");Qt.quit();return }
          step.restart()
        })
      }
    }
  }
}
''')
home=base/'home';home.mkdir()
runtime=base/'runtime';runtime.mkdir(mode=0o700)
desktop = '--desktop' in sys.argv
env=dict(os.environ,HOME=str(home),QT_QPA_PLATFORM='wayland' if desktop else 'offscreen',QT_QUICK_BACKEND='rhi' if desktop else 'software',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion',LANG='en_US.UTF-8')
command=['quickshell','-p',str(app),'--no-color']
if not desktop:
    env['XDG_RUNTIME_DIR']=str(runtime)
    for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
    command=['dbus-run-session','--',*command]
# Desktop rendering uses the GPU for rounded image masks; it owns only this test window.
process=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
try:
    output=process.communicate(timeout=15)[0]
except subprocess.TimeoutExpired:
    os.killpg(process.pid,signal.SIGTERM)
    output=process.communicate(timeout=3)[0]
print(output)
assert process.returncode==0 and 'PASS:' in output
print('Renders:',app)
