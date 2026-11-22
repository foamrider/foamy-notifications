import QtQuick
import QtTest
import "../components"
import "../NotificationLogic.js" as Logic

Item {
  width: 500
  height: 400
  NotificationCard {
    id: card
    x: 20
    y: 20
    width: 420
    app: "Test"
    summary: "Test title"
    body: "Test message"
    actions: [
      {
        identifier: "read",
        text: "Mark read"
      }
    ]
    // As the service passes them: buttons in draw/number order from actions and offers.
    deeds: Logic.deeds({
      actionsJson: JSON.stringify(actions || []),
      execActions: ""
    }, offers || {})
  }
  SignalSpy {
    id: opened
    target: card
    signalName: "cardClicked"
  }
  SignalSpy {
    id: dismissed
    target: card
    signalName: "closeRequested"
  }
  SignalSpy {
    id: hidden
    target: card
    signalName: "hideRequested"
  }
  SignalSpy {
    id: action
    target: card
    signalName: "actionRequested"
  }
  SignalSpy {
    id: feature
    target: card
    signalName: "featureRequested"
  }
  TestCase {
    name: "FoamyNotificationCard"
    onCompletedChanged: if (completed)
      console.log("UI_RESULT", qtest_results.passCount, "passed", qtest_results.failCount, "failed")
    when: windowShown
    function init() {
      card.offers = {
        codes: "",
        link: "",
        meeting: false
      }
      card.snoozeAvailable = false
      card.snoozeExpanded = false
      feature.clear()
      card.showTimeoutIndicator = true
      card.timeoutMs = 0
      card.remainingMs = 0
      card.compact = true
      card.app = "Test"
      card.desktopEntry = ""
      card.showImages = true
      card.imageSize = 56
      card.width = 420
      card.summary = "Test title"
      card.body = "Test message"
      card.busy = false
      card.image = ""
      card.appIcon = ""
      card.favicon = ""
      card.actionsExpanded = false
      card.actions = [
        {
          identifier: "read",
          text: "Mark read"
        }
      ]
      opened.clear()
      dismissed.clear()
      action.clear()
    }
    function cleanup() {
      card.error = ""
      card.copiedCode = ""
      if (qtest_results.failed)
        console.log("FAILED_CASE", qtest_results.functionName)
    }
    function test_snooze_hover_slot_does_not_shift_time_and_supports_keyboard() {
      card.snoozeAvailable = true
      card.timestamp = 1790930400000
      card.forceActiveFocus()
      mouseMove(card, -10, -10)
      waitForRendering(card)
      var snooze = findChild(card, "notificationSnooze"), time = findChild(card, "notificationTime")
      compare(snooze.opacity, 0)
      var position = time.mapToItem(card, 0, 0), height = card.height
      verify(snooze.mapToItem(card, snooze.width, 0).x < position.x)
      mouseMove(card, 100, 52)
      tryCompare(snooze, "opacity", 1)
      compare(time.mapToItem(card, 0, 0), position)
      compare(card.height, height)
      mouseMove(card, -10, -10)
      tryCompare(snooze, "opacity", 0)
      snooze.forceActiveFocus(Qt.TabFocusReason)
      compare(snooze.opacity, 1)
      keyClick(Qt.Key_Space)
      verify(card.snoozeExpanded)
      keyClick(Qt.Key_Escape)
      verify(!card.snoozeExpanded)
      card.forceActiveFocus()
      compare(snooze.opacity, 0)
      compare(opened.count, 0)
      compare(feature.count, 0)
      card.timestamp = 0
    }
    function test_copy_feedback_keeps_focus_and_button_size() {
      card.actions = []
      card.width = 280
      card.offers = {
        codes: "482913",
        link: "https://example.com",
        meeting: false
      }
      waitForRendering(card)
      var footer = findChild(card, "notificationFooter"), copy = findChild(card, "notificationCopyCode"), link = findChild(card, "notificationOpenLink")
      compare(copy.width, link.width)
      compare(copy.y, link.y)
      verify(footer.mapToItem(card, footer.width, footer.height).x <= card.width)
      verify(footer.mapToItem(card, 0, footer.height).y <= card.height)
      var height = copy.height
      copy.forceActiveFocus()
      keyClick(Qt.Key_Space)
      compare(feature.count, 1)
      compare(feature.signalArguments[0][1], "482913")
      card.copiedCode = "482913"
      waitForRendering(card)
      compare(findChild(card, "notificationCopyCode"), copy)
      verify(copy.activeFocus)
      compare(copy.text, "Copied 482913")
      compare(copy.glyph, "\u{f012c}")
      // The feedback does not resize the button under the pointer.
      compare(copy.height, height)
      compare(opened.count, 0)
      var before = hidden.count
      mouseClick(link, link.width / 2, link.height / 2, Qt.RightButton)
      compare(hidden.count, before + 1)
      compare(feature.count, 1)
    }
    function test_footer_wraps_without_losing_actions() {
      card.width = 280
      card.actions = [
        {
          identifier: "reply",
          text: "Reply"
        },
        {
          identifier: "read",
          text: "Mark read"
        }
      ]
      card.offers = {
        codes: "482913 731208",
        link: "https://example.com",
        meeting: false
      }
      waitForRendering(card)
      var footer = findChild(card, "notificationFooter")
      var buttons = Array.from(footer.children).filter(function (child) {
        return child.entry !== undefined
      })
      compare(buttons.length, 5)
      compare(buttons[4].width, footer.width)
      for (var button of buttons) {
        verify(button.width >= footer.width / 2)
        verify(button.mapToItem(card, button.width, button.height).x <= card.width)
        verify(button.mapToItem(card, button.width, button.height).y <= card.height)
        mouseClick(button)
      }
      compare(action.count, 2)
      compare(feature.count, 3)
      compare(opened.count, 0)
      compare(feature.signalArguments[0][1], "482913")
      compare(feature.signalArguments[1][1], "731208")
      compare(feature.signalArguments[2][0], "link")
    }
    function test_footer_busy_error_and_empty_state() {
      card.actions = []
      card.offers = {
        codes: "482913",
        link: "",
        meeting: false
      }
      waitForRendering(card)
      var button = findChild(card, "notificationCopyCode"), footer = findChild(card, "notificationFooter")
      card.busy = true
      mouseClick(button)
      compare(feature.count, 0)
      compare(opened.count, 0)
      card.busy = false
      waitForRendering(card)
      var before = card.height
      card.error = "Clipboard is busy. Try again."
      waitForRendering(card)
      // The error line shows, and the button still works.
      verify(card.height > before)
      mouseClick(button)
      compare(feature.count, 1)
      verify(footer.visible)
      card.error = ""
      waitForRendering(card)
      compare(card.height, before)
      card.offers = {}
      waitForRendering(card)
      verify(!footer.visible)
      verify(card.height < before)
    }
    function test_snooze_controls_keep_right_click_and_escape_behavior() {
      card.snoozeAvailable = true
      mouseClick(findChild(card, "notificationSnooze"))
      verify(card.snoozeExpanded)
      verify(card.hovered)
      waitForRendering(card)
      mouseClick(findChild(card, "notificationSnooze30"))
      compare(feature.count, 1)
      compare(feature.signalArguments[0][1], "30")
      verify(!card.snoozeExpanded)
      mouseClick(findChild(card, "notificationSnooze"))
      card.forceActiveFocus()
      keyClick(Qt.Key_Escape)
      verify(!card.snoozeExpanded)
      var before = hidden.count
      mouseClick(card, 100, 50, Qt.RightButton)
      compare(hidden.count, before + 1)
      compare(feature.count, 1)
    }
    function test_avatar_and_app_icon_use_separate_areas() {
      card.app = "Vivaldi"
      card.appIcon = Qt.resolvedUrl("fallback.svg").toString()
      card.image = Qt.resolvedUrl("avatar.svg").toString()
      var image = findChild(card, "notificationImage"), icon = findChild(card, "notificationAppIcon")
      tryCompare(image, "status", Image.Ready)
      tryCompare(icon, "status", Image.Ready)
      verify(image.visible)
      verify(icon.visible)
      var header = findChild(card, "notificationHeader")
      verify(header.height <= 32)
      verify(image.mapToItem(card, 0, 0).y >= header.height)
    }
    function test_favicon_has_priority_with_app_icon_fallback() {
      card.appIcon = Qt.resolvedUrl("fallback.svg").toString()
      card.favicon = Qt.resolvedUrl("avatar.svg").toString()
      var icon = findChild(card, "notificationAppIcon"), favicon = findChild(card, "notificationFavicon")
      tryCompare(icon, "status", Image.Ready)
      tryCompare(favicon, "status", Image.Ready)
      verify(favicon.visible)
      verify(!icon.visible)
      card.favicon = Qt.resolvedUrl("missing-favicon.png").toString()
      tryCompare(favicon, "status", Image.Error)
      verify(icon.visible)
      card.favicon = "https://example.invalid/favicon.png"
      compare(card.faviconSource, "")
      verify(icon.visible)
      card.favicon = ""
      verify(icon.visible)
    }
    // Chromium-family browsers put the site's origin on the first body line;
    // the card drops it only for them, never for an app whose name or icon
    // path merely contains a browser name.
    function test_leading_origin_is_dropped_only_for_a_browser_data() {
      return [
        {
          tag: "chromium browser",
          app: "Vivaldi",
          appIcon: "",
          body: "living-room.local is ready",
          shown: "is ready"
        },
        {
          tag: "chromium browser link",
          app: "Google Chrome",
          appIcon: "",
          body: "<a href=\"https://living-room.local/\">living-room.local</a> is ready",
          shown: "is ready"
        },
        {
          tag: "browser name inside an app name",
          app: "Cooperate",
          appIcon: "",
          body: "living-room.local is ready",
          shown: "living-room.local is ready"
        },
        {
          tag: "browser name inside an icon path",
          app: "Notes",
          appIcon: Qt.resolvedUrl("operator/icon.svg").toString(),
          body: "living-room.local is ready",
          shown: "living-room.local is ready"
        },
        {
          tag: "browser name inside an icon name",
          app: "Notes",
          appIcon: "chromatic",
          body: "living-room.local is ready",
          shown: "living-room.local is ready"
        },
        {
          tag: "browser known by its desktop entry",
          app: "Microsoft Edge",
          appIcon: "",
          desktopEntry: "microsoft-edge",
          body: "living-room.local is ready",
          shown: "is ready"
        }
      ]
    }
    function test_leading_origin_is_dropped_only_for_a_browser(data) {
      card.app = data.app
      card.appIcon = data.appIcon
      card.desktopEntry = data.desktopEntry || ""
      card.body = data.body
      compare(findChild(card, "notificationBody").text, data.shown)
    }
    function test_timeout_indicator_is_bounded_optional_and_does_not_resize() {
      waitForRendering(card)
      var indicator = findChild(card, "notificationTimeoutIndicator"), height = card.height
      verify(!indicator.visible)
      card.timeoutMs = 8000
      card.remainingMs = 4000
      waitForRendering(card)
      verify(indicator.visible)
      compare(indicator.progress, 0.5)
      compare(card.height, height)
      card.remainingMs = 9000
      compare(indicator.progress, 1)
      verify(indicator.x > card.radius)
      verify(indicator.x + indicator.width < card.width - card.radius)
      compare(indicator.height, 2)
      card.width = 280
      waitForRendering(card)
      verify(indicator.x + indicator.width < card.width - card.radius)
      card.remainingMs = -100
      verify(!indicator.visible)
      compare(indicator.progress, 0)
      card.remainingMs = 4000
      card.showTimeoutIndicator = false
      verify(!indicator.visible)
      card.showTimeoutIndicator = true
      card.timeoutMs = 0
      verify(!indicator.visible)
    }
    function test_density_switch_preserves_content_and_actions() {
      waitForRendering(card)
      var header = findChild(card, "notificationHeader"), title = findChild(card, "notificationTitle")
      var compactHeight = card.height, compactHeader = header.height, titleSize = title.font.pixelSize
      card.compact = false
      waitForRendering(card)
      verify(header.height > compactHeader)
      verify(card.height > compactHeight)
      compare(title.font.pixelSize, titleSize)
      compare(title.text, "Test title")
      mouseClick(findChild(card, "notificationAction"))
      compare(action.count, 1)
      card.compact = true
      waitForRendering(card)
      compare(card.height, compactHeight)
    }
    function test_remote_image_does_not_load_network_content() {
      // Sender-supplied image hints: only a local file may load.
      for (const source of ["https://example.invalid/avatar.png", "image://qsimage/1", "relative/avatar.png", "data:image/png;base64,AAAA"]) {
        card.image = source
        compare(card.contentImageSource, "", source)
      }
      card.favicon = "image://qsimage/1"
      compare(card.faviconSource, "")
      card.image = "/tmp/avatar.png"
      verify(card.contentImageSource.indexOf("file://") === 0)
    }
    function test_sender_action_label_tooltip_is_plain_text() {
      // A relayed (e.g. KDE Connect) action label is untrusted: as AutoText the
      // tooltip would draw the tag as rich text and fetch the image.
      var label = 'Open <img src="http://127.0.0.1:9/beacon.png" width="300" height="300"> and read the rest of this label'
      card.width = 280
      card.actions = [
        {
          identifier: "open",
          text: label
        }
      ]
      waitForRendering(card)
      var button = findChild(card, "notificationAction"), tip = findChild(button, "notificationFooterToolTip")
      verify(tip)
      compare(findChild(button, "notificationFooterLabel").children[1].textFormat, Text.PlainText)
      mouseMove(button, button.width / 2, button.height / 2)
      tryCompare(tip, "visible", true)
      compare(tip.text, label)
      compare(tip.contentItem.text, label)
      compare(tip.contentItem.textFormat, Text.PlainText)
      verify(tip.width <= card.width)
      mouseMove(card, -10, -10)
      tryCompare(tip, "visible", false)
    }
    function test_full_width_title_wraps_without_hover_reflow() {
      // Long enough to wrap in the foamy body font (narrower than the bar's mono font).
      card.width = 280
      card.summary = "Process crashed: xdg-desktop-portal-hyprland (signal 11, core dumped)"
      waitForRendering(card)
      var title = findChild(card, "notificationTitle"), appName = findChild(card, "notificationAppName")
      compare(appName.text, "Test")
      compare(title.lineCount, 2)
      verify(title.width > card.width * 0.88)
      var height = card.height
      mouseMove(card, 100, 60)
      waitForRendering(card)
      compare(card.height, height)
      card.summary = "  "
      compare(title.visible, false)
    }
    function test_shared_image_is_bounded_and_clicks_default_action() {
      card.image = Qt.resolvedUrl("preview.svg").toString()
      card.appIcon = Qt.resolvedUrl("fallback.svg").toString()
      var image = findChild(card, "notificationImage"), tile = findChild(card, "notificationImageTile"), title = findChild(card, "notificationTitle")
      tryCompare(image, "status", Image.Ready)
      waitForRendering(card)
      verify(tile.visible)
      compare(tile.width, 56)
      compare(tile.height, 56)
      verify(title.mapToItem(card, 0, 0).x > tile.mapToItem(card, 0, 0).x + tile.width)
      compare(image.fillMode, Image.PreserveAspectFit)
      mouseClick(image)
      compare(opened.count, 1)
      compare(action.count, 0)
      card.imageSize = 72
      waitForRendering(card)
      compare(tile.width, 72)
      card.showImages = false
      waitForRendering(card)
      verify(!tile.visible)
      verify(title.width > card.width * 0.88)
      card.showImages = true
      card.image = Qt.resolvedUrl("missing-preview.png").toString()
      tryCompare(image, "status", Image.Error)
      waitForRendering(card)
      verify(!tile.visible)
      verify(title.width > card.width * 0.88)
    }
    function test_left_click_activates() {
      mouseClick(card, 100, 52, Qt.LeftButton)
      compare(opened.count, 1)
      compare(dismissed.count, 0)
    }
    function test_right_click_dismisses() {
      hidden.clear()
      mouseClick(card, 100, 52, Qt.RightButton)
      compare(hidden.count, 1)
      compare(dismissed.count, 0)
      compare(opened.count, 0)
    }
    function test_close_button_only_dismisses() {
      hidden.clear()
      var button = findChild(card, "dismissButton")
      verify(button)
      mouseClick(button)
      compare(dismissed.count, 1)
      compare(hidden.count, 0)
      compare(opened.count, 0)
    }
    function test_app_action_only_invokes_action() {
      var button = findChild(card, "notificationAction")
      verify(button)
      mouseClick(button)
      compare(action.count, 1)
      compare(action.signalArguments[0][0], "read")
      compare(opened.count, 0)
    }
    function test_settings_and_overflow_remain_reachable() {
      card.actions = [
        {
          identifier: "settings",
          text: "Innstillinger"
        },
        {
          identifier: "reply",
          text: "Reply"
        },
        {
          identifier: "read",
          text: "Mark read"
        },
        {
          identifier: "snooze",
          text: "Snooze"
        }
      ]
      waitForRendering(card)
      // Footer: two sender actions; Settings and the rest behind "More actions" (deeds order).
      compare(card.footerActions.filter(function (e) {
        return e.kind === "action"
      }).length, 2)
      compare(card.overflowActions.length, 2)
      var more = findChild(card, "moreActionsButton")
      verify(more.visible)
      mouseClick(more)
      compare(card.actionsExpanded, true)
      compare(opened.count, 0)
      mouseMove(card, -10, -10)
      compare(card.hovered, true)
      waitForRendering(card)
      var button = findChild(card, "overflowAction-settings")
      verify(button)
      mouseClick(button)
      compare(action.count, 1)
      compare(action.signalArguments[0][0], "settings")
      compare(opened.count, 0)
      compare(card.actionsExpanded, false)
    }
    function test_sender_closure_clears_expanded_actions() {
      card.actions = [
        {
          identifier: "settings",
          text: "Settings"
        }
      ]
      card.actionsExpanded = true
      card.actions = []
      compare(card.actionsExpanded, false)
      compare(findChild(card, "moreActionsButton").visible, false)
    }
    function test_busy_click_does_not_duplicate_action() {
      card.busy = true
      mouseClick(card, 100, 52, Qt.LeftButton)
      compare(opened.count, 0)
    }
  }
}
