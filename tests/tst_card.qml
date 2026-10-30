import QtQuick
import QtTest
import "../components"
import "../NotificationLogic.js" as Logic
Item {
  width: 500; height: 400
  NotificationCard {
    id: card
    x: 20; y: 20; width: 420
    app: "Test"; summary: "Test title"; body: "Test message"
    actions: [{identifier:"read",text:"Mark read"}]
    // As the service passes them: buttons in draw/number order from actions and offers.
    deeds: Logic.deeds({actionsJson: JSON.stringify(actions || []), execActions: ""}, offers || {})
  }
  SignalSpy { id: opened; target: card; signalName: "cardClicked" }
  SignalSpy { id: dismissed; target: card; signalName: "closeRequested" }
  SignalSpy { id: hidden; target: card; signalName: "hideRequested" }
  SignalSpy { id: action; target: card; signalName: "actionRequested" }
  SignalSpy { id: feature; target: card; signalName: "featureRequested" }
  TestCase {
    name: "FoamyNotificationCard"
    onCompletedChanged: if (completed) console.log("UI_RESULT",qtest_results.passCount,"passed",qtest_results.failCount,"failed")
    when: windowShown
    function init() { card.offers={codes:"",link:"",meeting:false};card.snoozeAvailable=false;card.snoozeExpanded=false;feature.clear(); card.showTimeoutIndicator=true;card.timeoutMs=0;card.remainingMs=0;card.compact=true;card.app="Test";card.showImages=true;card.imageSize=56;card.width=420;card.summary="Test title";card.busy=false;card.image="";card.appIcon="";card.favicon="";card.actionsExpanded=false;card.actions=[{identifier:"read",text:"Mark read"}];opened.clear();dismissed.clear();action.clear() }
    function cleanup() { card.error="";card.copiedCode="";if (qtest_results.failed) console.log("FAILED_CASE", qtest_results.functionName) }
    function test_snooze_hover_slot_does_not_shift_time_and_supports_keyboard() {
      card.snoozeAvailable=true;card.timestamp=1790930400000
      card.forceActiveFocus();mouseMove(card,-10,-10);waitForRendering(card)
      var snooze=findChild(card,"notificationSnooze"),time=findChild(card,"notificationTime")
      compare(snooze.opacity,0)
      var position=time.mapToItem(card,0,0),height=card.height
      verify(snooze.mapToItem(card,snooze.width,0).x<position.x)
      mouseMove(card,100,52);tryCompare(snooze,"opacity",1)
      compare(time.mapToItem(card,0,0),position);compare(card.height,height)
      mouseMove(card,-10,-10);tryCompare(snooze,"opacity",0)
      snooze.forceActiveFocus(Qt.TabFocusReason);compare(snooze.opacity,1)
      keyClick(Qt.Key_Space);verify(card.snoozeExpanded)
      keyClick(Qt.Key_Escape);verify(!card.snoozeExpanded)
      card.forceActiveFocus();compare(snooze.opacity,0)
      compare(opened.count,0);compare(feature.count,0)
      card.timestamp=0
    }
    function test_footer_centers_actions_and_preserves_focus_after_copy() {
      card.actions=[];card.width=280
      card.offers={codes:"482913",link:"https://example.com",meeting:false}
      waitForRendering(card)
      var footer=findChild(card,"notificationFooter"), copy=findChild(card,"notificationCopyCode"), link=findChild(card,"notificationOpenLink")
      compare(footer.width,card.width-2);compare(copy.width,link.width)
      compare(copy.height,34);compare(link.height,34)
      compare(copy.y,link.y);compare(link.glyph,"\u{f03cc}")
      verify(Math.abs(footer.mapToItem(card,0,footer.height).y-(card.height-1))<1)
      for (var button of [copy,link]) {
        var label=findChild(button,"notificationFooterLabel")
        verify(Math.abs(label.mapToItem(button,label.width/2,label.height/2).x-button.width/2)<1)
        verify(Math.abs(label.mapToItem(button,label.width/2,label.height/2).y-button.height/2)<1)
      }
      copy.forceActiveFocus();keyClick(Qt.Key_Space)
      compare(feature.count,1);compare(feature.signalArguments[0][1],"482913")
      card.copiedCode="482913";waitForRendering(card)
      compare(findChild(card,"notificationCopyCode"),copy);verify(copy.activeFocus)
      compare(copy.text,"Copied 482913");compare(copy.glyph,"\u{f012c}");compare(copy.height,34)
      compare(opened.count,0)
      var before=hidden.count
      mouseClick(link,link.width/2,link.height/2,Qt.RightButton)
      compare(hidden.count,before+1);compare(feature.count,1)
    }
    function test_footer_wraps_without_losing_actions() {
      card.width=280
      card.actions=[{identifier:"reply",text:"Reply"},{identifier:"read",text:"Mark read"}]
      card.offers={codes:"482913 731208",link:"https://example.com",meeting:false}
      waitForRendering(card)
      var footer=findChild(card,"notificationFooter")
      var buttons=Array.from(footer.children).filter(function(child) { return child.entry !== undefined })
      compare(buttons.length,5)
      compare(buttons[4].width,footer.width)
      for (var button of buttons) {
        verify(button.width>=footer.width/2)
        verify(button.mapToItem(card,button.width,button.height).x<=card.width)
        verify(button.mapToItem(card,button.width,button.height).y<=card.height)
        mouseClick(button)
      }
      compare(action.count,2);compare(feature.count,3);compare(opened.count,0)
      compare(feature.signalArguments[0][1],"482913");compare(feature.signalArguments[1][1],"731208")
      compare(feature.signalArguments[2][0],"link")
    }
    function test_footer_busy_error_and_empty_state() {
      card.actions=[];card.offers={codes:"482913",link:"",meeting:false}
      waitForRendering(card)
      var button=findChild(card,"notificationCopyCode"),footer=findChild(card,"notificationFooter")
      card.busy=true;mouseClick(button);compare(feature.count,0);compare(opened.count,0)
      card.busy=false;card.error="Clipboard is busy. Try again.";waitForRendering(card)
      mouseClick(button);compare(feature.count,1);verify(footer.visible)
      card.error="";waitForRendering(card)
      var height=card.height
      card.offers={};waitForRendering(card)
      verify(!footer.visible);compare(card.height,height-34)
    }
    function test_feature_buttons_do_not_trigger_default_actions() {
      card.offers={codes:"482913",link:"https://meet.google.com/abc",meeting:true}
      card.width=280
      waitForRendering(card)
      var copy=findChild(card,"notificationCopyCode"), link=findChild(card,"notificationOpenLink")
      verify(copy.visible);verify(link.visible)
      mouseClick(copy);compare(feature.count,1);compare(feature.signalArguments[0][0],"code");compare(feature.signalArguments[0][1],"482913")
      link.forceActiveFocus();keyClick(Qt.Key_Space)
      compare(feature.count,2);compare(feature.signalArguments[1][0],"link")
      compare(opened.count,0);compare(action.count,0)
      verify(copy.mapToItem(card,copy.width,0).x<=card.width)
      verify(link.mapToItem(card,link.width,0).x<=card.width)
    }
    function test_snooze_controls_keep_right_click_and_escape_behavior() {
      card.snoozeAvailable=true
      mouseClick(findChild(card,"notificationSnooze"));verify(card.snoozeExpanded);verify(card.hovered)
      waitForRendering(card)
      mouseClick(findChild(card,"notificationSnooze30"));compare(feature.count,1);compare(feature.signalArguments[0][1],"30");verify(!card.snoozeExpanded)
      mouseClick(findChild(card,"notificationSnooze"));card.forceActiveFocus();keyClick(Qt.Key_Escape);verify(!card.snoozeExpanded)
      var before=hidden.count;mouseClick(card,100,50,Qt.RightButton);compare(hidden.count,before+1);compare(feature.count,1)
    }
    function test_avatar_and_app_icon_use_separate_areas() {
      card.app="Vivaldi"
      card.appIcon=Qt.resolvedUrl("fallback.svg").toString();card.image=Qt.resolvedUrl("avatar.svg").toString()
      var image=findChild(card,"notificationImage"), icon=findChild(card,"notificationAppIcon")
      tryCompare(image,"status",Image.Ready);tryCompare(icon,"status",Image.Ready)
      verify(image.visible);verify(icon.visible)
      var header=findChild(card,"notificationHeader")
      verify(header.height<=32);verify(image.mapToItem(card,0,0).y>=header.height)
    }
    function test_favicon_has_priority_with_app_icon_fallback() {
      card.appIcon=Qt.resolvedUrl("fallback.svg").toString()
      card.favicon=Qt.resolvedUrl("avatar.svg").toString()
      var icon=findChild(card,"notificationAppIcon"), favicon=findChild(card,"notificationFavicon")
      tryCompare(icon,"status",Image.Ready);tryCompare(favicon,"status",Image.Ready)
      verify(favicon.visible);verify(!icon.visible)
      card.favicon=Qt.resolvedUrl("missing-favicon.png").toString()
      tryCompare(favicon,"status",Image.Error);verify(icon.visible)
      card.favicon="https://example.invalid/favicon.png";compare(card.faviconSource,"");verify(icon.visible)
      card.favicon="";verify(icon.visible)
    }
    function test_timeout_indicator_is_bounded_optional_and_does_not_resize() {
      waitForRendering(card)
      var indicator=findChild(card,"notificationTimeoutIndicator"), height=card.height
      verify(!indicator.visible)
      card.timeoutMs=8000;card.remainingMs=4000;waitForRendering(card)
      verify(indicator.visible);compare(indicator.progress,0.5);compare(card.height,height)
      card.remainingMs=9000;compare(indicator.progress,1)
      verify(indicator.x>card.radius)
      verify(indicator.x+indicator.width<card.width-card.radius)
      compare(indicator.height,2)
      card.width=280;waitForRendering(card)
      verify(indicator.x+indicator.width<card.width-card.radius)
      card.remainingMs=-100;verify(!indicator.visible);compare(indicator.progress,0)
      card.remainingMs=4000;card.showTimeoutIndicator=false;verify(!indicator.visible)
      card.showTimeoutIndicator=true;card.timeoutMs=0;verify(!indicator.visible)
    }
    function test_density_switch_preserves_content_and_actions() {
      waitForRendering(card)
      var header=findChild(card,"notificationHeader"), title=findChild(card,"notificationTitle")
      var compactHeight=card.height, titleSize=title.font.pixelSize
      compare(header.height,32)
      card.compact=false;waitForRendering(card)
      compare(header.height,44);verify(card.height>compactHeight)
      compare(title.font.pixelSize,titleSize);compare(title.text,"Test title")
      mouseClick(findChild(card,"notificationAction"));compare(action.count,1)
      card.compact=true;waitForRendering(card);compare(card.height,compactHeight)
    }
    function test_remote_image_does_not_load_network_content() {
      card.image="https://example.invalid/avatar.png"
      compare(card.contentImageSource,"")
    }
    function test_full_width_title_wraps_without_hover_reflow() {
      // Long enough to wrap in the foamy body font (narrower than the bar's mono font).
      card.width=280;card.summary="Process crashed: xdg-desktop-portal-hyprland (signal 11, core dumped)"
      waitForRendering(card)
      var title=findChild(card,"notificationTitle"), appName=findChild(card,"notificationAppName")
      compare(appName.text,"Test");compare(title.lineCount,2);verify(title.width>card.width*0.88)
      var height=card.height
      mouseMove(card,100,60);waitForRendering(card);compare(card.height,height)
      card.summary="  ";compare(title.visible,false)
    }
    function test_shared_image_is_bounded_and_clicks_default_action() {
      card.image=Qt.resolvedUrl("preview.svg").toString();card.appIcon=Qt.resolvedUrl("fallback.svg").toString()
      var image=findChild(card,"notificationImage"), tile=findChild(card,"notificationImageTile"), title=findChild(card,"notificationTitle")
      tryCompare(image,"status",Image.Ready);waitForRendering(card)
      verify(tile.visible);compare(tile.width,56);compare(tile.height,56)
      verify(title.mapToItem(card,0,0).x>tile.mapToItem(card,0,0).x+tile.width)
      compare(image.fillMode,Image.PreserveAspectFit)
      mouseClick(image);compare(opened.count,1);compare(action.count,0)
      card.imageSize=72;waitForRendering(card);compare(tile.width,72)
      card.showImages=false;waitForRendering(card);verify(!tile.visible);verify(title.width>card.width*0.88)
      card.showImages=true;card.image=Qt.resolvedUrl("missing-preview.png").toString()
      tryCompare(image,"status",Image.Error);waitForRendering(card);verify(!tile.visible);verify(title.width>card.width*0.88)
    }
    function test_image_slot_is_identical_for_browser_and_native_apps() {
      card.image=Qt.resolvedUrl("avatar.svg").toString()
      var image=findChild(card,"notificationImage"), tile=findChild(card,"notificationImageTile")
      tryCompare(image,"status",Image.Ready);waitForRendering(card)
      var before=tile.mapToItem(card,0,0), width=tile.width, height=card.height
      card.app="Vivaldi";waitForRendering(card)
      compare(tile.mapToItem(card,0,0),before);compare(tile.width,width);compare(card.height,height)
    }
    function test_left_click_activates() { mouseClick(card,100,52,Qt.LeftButton);compare(opened.count,1);compare(dismissed.count,0) }
    function test_right_click_dismisses() { hidden.clear();mouseClick(card,100,52,Qt.RightButton);compare(hidden.count,1);compare(dismissed.count,0);compare(opened.count,0) }
    function test_close_button_only_dismisses() { hidden.clear();var button=findChild(card,"dismissButton");verify(button);mouseClick(button);compare(dismissed.count,1);compare(hidden.count,0);compare(opened.count,0) }
    function test_app_action_only_invokes_action() { var button=findChild(card,"notificationAction");verify(button);mouseClick(button);compare(action.count,1);compare(action.signalArguments[0][0],"read");compare(opened.count,0) }
    function test_settings_and_overflow_remain_reachable() {
      card.actions=[{identifier:"settings",text:"Innstillinger"},{identifier:"reply",text:"Reply"},{identifier:"read",text:"Mark read"},{identifier:"snooze",text:"Snooze"}]
      waitForRendering(card)
      // Footer: two sender actions; Settings and the rest behind "More actions" (deeds order).
      compare(card.footerActions.filter(function(e) { return e.kind === "action" }).length,2);compare(card.overflowActions.length,2)
      var more=findChild(card,"moreActionsButton");verify(more.visible)
      mouseClick(more);compare(card.actionsExpanded,true);compare(opened.count,0)
      mouseMove(card,-10,-10);compare(card.hovered,true)
      waitForRendering(card);var button=findChild(card,"overflowAction-settings");verify(button);mouseClick(button)
      compare(action.count,1);compare(action.signalArguments[0][0],"settings");compare(opened.count,0);compare(card.actionsExpanded,false)
    }
    function test_sender_closure_clears_expanded_actions() {
      card.actions=[{identifier:"settings",text:"Settings"}]
      card.actionsExpanded=true;card.actions=[]
      compare(card.actionsExpanded,false);compare(findChild(card,"moreActionsButton").visible,false)
    }
    function test_hover_pauses_signal() { mouseMove(card,100,52);tryCompare(card,"hovered",true);mouseMove(card,-10,-10);tryCompare(card,"hovered",false) }
    function test_busy_click_does_not_duplicate_action() { card.busy=true;mouseClick(card,100,52,Qt.LeftButton);compare(opened.count,0) }
  }
}
