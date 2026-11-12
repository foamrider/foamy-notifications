import QtQuick
import qs.Commons
import qs.Commons as Commons
import "../kit"

ListView {
  id: root
  property var groups: []
  // Cards waiting in the overflow queue; a small "+N waiting" note under the stack.
  property int waiting: 0
  // Once the final card leaves, do not keep an invisible input region under the pointer.
  readonly property bool holdPositions: groups.length > 0 && pointer.hovered
  // The pointer's height in the window, or -1 when it isn't over the stack.
  // The handler sits on the content item, whose coordinates shift when a card
  // leaves at the top, and a resting pointer sends no new position; the
  // window position of a resting pointer stays right.
  readonly property real pointerY: holdPositions ? pointer.point.scenePosition.y : -1
  implicitHeight: Math.max(0, contentHeight)
  // A nonzero initial viewport lets ListView measure its first delegate.
  height: Math.max(1, implicitHeight)
  interactive: false
  currentIndex: -1
  model: entries
  HoverHandler {
    id: pointer
  }
  ListModel {
    id: entries
  }
  // Under the last card, so it never moves a card; the card colours of the theme.
  footer: Item {
    width: root.width
    height: root.waiting > 0 && root.count > 0 ? waitingNote.height + root.spacing : 0
    visible: height > 0
    Rectangle {
      id: waitingNote
      objectName: "waitingNote"
      readonly property color surface: Commons.Color.notifications.background
      readonly property color ink: Commons.Color.notifications.text
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      width: waitingText.implicitWidth + Style.space(16)
      height: waitingText.implicitHeight + Style.space(6)
      radius: FoamyStyle.radius
      color: surface
      border.width: 1
      border.color: Util.alpha(ink, 0.15)
      Text {
        id: waitingText
        anchors.centerIn: parent
        text: FoamyI18n.t("+%1 waiting", root.waiting)
        color: Util.alpha(waitingNote.ink, 0.65)
        font.family: FoamyStyle.fontFamily
        font.pixelSize: FoamyStyle.font.bodySmall
      }
    }
  }
  // Cards below a removed card slide up into its place.
  removeDisplaced: Transition {
    NumberAnimation {
      properties: "y"
      duration: FoamyMotion.slider
      easing.type: Easing.OutCubic
    }
  }
  // Removing the top card moves the view's origin instead of the cards, which
  // removeDisplaced never sees: the whole stack jumps up. Slide it there too.
  // With animations off the duration is 0 and the stack moves at once.
  property real slide: 0
  property real settledContentY: 0
  Translate {
    id: slideShift
    y: root.slide
  }
  NumberAnimation {
    id: slideBack
    target: root
    property: "slide"
    to: 0
    duration: FoamyMotion.slider
    easing.type: Easing.OutCubic
  }
  onContentYChanged: {
    var rise = contentY - settledContentY
    settledContentY = contentY
    // Only upward moves: a card arriving at the top pushes the stack down as before.
    if (rise <= 0 || FoamyMotion.slider <= 0)
      return
    slideBack.stop()
    slide += rise
    slideBack.start()
  }
  onGroupsChanged: Qt.callLater(reconcile)
  Component.onCompleted: {
    contentItem.transform = [slideShift]
    settledContentY = contentY
    reconcile()
  }

  // While the pointer is over the stack, a card that leaves above it keeps its
  // space, so the card under the pointer and those below never slide away from
  // it. A card that leaves under the pointer (closing the hovered card) or
  // below it is let go, so the cards below move up.
  function holdsRemoval(card) {
    return pointerY >= 0 && card.mapToItem(null, 0, card.height).y <= pointerY
  }

  // Height of the cards that stay (not those fading out) and the note: what
  // the stack needs once removals finish.
  function settledHeight() {
    var total = 0
    for (var i = 0; i < count; i++) {
      var item = itemAtIndex(i)
      if (item)
        total += item.height + (i > 0 ? spacing : 0)
    }
    return total + (footerItem ? footerItem.height : 0)
  }

  function sameGroup(serialized, group) {
    var previous = JSON.parse(serialized)
    return previous.key === group.key || previous.keys.some(function (key) {
      return group.keys.indexOf(key) >= 0
    })
  }

  function reconcile() {
    // Incremental model edits preserve surviving delegates and let Qt delay removal.
    // Resetting the model would discard both hover state and removal transitions.
    for (var i = entries.count - 1; i >= 0; i--) {
      var serialized = entries.get(i).entryJson
      if (!groups.some(function (group) {
        return sameGroup(serialized, group)
      }))
        entries.remove(i)
    }
    for (var target = 0; target < groups.length; target++) {
      var group = groups[target], found = -1
      for (var j = target; j < entries.count; j++) {
        if (sameGroup(entries.get(j).entryJson, group)) {
          found = j
          break
        }
      }
      var next = JSON.stringify(group)
      if (found < 0)
        entries.insert(target, {
          entryJson: next
        })
      else {
        if (found !== target)
          entries.move(found, target, 1)
        if (entries.get(target).entryJson !== next)
          entries.setProperty(target, "entryJson", next)
      }
    }
    // Merging groups can match several old rows to one survivor.
    if (entries.count > groups.length)
      entries.remove(groups.length, entries.count - groups.length)
  }
}
