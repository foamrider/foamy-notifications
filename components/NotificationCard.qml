import QtQuick
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell
import qs.Commons
import "../NotificationLogic.js" as Logic

Rectangle {
  id: root
  property string app: ""
  property string appIcon: ""
  property string image: ""
  property bool showTimeoutIndicator: true
  property real timeoutMs: 0
  property real remainingMs: 0
  readonly property real timeoutProgress: timeoutMs > 0 ? Math.max(0, Math.min(1, remainingMs / timeoutMs)) : 0
  property bool compact: true
  property bool showImages: true
  property int imageSize: 56
  property string summary: ""
  property string body: ""
  property string glyph: ""
  property int urgency: 1
  property double timestamp: 0
  property int count: 1
  property var actions: []
  property bool busy: false
  property string error: ""
  property string fontFamily: Style.font.family
  property real cardWidth: Style.space(390)
  property bool actionsExpanded: false
  // Keep settings reachable without giving browser chrome a permanent action row.
  readonly property var primaryActions: actions.filter(function(a) { return a.identifier !== "settings" }).slice(0, 2)
  readonly property var overflowActions: actions.filter(function(a) { return primaryActions.indexOf(a) < 0 })
  readonly property bool hovered: hover.hovered || actionsExpanded
  readonly property bool hasTitle: summary.trim().length > 0
  readonly property string smallIcon: {
    if (!appIcon) return ""
    if (appIcon.indexOf("file://") === 0 || appIcon.indexOf("image://") === 0) return appIcon
    if (appIcon.charAt(0) === "/") return Util.fileUrl(appIcon)
    if (appIcon.indexOf(":") >= 0) return ""
    return Quickshell.iconPath(appIcon, true)
  }
  readonly property string safeImageSource: {
    if (image.indexOf("file://") === 0 || image.indexOf("image://") === 0) return image
    return image.charAt(0) === "/" ? Util.fileUrl(image) : ""
  }
  readonly property string contentImageSource: showImages ? safeImageSource : ""
  onActionsChanged: actionsExpanded = false
  onBusyChanged: if (busy) actionsExpanded = false
  Keys.onEscapePressed: actionsExpanded = false
  signal closeRequested()
  signal cardClicked()
  signal actionRequested(string identifier)
  implicitWidth: cardWidth
  implicitHeight: content.implicitHeight + 2
  radius: Style.cornerRadius * 2
  color: urgency === 2 ? Qt.tint(Color.popups.background, Util.alpha(Color.urgent, 0.09)) : Color.popups.background
  border.width: 1
  border.color: Util.alpha(Color.popups.text, 0.18)
  HoverHandler { id: hover }
  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: Qt.PointingHandCursor
    enabled: !root.busy
    onClicked: function(mouse) {
      if (mouse.button === Qt.RightButton) root.closeRequested()
      else root.cardClicked()
    }
  }
  ColumnLayout {
    id: content
    x: 1; y: 1
    width: parent.width - 2
    spacing: 0
    Item {
      Layout.fillWidth: true
      objectName: "notificationHeader"
      implicitHeight: header.implicitHeight + Style.space(root.compact ? 8 : 20)
      clip: true
      // Give the background enough height that Qt does not shrink large corners.
      Rectangle {
        width: parent.width
        height: Math.max(parent.height, topLeftRadius * 2)
        topLeftRadius: Math.max(0, root.radius - root.border.width)
        topRightRadius: topLeftRadius
        color: Qt.tint(Color.popups.background, Util.alpha(Color.popups.text, 0.055))
      }
      RowLayout {
        id: header
        anchors.fill: parent
        anchors.topMargin: Style.space(root.compact ? 4 : 10); anchors.bottomMargin: anchors.topMargin
        anchors.leftMargin: Style.space(13); anchors.rightMargin: Style.space(9)
        spacing: Style.space(8)
        Item {
          Layout.preferredWidth: Style.space(root.compact ? 14 : 22)
          Layout.preferredHeight: Layout.preferredWidth
          Image {
            id: icon
            objectName: "notificationAppIcon"
            anchors.fill: parent
            source: root.smallIcon
            sourceSize.width: Math.ceil(width * 2); sourceSize.height: Math.ceil(height * 2)
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            visible: status === Image.Ready
          }
          Text {
            anchors.centerIn: parent
            visible: icon.status !== Image.Ready
            text: root.glyph || "󰂚"
            textFormat: Text.PlainText
            font.family: root.fontFamily
            font.pixelSize: Style.space(14)
            color: root.urgency === 2 ? Color.urgent : Color.accent
          }
        }
        Text {
          objectName: "notificationAppName"
          Layout.fillWidth: true
          text: root.app || "Notification"
          textFormat: Text.PlainText
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          color: Color.popups.text
          elide: Text.ElideRight
          maximumLineCount: 1
        }
        Text {
          visible: root.count > 1
          text: "· " + root.count
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          color: Util.alpha(Color.popups.text, 0.65)
        }
        Text {
          objectName: "notificationTime"
          visible: root.timestamp > 0
          text: visible ? Qt.formatTime(new Date(root.timestamp), "hh:mm") : ""
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          color: Util.alpha(Color.popups.text, 0.65)
        }
        Controls.AbstractButton {
          id: moreButton
          objectName: "moreActionsButton"
          visible: root.overflowActions.length > 0
          Layout.preferredWidth: Style.space(24)
          Layout.preferredHeight: Style.space(24)
          enabled: !root.busy
          Accessible.name: root.actionsExpanded ? "Hide more actions" : "Show more actions"
          background: Rectangle { radius: Style.cornerRadius * 2; color: moreButton.hovered || root.actionsExpanded ? Util.alpha(Color.popups.text, 0.09) : "transparent" }
          contentItem: Text { text: "⋯"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: Color.popups.text; font.family: root.fontFamily; font.pixelSize: Style.font.title }
          onClicked: root.actionsExpanded = !root.actionsExpanded
        }
        Controls.AbstractButton {
          id: closeButton
          objectName: "dismissButton"
          Layout.preferredWidth: Style.space(24)
          Layout.preferredHeight: Style.space(24)
          enabled: !root.busy
          Accessible.name: "Dismiss notification"
          background: Rectangle { radius: Style.cornerRadius * 2; color: closeButton.hovered ? Util.alpha(Color.popups.text, 0.09) : "transparent" }
          contentItem: Text { text: "×"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: Util.alpha(Color.popups.text, 0.7); font.family: root.fontFamily; font.pixelSize: Style.font.title }
          onClicked: root.closeRequested()
        }
      }
    }
    ColumnLayout {
      Layout.fillWidth: true
      Layout.margins: Style.space(root.compact ? 13 : 16)
      spacing: Style.space(4)
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(12)
        // All sender images share one slot: browser avatars and screenshots need no app heuristics.
        Rectangle {
          id: imageTile
          objectName: "notificationImageTile"
          Layout.preferredWidth: Style.space(root.imageSize)
          Layout.preferredHeight: Style.space(root.imageSize)
          Layout.alignment: Qt.AlignTop
          visible: contentImage.status === Image.Ready
          color: Util.alpha(Color.popups.text, 0.055)
          radius: Style.cornerRadius * 2
          // Keep the mask layer stable while asynchronous loading and screen scaling settle.
          layer.enabled: true
          layer.effect: MultiEffect {
            maskEnabled: true
            maskSource: imageMask
            maskThresholdMin: 0.5
            maskSpreadAtMin: 1.0
          }
          Image {
            id: contentImage
            objectName: "notificationImage"
            anchors.fill: parent
            source: root.contentImageSource
            sourceSize.width: 144; sourceSize.height: 144
            // Keep the full screenshot visible; square avatars naturally fill the same tile.
            fillMode: Image.PreserveAspectFit
            asynchronous: true
          }
        }
        ColumnLayout {
          Layout.fillWidth: true
          Layout.alignment: Qt.AlignTop
          spacing: Style.space(4)
          Text {
            objectName: "notificationTitle"
            Layout.fillWidth: true
            visible: root.hasTitle
            text: root.summary
            textFormat: Text.PlainText
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            color: Color.popups.text
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
          }
          Text {
            Layout.fillWidth: true
            visible: text.length > 0
            text: Logic.styledBody(root.body, root.app, root.appIcon)
            textFormat: Text.StyledText
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: Util.alpha(Color.popups.text, 0.75)
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
          }
        }
      }
      Flow {
        Layout.fillWidth: true
        Layout.topMargin: visible ? Style.space(3) : 0
        spacing: Style.space(5)
        visible: root.primaryActions.length > 0
        Repeater {
          model: root.primaryActions
          ActionButton {
            required property var modelData
            objectName: "notificationAction"
            actionData: modelData
            width: Math.min(implicitWidth, parent.width)
          }
        }
      }
      // Expanding inside the card keeps the input region and popup stack in sync.
      ColumnLayout {
        Layout.fillWidth: true
        visible: root.actionsExpanded && root.overflowActions.length > 0
        spacing: Style.space(3)
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Util.alpha(Color.popups.text, 0.2) }
        Repeater {
          model: root.overflowActions
          ActionButton {
            required property var modelData
            objectName: "overflowAction-" + modelData.identifier
            actionData: modelData
            Layout.fillWidth: true
          }
        }
      }
      Text {
        Layout.fillWidth: true
        visible: root.error.length > 0
        text: root.error
        textFormat: Text.PlainText
        color: Color.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }
    }
  }

  Rectangle {
    id: imageMask
    width: imageTile.width; height: imageTile.height
    radius: imageTile.radius
    color: "white"
    visible: false
    layer.enabled: true
  }

  component ActionButton: Controls.AbstractButton {
    id: actionButton
    required property var actionData
    implicitWidth: label.implicitWidth + Style.space(16)
    implicitHeight: label.implicitHeight + Style.space(8)
    enabled: !root.busy
    Accessible.name: actionData.text
    background: Rectangle {
      radius: Style.cornerRadius * 2
      color: Qt.tint(Color.popups.background, Util.alpha(Color.popups.text, actionButton.hovered ? 0.1 : 0.04))
      border.width: 1
      border.color: Util.alpha(Color.popups.text, 0.2)
    }
    contentItem: Text {
      id: label
      text: actionButton.actionData.text
      textFormat: Text.PlainText
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      color: Color.popups.text
      horizontalAlignment: Text.AlignHCenter
      verticalAlignment: Text.AlignVCenter
      elide: Text.ElideRight
      maximumLineCount: 1
    }
    onClicked: { root.actionsExpanded = false; root.actionRequested(actionData.identifier) }
  }
  Rectangle {
    id: timeoutIndicator
    objectName: "notificationTimeoutIndicator"
    // Keep the full track on the straight edge, clear of both rounded corners.
    x: root.radius + 1
    y: parent.height - height - 1
    width: Math.max(0, parent.width - 2 * x) * progress
    height: 2
    visible: root.showTimeoutIndicator && root.timeoutMs > 0 && progress > 0
    readonly property real progress: root.timeoutProgress
    color: Util.alpha(Color.accent, 0.55)
    antialiasing: true
  }
  Canvas {
    id: urgentEdge
    visible: root.urgency === 2
    width: Math.max(curveRadius + 1, Style.space(3)); height: root.height
    property color accent: Color.urgent
    property real curveRadius: Math.min(root.radius, root.width / 2, root.height / 2)
    onAccentChanged: requestPaint()
    onCurveRadiusChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onVisibleChanged: if (visible) requestPaint()
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var r = curveRadius, h = height, thickness = Style.space(3)
      ctx.fillStyle = accent
      // A square card needs a straight urgency stripe, with no zero-radius arcs.
      if (r === 0) {
        ctx.fillRect(0, 0, thickness, h)
        return
      }
      ctx.beginPath()
      // Follow the card outline; taper thickness to zero at each horizontal tangent.
      ctx.moveTo(r, 0)
      ctx.arc(r, r, r, -Math.PI / 2, -Math.PI, true)
      ctx.lineTo(0, h - r)
      ctx.arc(r, h - r, r, Math.PI, Math.PI / 2, true)
      for (var i = 0; i <= 24; i++) {
        var angle = Math.PI / 2 + i * Math.PI / 48
        var inner = r + thickness * Math.cos(angle)
        ctx.lineTo(r + inner * Math.cos(angle), h - r + inner * Math.sin(angle))
      }
      ctx.lineTo(thickness, r)
      for (var j = 0; j <= 24; j++) {
        var topAngle = -Math.PI + j * Math.PI / 48
        var topInner = r + thickness * Math.cos(topAngle)
        ctx.lineTo(r + topInner * Math.cos(topAngle), r + topInner * Math.sin(topAngle))
      }
      ctx.closePath()
      ctx.fill()
    }
  }
}
