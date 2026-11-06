import QtQuick
import "../kit"

ListView {
  id: root
  property var groups: []
  // Once the final card leaves, do not keep an invisible input region under the pointer.
  readonly property bool holdPositions: groups.length > 0 && pointer.hovered
  // The pointer's height in the stack, or -1 when it isn't over the stack.
  readonly property real pointerY: holdPositions ? pointer.point.position.y : -1
  implicitHeight: Math.max(0, contentHeight)
  // A nonzero initial viewport lets ListView measure its first delegate.
  height: Math.max(1, implicitHeight)
  interactive: false
  currentIndex: -1
  model: entries
  HoverHandler { id: pointer }
  ListModel { id: entries }
  removeDisplaced: Transition {
    NumberAnimation { properties: "y"; duration: FoamyMotion.slider; easing.type: Easing.OutCubic }
  }
  onGroupsChanged: Qt.callLater(reconcile)
  Component.onCompleted: reconcile()

  // While the pointer is over the stack, a card that leaves above it keeps its
  // space, so the card under the pointer and those below never slide away from
  // it. A card that leaves under the pointer (closing the hovered card) or
  // below it is let go, so the cards below move up.
  function holdsRemoval(card) {
    // card.y is in content coordinates; removals at the top move the origin.
    return pointerY >= 0 && card.y - contentY + card.height <= pointerY
  }

  function sameGroup(serialized, group) {
    var previous = JSON.parse(serialized)
    return previous.key === group.key || previous.keys.some(function(key) { return group.keys.indexOf(key) >= 0 })
  }

  function reconcile() {
    // Incremental model edits preserve surviving delegates and let Qt delay removal.
    // Resetting the model would discard both hover state and removal transitions.
    for (var i = entries.count - 1; i >= 0; i--) {
      var serialized = entries.get(i).entryJson
      if (!groups.some(function(group) { return sameGroup(serialized, group) })) entries.remove(i)
    }
    for (var target = 0; target < groups.length; target++) {
      var group = groups[target], found = -1
      for (var j = target; j < entries.count; j++) {
        if (sameGroup(entries.get(j).entryJson, group)) { found = j; break }
      }
      var next = JSON.stringify(group)
      if (found < 0) entries.insert(target, {entryJson: next})
      else {
        if (found !== target) entries.move(found, target, 1)
        if (entries.get(target).entryJson !== next) entries.setProperty(target, "entryJson", next)
      }
    }
    // Merging groups can match several old rows to one survivor.
    if (entries.count > groups.length) entries.remove(groups.length, entries.count - groups.length)
  }
}
