import QtQuick
import QtTest
import "../components"
import "../NotificationLogic.js" as Logic
import "../kit"

// ui.py runs this file in the kit's light preview theme, whose green reads
// 2.55:1 on a hovered footer button.
Item {
  width: 500
  height: 400
  NotificationCard {
    id: card
    x: 20
    y: 20
    width: 280
    app: "Test"
    summary: "Test title"
    body: "Test message"
    actions: []
    offers: ({
        codes: "482913",
        link: "",
        meeting: false
      })
    deeds: Logic.deeds({
      actionsJson: "[]",
      execActions: ""
    }, offers || {})
  }
  TestCase {
    name: "FoamyNotificationCardLight"
    onCompletedChanged: if (completed)
      console.log("UI_RESULT", qtest_results.passCount, "passed", qtest_results.failCount, "failed")
    when: windowShown
    // The copied feedback (its glyph and label) reads at 4.5:1 on the
    // footer button's fill, hovered and not.
    function test_copied_feedback_reads_on_the_button() {
      card.copiedCode = "482913"
      waitForRendering(card)
      var copy = findChild(card, "notificationCopyCode"), row = findChild(copy, "notificationFooterLabel")
      var glyph = row.children[0], label = row.children[1]
      compare(label.text, "Copied 482913")
      var states = [[card, -10, -10, false], [copy, copy.width / 2, copy.height / 2, true]]
      states.forEach(function (state) {
        mouseMove(state[0], state[1], state[2])
        tryCompare(copy, "hovered", state[3])
        var fill = copy.background.color
        verify(FoamyPalette.contrastRatio(FoamyPalette.success, fill) < 4.5, "the theme's green already reads on " + fill + "; the check proves nothing")
        verify(FoamyPalette.contrastRatio(label.color, fill) >= 4.5, "copied label " + label.color + " on " + fill)
        verify(FoamyPalette.contrastRatio(glyph.color, fill) >= 4.5, "copied glyph " + glyph.color + " on " + fill)
      })
    }
  }
}
