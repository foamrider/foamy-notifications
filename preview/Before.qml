import QtQuick
import qs.Commons

import "../components"
import "../NotificationLogic.js" as Logic

// The dev (before) toasts for render.py --trio: same mock states, dev card API.
// Foamy popups are layer-shell windows, not a bar popup: `popup` is the card
// stack, placed at the top-right under the bar as the service places it.
Item {
  id: preview
  required property QtObject bar
  required property Item anchorItem
  property var previewStates: ["default", "actions", "keyboard"]
  property string previewState: "default"
  property int settleMs: 700
  readonly property Item popup: stack

  readonly property double now: 1793350800000
  readonly property string recording: JSON.stringify([{label: "Open", argv: ["mpv", "/tmp/screenrecording.mp4"]}, {label: "Omacut", argv: ["omacut", "/tmp/screenrecording.mp4"]}])
  readonly property var cards: [
    { key: "1-1", app: "Signal", appIcon: "signal-desktop", summary: "Ada Lovelace", body: "Are we still on for the <b>review</b> at 3? &amp;amp; bring <font color=\"#000\">notes</font>",
      urgency: 1, actionsJson: JSON.stringify([{identifier: "reply", text: "Reply"}, {identifier: "read", text: "Mark read"}]), execActions: "", offers: {codes: "", link: ""} },
    { key: "2-2", app: "Screen recording", appIcon: "", glyph: "\u{f0a1c}", summary: "Recording saved", body: "screenrecording-2026-10-30.mp4",
      urgency: 1, actionsJson: "[]", execActions: recording, offers: {codes: "", link: ""} },
    { key: "3-3", app: "Bank", appIcon: "", summary: "Sign-in code", body: "Your code is 482913. It expires in 5 minutes.",
      urgency: 2, actionsJson: "[]", execActions: "", offers: {codes: "482913", link: ""} }
  ]
  readonly property var shown: previewState === "default" ? [cards[0], cards[2]] : previewState === "actions" ? [cards[1], cards[0]] : [cards[1], cards[0], cards[2]]

  Column {
    id: stack
    readonly property Item cardItem: stack
    x: 1920 - width - Style.gapsOut - Style.space(8)
    y: preview.bar.barSize + Style.gapsOut + Style.space(8)
    width: Style.space(390)
    spacing: Style.space(8)
    Repeater {
      model: preview.shown
      NotificationCard {
        required property var modelData
        required property int index
        width: stack.width
        app: modelData.app
        appIcon: modelData.appIcon
        glyph: modelData.glyph || ""
        summary: modelData.summary
        body: modelData.body
        urgency: modelData.urgency
        timestamp: preview.now
        timeoutMs: 8000
        remainingMs: 5200
        compact: true
        snoozeAvailable: true
        actions: JSON.parse(modelData.actionsJson)
        offers: modelData.offers
      }
    }
  }
}
