import QtQuick
import QtTest
import "../components"
Item {
  width: 500; height: 400
  NotificationCard {
    id: card
    x: 20; y: 20; width: 420
    app: "Test"; summary: "Test title"; body: "Test message"
    actions: [{identifier:"read",text:"Mark read"}]
  }
  SignalSpy { id: opened; target: card; signalName: "cardClicked" }
  SignalSpy { id: dismissed; target: card; signalName: "closeRequested" }
  SignalSpy { id: action; target: card; signalName: "actionRequested" }
  TestCase {
    name: "FoamyNotificationCard"
    onCompletedChanged: if (completed) console.log("UI_RESULT",qtest_results.passCount,"passed",qtest_results.failCount,"failed")
    when: windowShown
    function init() { card.showTimeoutIndicator=true;card.timeoutMs=0;card.remainingMs=0;card.compact=true;card.app="Test";card.showImages=true;card.imageSize=56;card.width=420;card.summary="Test title";card.busy=false;card.image="";card.appIcon="";card.favicon="";card.actionsExpanded=false;card.actions=[{identifier:"read",text:"Mark read"}];opened.clear();dismissed.clear();action.clear() }
    function cleanup() { if (qtest_results.failed) console.log("FAILED_CASE", qtest_results.functionName) }
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
      card.width=280;card.summary="Process crashed: xdg-desktop-portal-hyprland"
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
    function test_right_click_dismisses() { mouseClick(card,100,52,Qt.RightButton);compare(dismissed.count,1);compare(opened.count,0) }
    function test_close_button_only_dismisses() { var button=findChild(card,"dismissButton");verify(button);mouseClick(button);compare(dismissed.count,1);compare(opened.count,0) }
    function test_app_action_only_invokes_action() { var button=findChild(card,"notificationAction");verify(button);mouseClick(button);compare(action.count,1);compare(action.signalArguments[0][0],"read");compare(opened.count,0) }
    function test_settings_and_overflow_remain_reachable() {
      card.actions=[{identifier:"settings",text:"Innstillinger"},{identifier:"reply",text:"Reply"},{identifier:"read",text:"Mark read"},{identifier:"snooze",text:"Snooze"}]
      waitForRendering(card);compare(card.primaryActions.length,2);compare(card.overflowActions.length,2)
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
