"""Exercise the shipped popup delegate's animations on an isolated Qt surface."""

import os
from pathlib import Path
import sys
import tempfile
import testhost
from testhost import hostenv

source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix="foamy-notifications-motion-"))
app = base / "app"
app.mkdir()
for name in ("Commons", "Ui"):
    (app / name).symlink_to(hostenv.root() / "shell" / name, target_is_directory=True)
# The plugin's kit/ (FoamyI18n, FoamyStyle, …) and the files the card imports.
(app / "kit").symlink_to(source / "kit", target_is_directory=True)
(app / "i18n").symlink_to(source / "i18n", target_is_directory=True)
for name in ("Markup.js", "BrowserIdentity.js"):
    (app / name).symlink_to(source / name)
(app / "qmldir").write_text("")
(app / "components").symlink_to(source / "components", target_is_directory=True)
(app / "NotificationLogic.js").symlink_to(source / "NotificationLogic.js")
s = (source / "Service.qml").read_text()
start = s.index("NotificationCard {", s.index("delegate: NotificationCard {", s.index("  Variants {")))
end = s.index("\n      }\n    }\n  }", start)
delegate = s[start:end].replace("service.", "fixture.").replace("popupWindow.screen.name", '"TEST-1"')
(app / "shell.qml").write_text(
    """import QtQuick
import QtTest
import Quickshell
import qs.Commons
import qs.Commons as Commons
import "components"
import "kit"
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
  // The rest of the service surface the shipped delegate reads (keyboard mode, deeds, clipboard).
  property bool keyboardMode: false
  property string keyboardKey: ""
  property string clipboardCopiedKey: ""
  property string clipboardSecret: ""
  function stopKeyboard() {}
  function deedsFor(group) { return [] }
  function offersFor(row) { return {codes:"", link:"", meeting:false} }
  function popupImageFor(row, role) { return "" }
  function featureAction(group, kind, value) {}
  function dismissGroup(group, fromClose) { popupColumn.groups=popupColumn.groups.filter(function(g) { return g.key!==group.key }) }
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
  // Frames on which a tracked card sits strictly between two stack positions.
  property var tracked:null
  property real trackFrom:0
  property real trackTo:0
  property int betweenFrames:0
  // Frames on which a card that must stay put (under the pointer) is elsewhere.
  property var pinned:null
  property real pinnedY:0
  property int pinnedMoves:0
  FrameAnimation {running:true;onTriggered:{
   if (window.pinned && Math.abs(window.pinned.mapToItem(popupColumn,0,0).y-window.pinnedY)>0.5) window.pinnedMoves++
   if (window.tracked) {
    var y=window.tracked.mapToItem(popupColumn,0,0).y
    if (y>Math.min(window.trackFrom,window.trackTo)+0.5 && y<Math.max(window.trackFrom,window.trackTo)-0.5) window.betweenFrames++
   }
   var card=window.watched
   if (!card) return
   if (card.opacity>0 && card.opacity<1) {if(card.retired)window.exitFrames++;else window.entryFrames++}
   if (!card.retired && card.entranceOffset>0 && card.entranceOffset<8)window.movingFrames++
  }}
  TestCase {
   name:"NotificationMotion";when:window.ready
   onCompletedChanged:if(completed)console.log("MOTION_RESULT",qtest_results.passCount,"passed",qtest_results.failCount,"failed")
   // A failed test must not leave cards, the pointer or reduced motion to the next.
   function cleanup(){
    Commons.Style.reduceMotion=false;window.tracked=null;window.watched=null;window.pinned=null;popupColumn.waiting=0
    mouseMove(window.contentItem,490,250);popupColumn.groups=[]
    tryVerify(function(){return popupColumn.implicitHeight===0},3000)
   }
   function check(condition,message){if(!condition)console.log("FAILED",message);verify(condition,message)}
   function row(key,title){return {key:key,keys:[key],app:"Teams",appIcon:"",image:"",summary:title,body:"The updated files are ready. Have a look when you have a moment.",glyph:"",urgency:1,timestamp:0,actionsJson:"[]"}}
   function capture(name){popupColumn.grabToImage(function(result){result.saveToFile(Qt.resolvedUrl(name+".png").toString().replace("file://",""))})}
   function test_motion_data(){return [{tag:"wide-dark",width:390,light:false},{tag:"narrow-light",width:280,light:true}]}
   function test_motion(data){try{
    mouseMove(window.contentItem,490,250)
    popupColumn.width=data.width
    Commons.Color.background=data.light?"#eff1f5":"#202030";Commons.Color.foreground=data.light?"#4c4f69":"#bdcafa"
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
   function test_close_moves_cards_below_up(){try{
    // Positions on screen (ListView moves its origin when cards leave at the top).
    function at(card){return card.mapToItem(popupColumn,0,0).y}
    popupColumn.groups=[row("3-1","Top"),row("3-2","Middle"),row("3-3","Bottom")]
    tryCompare(popupColumn,"count",3)
    tryVerify(function(){return popupColumn.itemAtIndex(2)!==null})
    var top=popupColumn.itemAtIndex(0),middle=popupColumn.itemAtIndex(1),bottom=popupColumn.itemAtIndex(2)
    tryCompare(bottom,"opacity",1)
    var middleY=at(middle),bottomY=at(bottom)
    check(middleY>0&&bottomY>middleY,"three stacked cards")
    mouseMove(middle,100,20);tryCompare(popupColumn,"holdPositions",true)
    // A card above the pointer expires: the hovered card and the ones below stay put.
    popupColumn.groups=[row("3-2","Middle"),row("3-3","Bottom")]
    tryCompare(top,"retired",true);wait(500)
    check(at(middle)===middleY,"hovered card stays under the pointer");check(at(bottom)===bottomY,"cards below stay put")
    // Closing the hovered card moves the cards below it up, while the expired
    // card above still holds its space.
    popupColumn.groups=[row("3-3","Bottom")]
    tryCompare(middle,"retired",true)
    tryVerify(function(){return at(bottom)===middleY},3000,"the card below moves up into the closed card's place")
    check(popupColumn.holdPositions,"the pointer is still over the stack")
    // Leaving the stack closes the remaining gap.
    mouseMove(window.contentItem,490,250)
    tryVerify(function(){return at(bottom)===0},3000,"leaving the stack closes the gap")
    popupColumn.groups=[];tryVerify(function(){return popupColumn.implicitHeight===0})
   }catch(e){console.log("MOTION_FAILURE",e.message,e.stack);throw e}}
   function test_close_top_card_repeatedly(){try{
    // The user's report: three criticals, the pointer on the top card's ×.
    // Each close must move the next card up under the pointer, every time.
    function at(card){return card.mapToItem(popupColumn,0,0).y}
    function critical(key,title){var r=row(key,title);r.urgency=2;return r}
    popupColumn.groups=[critical("4-1","Critical notification 3"),critical("4-2","Critical notification 2"),critical("4-3","Critical notification 1")]
    tryCompare(popupColumn,"count",3)
    tryVerify(function(){return popupColumn.itemAtIndex(2)!==null})
    var first=popupColumn.itemAtIndex(0),second=popupColumn.itemAtIndex(1),third=popupColumn.itemAtIndex(2)
    tryCompare(third,"opacity",1)
    var close=findChild(first,"dismissButton");verify(close)
    var point=close.mapToItem(window.contentItem,close.width/2,close.height/2)
    mouseMove(window.contentItem,point.x,point.y);tryCompare(popupColumn,"holdPositions",true)
    mouseClick(window.contentItem,point.x,point.y)
    tryCompare(first,"retired",true)
    tryVerify(function(){return at(second)===0},3000,"the second card moves up under the pointer")
    mouseClick(window.contentItem,point.x,point.y)
    tryCompare(second,"retired",true)
    tryVerify(function(){return at(third)===0},3000,"the third card moves up under the pointer")
    check(popupColumn.holdPositions,"the pointer is still over the stack")
    mouseClick(window.contentItem,point.x,point.y)
    tryCompare(third,"retired",true)
    mouseMove(window.contentItem,490,250)
    tryVerify(function(){return popupColumn.implicitHeight===0})
   }catch(e){console.log("MOTION_FAILURE",e.message,e.stack);throw e}}
   function test_move_up_data(){return [{tag:"animations-on",reduce:false},{tag:"animations-off",reduce:true}]}
   function test_move_up(data){try{
    // Cards that move up after a removal slide there, unless Omarchy's
    // animations are off (Style.reduceMotion), when they move at once.
    function at(card){return card.mapToItem(popupColumn,0,0).y}
    Commons.Style.reduceMotion=data.reduce
    mouseMove(window.contentItem,490,250)
    popupColumn.groups=[row("5-1","Top"),row("5-2","Middle"),row("5-3","Bottom")]
    tryCompare(popupColumn,"count",3)
    tryVerify(function(){return popupColumn.itemAtIndex(2)!==null})
    var top=popupColumn.itemAtIndex(0),middle=popupColumn.itemAtIndex(1),bottom=popupColumn.itemAtIndex(2)
    tryCompare(bottom,"opacity",1);tryCompare(bottom,"entranceOffset",0)
    var middleY=at(middle),bottomY=at(bottom)
    function moves(card,from,to,removal){
     window.betweenFrames=0;window.trackFrom=from;window.trackTo=to;window.tracked=card
     popupColumn.groups=removal
     tryVerify(function(){return at(card)===to},3000,"the card reaches its new place")
     var frames=window.betweenFrames;window.tracked=null
     return frames
    }
    // A card in the middle leaves: the card below moves up (its own y changes).
    var frames=moves(bottom,bottomY,middleY,[row("5-1","Top"),row("5-3","Bottom")])
    if(data.reduce)check(frames===0,"with animations off the card below moves at once ("+frames+" frames)")
    else check(frames>1,"the card below slides up ("+frames+" frames)")
    // The top card leaves: the rest move up (ListView moves its origin instead).
    frames=moves(bottom,middleY,0,[row("5-3","Bottom")])
    if(data.reduce)check(frames===0,"with animations off the stack moves at once ("+frames+" frames)")
    else check(frames>1,"the stack slides up after the top card leaves ("+frames+" frames)")
   }catch(e){console.log("MOTION_FAILURE",e.message,e.stack);throw e}}
   function test_waiting_card_joins_below_hovered(){try{
    // A card from the overflow queue joins the bottom of the stack: while the
    // pointer rests on a card, nothing above it moves it, not a card leaving
    // above (its space is kept), below, or a waiting card appearing.
    function at(card){return card.mapToItem(popupColumn,0,0).y}
    popupColumn.waiting=2
    popupColumn.groups=[row("6-1","Top"),row("6-2","Hovered"),row("6-3","Bottom")]
    tryCompare(popupColumn,"count",3)
    tryVerify(function(){return popupColumn.itemAtIndex(2)!==null})
    var hovered=popupColumn.itemAtIndex(1),bottom=popupColumn.itemAtIndex(2)
    tryCompare(bottom,"opacity",1);tryCompare(bottom,"entranceOffset",0)
    var note=popupColumn.footerItem
    check(note&&note.height>0,"the waiting note shows under the stack")
    check(note.mapToItem(popupColumn,0,0).y>=at(bottom)+bottom.height,"the note sits below the last card")
    var noteLabel=findChild(note,"waitingNote");check(noteLabel&&noteLabel.children[0].text==="+2 waiting","the note counts the waiting cards")
    var hoveredY=at(hovered),bottomY=at(bottom)
    capture("waiting-note");wait(150)
    mouseMove(hovered,100,20);tryCompare(popupColumn,"holdPositions",true)
    window.pinnedY=hoveredY;window.pinnedMoves=0;window.pinned=hovered
    // The top card expires and the first waiting card shows, under the bottom card.
    popupColumn.waiting=1
    popupColumn.groups=[row("6-2","Hovered"),row("6-3","Bottom"),row("6-4","Waited")]
    tryVerify(function(){var c=popupColumn.itemAtIndex(2);return popupColumn.count===3&&c&&c.summary==="Waited"&&c.opacity===1&&c.entranceOffset===0})
    var waited=popupColumn.itemAtIndex(2)
    check(at(waited)>at(bottom),"the waiting card joins below the stack")
    check(at(bottom)===bottomY,"the card below the pointer stays put")
    // The card below the pointer expires and the last waiting card shows: the
    // cards below the pointer move up, the hovered card does not.
    popupColumn.waiting=0
    popupColumn.groups=[row("6-2","Hovered"),row("6-4","Waited"),row("6-5","Last")]
    tryVerify(function(){var c=popupColumn.itemAtIndex(2);return popupColumn.count===3&&c&&c.summary==="Last"&&c.opacity===1&&c.entranceOffset===0&&at(waited)===bottomY},3000,"the cards below slide up and the last waiting card joins")
    tryVerify(function(){return note.height===0},3000,"the note leaves with the queue")
    check(window.pinnedMoves===0,"the hovered card moved on "+window.pinnedMoves+" frames")
    check(at(hovered)===hoveredY&&hovered.hovered,"the hovered card is still under the pointer")
    window.pinned=null
    mouseMove(window.contentItem,490,250)
    tryVerify(function(){return at(hovered)===0},3000,"leaving the stack closes the gap above")
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
""".replace("DELEGATE", delegate)
)
home = base / "home"
home.mkdir()
runtime = base / "runtime"
runtime.mkdir(mode=0o700)
desktop = "--desktop" in sys.argv
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_STATE_HOME=str(home / ".local/state"),
    QT_QPA_PLATFORM="wayland" if desktop else "offscreen",
    QT_QUICK_BACKEND="rhi" if desktop else "software",
    QT_QPA_PLATFORMTHEME="basic",
    QT_STYLE_OVERRIDE="Fusion",
)
if not desktop:
    env["XDG_RUNTIME_DIR"] = str(runtime)
    for key in ["DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"]:
        env.pop(key, None)
testhost.make(base, app, env, display=desktop)
status, output = testhost.run(
    ["dbus-run-session", "--", "quickshell", "-p", str(app), "--no-color"], env, base / "qs.log", 15
)
print(output)
print("Motion captures:", app)
hostenv.check(env, output)
assert status == 0 and "MOTION_RESULT 9 passed 0 failed" in output
