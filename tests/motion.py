"""Exercise the shipped popup delegate's animations on an isolated Qt surface."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

source=Path(__file__).resolve().parents[1]
base=Path(tempfile.mkdtemp(prefix='foamy-notifications-motion-'));app=base/'app';app.mkdir()
(app/'Commons').symlink_to('/usr/share/omarchy/shell/Commons',target_is_directory=True)
(app/'components').symlink_to(source/'components',target_is_directory=True)
(app/'NotificationLogic.js').symlink_to(source/'NotificationLogic.js')
s=(source/'Service.qml').read_text();start=s.index('          NotificationCard {',s.index('  Variants {'));end=s.index('\n      }\n    }\n  }',start)
delegate=s[start:end].replace('service.','fixture.').replace('popupWindow.screen.name','"TEST-1"')
(app/'shell.qml').write_text('''import QtQuick
import QtTest
import Quickshell
import qs.Commons
import "components"
ShellRoot {
 Item {
  id: fixture
  property var shell: null
  property var preferences: ({compact:true,showTimeoutIndicator:true,showImages:true,imageSize:56})
  property var hoveredKeys: ({})
  property var busyKeys: ({})
  property var actionErrors: ({})
  function durationFor(urgency, timeout) { return 8000 }
  function remainingFor(group) { return 4000 }
  function removeKeys(keys,reason) { popupColumn.groups=[] }
  function activateGroup(group,identifier) { popupColumn.groups=[] }
 }
 FloatingWindow {
  id: window
  visible: true; implicitWidth:500;implicitHeight:260;color:"#17171c"
  NotificationStack { id:popupColumn;x:20;y:20;width:390;spacing:8
   delegate: DELEGATE
  }
  property bool ready:false
  Timer {interval:400;running:true;onTriggered:window.ready=true}
  property var watched:null
  property int entryFrames:0
  property int exitFrames:0
  property int movingFrames:0
  FrameAnimation {running:true;onTriggered:{
   var card=window.watched
   if (!card) return
   if (card.opacity>0 && card.opacity<1) {if(card.retired)window.exitFrames++;else window.entryFrames++}
   if (!card.retired && card.entranceOffset>0 && card.entranceOffset<8)window.movingFrames++
  }}
  TestCase {
   name:"NotificationMotion";when:window.ready
   onCompletedChanged:if(completed)console.log("MOTION_RESULT",qtest_results.passCount,"passed",qtest_results.failCount,"failed")
   function check(condition,message){if(!condition)console.log("FAILED",message);verify(condition,message)}
   function row(key,title){return {key:key,keys:[key],app:"Teams",appIcon:"",image:"",summary:title,body:"The updated files are ready. Have a look when you have a moment.",glyph:"",urgency:1,timestamp:0,actionsJson:"[]"}}
   function capture(name){popupColumn.grabToImage(function(result){result.saveToFile(Qt.resolvedUrl(name+".png").toString().replace("file://",""))})}
   function test_motion_data(){return [{tag:"wide-dark",width:390,light:false},{tag:"narrow-light",width:280,light:true}]}
   function test_motion(data){try{
    mouseMove(window.contentItem,490,250)
    popupColumn.width=data.width
    Color.background=data.light?"#eff1f5":"#202030";Color.foreground=data.light?"#4c4f69":"#bdcafa"
    window.entryFrames=0;window.exitFrames=0;window.movingFrames=0
    popupColumn.groups=[row("1-1","Alex · Design review")]
    tryCompare(popupColumn,"count",1)
    tryVerify(function(){return popupColumn.itemAtIndex(0)!==null})
    var card=popupColumn.itemAtIndex(0);verify(card);window.watched=card
    wait(45);capture(data.tag+"-entrance")
    tryCompare(card,"opacity",1);tryCompare(card,"entranceOffset",0)
    check(window.entryFrames>0,"entrance must have intermediate opacity frames")
    check(window.movingFrames>0,"entrance must move smoothly")
    capture(data.tag+"-settled")
    popupColumn.groups=[row("1-1","Updated message")];wait(60)
    compare(popupColumn.itemAtIndex(0),card);compare(card.opacity,1);compare(card.entranceOffset,0)
    mouseMove(card,100,50);tryCompare(popupColumn,"holdPositions",true)
    popupColumn.groups=[]
    tryCompare(card,"retired",true);verify(!card.enabled)
    check(popupColumn.implicitHeight>0,"last card must retain its surface during fade")
    wait(35);capture(data.tag+"-exit")
    tryVerify(function(){return popupColumn.implicitHeight===0})
    check(window.exitFrames>0,"exit must have intermediate opacity frames")
    check(!popupColumn.holdPositions,"last card must not leave an invisible input region")
    window.watched=null
   }catch(e){console.log("MOTION_FAILURE",e.message,e.stack);throw e}}
   function test_removal_during_entrance(){
    popupColumn.groups=[row("2-2","Short lived")];tryCompare(popupColumn,"count",1)
    tryVerify(function(){return popupColumn.itemAtIndex(0)!==null})
    var card=popupColumn.itemAtIndex(0)
    wait(20);popupColumn.groups=[]
    tryVerify(function(){return popupColumn.implicitHeight===0})
    wait(200);compare(popupColumn.count,0);compare(popupColumn.implicitHeight,0)
   }
  }
 }
}
'''.replace('DELEGATE',delegate))
home=base/'home';home.mkdir();runtime=base/'runtime';runtime.mkdir(mode=0o700)
desktop='--desktop' in sys.argv
env=dict(os.environ,HOME=str(home),QT_QPA_PLATFORM='wayland' if desktop else 'offscreen',QT_QUICK_BACKEND='rhi' if desktop else 'software',QT_QPA_PLATFORMTHEME='basic',QT_STYLE_OVERRIDE='Fusion')
if not desktop:
 env['XDG_RUNTIME_DIR']=str(runtime)
 for key in ['DISPLAY','WAYLAND_DISPLAY','HYPRLAND_INSTANCE_SIGNATURE']:env.pop(key,None)
process=subprocess.Popen(['dbus-run-session','--','quickshell','-p',str(app),'--no-color'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
try:output=process.communicate(timeout=15)[0]
except subprocess.TimeoutExpired:
 os.killpg(process.pid,signal.SIGTERM);output=process.communicate(timeout=3)[0]
print(output);print('Motion captures:',app)
assert process.returncode==0 and 'MOTION_RESULT 4 passed 0 failed' in output
