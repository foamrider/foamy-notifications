import QtQuick
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell
import qs.Commons
import qs.Commons as Commons
import "../NotificationLogic.js" as Logic
import "../Markup.js" as Markup
import "../kit"

Rectangle {
  id: root
  LayoutMirroring.enabled: FoamyI18n.rightToLeft
  LayoutMirroring.childrenInherit: true
  property string app: ""
  property string appIcon: ""
  property string favicon: ""
  readonly property string faviconSource: favicon.indexOf("file://") === 0 ? favicon : ""
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
  property var offers: ({codes:"", link:"", meeting:false})
  property bool snoozeAvailable: false
  property string copiedCode: ""
  property bool snoozeExpanded: false
  signal featureRequested(string kind, string value)
  property bool busy: false
  property string error: ""
  // Foamy body font; glyphs (Nerd Font code points) use the Omarchy mono font.
  property string fontFamily: FoamyStyle.fontFamily
  readonly property string glyphFamily: FoamyStyle.monoFamily
  // The theme's [notifications] colours (4.0 and 4.5), foamy 1px border.
  readonly property color surface: Commons.Color.notifications ? Commons.Color.notifications.background : Commons.Color.popups.background
  readonly property color ink: Commons.Color.notifications ? Commons.Color.notifications.text : Commons.Color.popups.text
  // Keyboard mode (`omarchy-shell notifications keyboard`): this card has the cursor.
  property bool selected: false
  // Every button in draw/number order (NotificationLogic.deeds).
  property var deeds: []
  property real cardWidth: Style.space(390)
  property bool actionsExpanded: false
  // The footer holds Omarchy --action buttons, two sender actions, codes and
  // the link; further sender actions (and Settings) go behind "More actions".
  readonly property var footerActions: {
    var out = [], senders = 0
    for (var i = 0; i < deeds.length; i++) {
      var deed = deeds[i]
      if (deed.kind === "action" && (senders >= 2 || /^settings$/i.test(deed.value))) continue
      if (deed.kind === "action") senders++
      out.push({kind: deed.kind, value: deed.value, number: i + 1,
        text: deed.kind === "link" ? (deed.meeting ? FoamyI18n.t("Join meeting") : FoamyI18n.t("Open link")) : deed.text,
        glyph: deed.kind === "code" ? "󰆏" : deed.kind === "link" ? (deed.meeting ? "󰕧" : "󰏌") : deed.kind === "action" && deed.value === "reply" ? "󰑚" : ""})
    }
    return out
  }
  readonly property var overflowActions: {
    var shown = footerActions.map(function(entry) { return entry.kind + ":" + entry.value })
    return deeds.filter(function(deed) { return deed.kind === "action" && shown.indexOf("action:" + deed.value) < 0 })
      .map(function(deed) { return {identifier: deed.value, text: deed.text} })
  }
  readonly property color copiedColor: FoamyPalette.success
  readonly property bool hovered: hover.hovered || actionsExpanded || snoozeExpanded || selected
  readonly property bool hasTitle: summary.trim().length > 0
  readonly property string smallIcon: {
    if (!appIcon) return ""
    if (appIcon.indexOf("file://") === 0) return appIcon
    var name = Logic.themeIcon(appIcon)
    return name ? Quickshell.iconPath(name, true) : ""
  }
  readonly property string safeImageSource: {
    if (image.indexOf("file://") === 0) return image
    return image.charAt(0) === "/" ? Util.fileUrl(image) : ""
  }
  readonly property string contentImageSource: showImages ? safeImageSource : ""
  onActionsChanged: actionsExpanded = false
  onBusyChanged: if (busy) actionsExpanded = false
  Keys.onEscapePressed: { actionsExpanded = false; snoozeExpanded = false }
  signal closeRequested()
  signal hideRequested()
  signal cardClicked()
  signal actionRequested(string identifier)
  implicitWidth: cardWidth
  implicitHeight: content.implicitHeight + 2
  radius: FoamyStyle.radius
  color: urgency === 2 ? Qt.tint(root.surface, Util.alpha(Commons.Color.urgent, 0.09)) : root.surface
  border.width: selected ? 2 : 1
  border.color: selected ? Commons.Color.accent : Util.alpha(root.ink, 0.15)
  HoverHandler { id: hover }
  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: Qt.PointingHandCursor
    enabled: !root.busy
    onClicked: function(mouse) {
      if (mouse.button === Qt.RightButton) root.hideRequested()
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
        color: Qt.tint(root.surface, Util.alpha(root.ink, 0.055))
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
            visible: status === Image.Ready && faviconIcon.status !== Image.Ready
          }
          Image {
            id: faviconIcon
            objectName: "notificationFavicon"
            anchors.fill: parent
            source: root.faviconSource
            sourceSize.width: Math.ceil(width * 2); sourceSize.height: Math.ceil(height * 2)
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            visible: status === Image.Ready
          }
          Text {
            anchors.centerIn: parent
            visible: icon.status !== Image.Ready && faviconIcon.status !== Image.Ready
            text: root.glyph || "󰂚"
            textFormat: Text.PlainText
            font.family: root.glyphFamily
            font.pixelSize: FoamyStyle.font.title
            color: root.urgency === 2 ? Commons.Color.urgent : Commons.Color.accent
          }
        }
        Text {
          objectName: "notificationAppName"
          Layout.fillWidth: true
          text: root.app || FoamyI18n.t("Notification")
          textFormat: Text.PlainText
          font.family: root.fontFamily
          font.pixelSize: FoamyStyle.font.caption
          color: root.ink
          elide: Text.ElideRight
          maximumLineCount: 1
        }
        Text {
          visible: root.count > 1
          text: "· " + FoamyI18n.number(root.count)
          font.family: root.fontFamily
          font.pixelSize: FoamyStyle.font.caption
          color: Util.alpha(root.ink, 0.65)
        }
        Controls.AbstractButton {
          id: snoozeButton
          objectName: "notificationSnooze"
          visible: root.snoozeAvailable
          // Reserve the slot so revealing the control does not move the timestamp.
          opacity: hover.hovered || activeFocus ? 1 : 0
          focusPolicy: Qt.TabFocus
          Layout.preferredWidth: Style.space(24)
          Layout.preferredHeight: Style.space(24)
          enabled: !root.busy
          Accessible.name: FoamyI18n.t("Snooze this sender")
          background: Rectangle { radius: FoamyStyle.radius; color: snoozeButton.hovered || root.snoozeExpanded ? Util.alpha(root.ink, 0.09) : "transparent" }
          contentItem: Text { text: "󰥔"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: root.ink; font.family: root.glyphFamily; font.pixelSize: FoamyStyle.font.body }
          onClicked: root.snoozeExpanded = !root.snoozeExpanded
        }
        Text {
          objectName: "notificationTime"
          visible: root.timestamp > 0
          text: visible ? Qt.locale(FoamyI18n.formatLocale).toString(new Date(root.timestamp), "hh:mm") : ""
          font.family: root.fontFamily
          font.pixelSize: FoamyStyle.font.caption
          color: Util.alpha(root.ink, 0.65)
        }
        Controls.AbstractButton {
          id: moreButton
          objectName: "moreActionsButton"
          visible: root.overflowActions.length > 0
          Layout.preferredWidth: Style.space(24)
          Layout.preferredHeight: Style.space(24)
          enabled: !root.busy
          Accessible.name: root.actionsExpanded ? FoamyI18n.t("Hide more actions") : FoamyI18n.t("Show more actions")
          background: Rectangle { radius: FoamyStyle.radius; color: moreButton.hovered || root.actionsExpanded ? Util.alpha(root.ink, 0.09) : "transparent" }
          contentItem: Text { text: "⋯"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: root.ink; font.family: root.fontFamily; font.pixelSize: FoamyStyle.font.title }
          onClicked: root.actionsExpanded = !root.actionsExpanded
        }
        Controls.AbstractButton {
          id: closeButton
          objectName: "dismissButton"
          Layout.preferredWidth: Style.space(24)
          Layout.preferredHeight: Style.space(24)
          enabled: !root.busy
          Accessible.name: FoamyI18n.t("Dismiss notification")
          background: Rectangle { radius: FoamyStyle.radius; color: closeButton.hovered ? Util.alpha(root.ink, 0.09) : "transparent" }
          contentItem: Text { text: "×"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: Util.alpha(root.ink, 0.7); font.family: root.fontFamily; font.pixelSize: FoamyStyle.font.title }
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
          color: Util.alpha(root.ink, 0.055)
          radius: FoamyStyle.radius
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
            font.pixelSize: FoamyStyle.font.body
            color: root.ink
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
          }
          Text {
            Layout.fillWidth: true
            visible: text.length > 0
            // Stock 4.5 Markup semantics: decode, escape all, allow b/i/u/s/em/strong/br only.
            text: Markup.render(Logic.sanitizeBody(root.body, root.app, root.appIcon))
            textFormat: Text.StyledText
            font.family: root.fontFamily
            font.pixelSize: FoamyStyle.font.caption
            color: Util.alpha(root.ink, 0.75)
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
          }
        }
      }
      Flow {
        Layout.fillWidth: true
        spacing: Style.space(5)
        visible: root.snoozeExpanded
        Repeater {
          model: [15, 30, 60, 240]
          FeatureButton {
            required property int modelData
            objectName: "notificationSnooze" + modelData
            text: modelData < 60 ? FoamyI18n.t("%1 min", FoamyI18n.number(modelData)) : FoamyI18n.t("%1 h", FoamyI18n.number(modelData / 60))
            width: Math.min(implicitWidth, parent.width)
            onClicked: { root.snoozeExpanded = false; root.featureRequested("snooze", String(modelData)) }
          }
        }
      }
      // Expanding inside the card keeps the input region and popup stack in sync.
      ColumnLayout {
        Layout.fillWidth: true
        visible: root.actionsExpanded && root.overflowActions.length > 0
        spacing: Style.space(3)
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Util.alpha(root.ink, 0.2) }
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
        text: FoamyI18n.t(root.error)
        textFormat: Text.PlainText
        color: Commons.Color.urgent
        font.family: root.fontFamily
        font.pixelSize: FoamyStyle.font.caption
        wrapMode: Text.Wrap
      }
    }
    GridLayout {
      id: footer
      objectName: "notificationFooter"
      Layout.fillWidth: true
      visible: root.footerActions.length > 0
      columns: Math.min(2, root.footerActions.length)
      columnSpacing: 0
      rowSpacing: 0
      uniformCellWidths: true
      Repeater {
        model: root.footerActions
        FooterButton {
          required property int index
          required property var modelData
          entry: modelData
          objectName: entry.kind === "code" ? "notificationCopyCode" : entry.kind === "link" ? "notificationOpenLink" : "notificationAction"
          Layout.fillWidth: true
          Layout.fillHeight: true
          Layout.minimumWidth: 0
          Layout.preferredWidth: 1
          // An odd final action fills its row, including on narrow cards.
          Layout.columnSpan: index === root.footerActions.length - 1 && index % 2 === 0 ? footer.columns : 1
          leftDivider: index % 2 === 1
          readonly property bool firstInLastRow: index === root.footerActions.length - (root.footerActions.length % 2 === 0 ? 2 : 1)
          readonly property bool lastInLastRow: index === root.footerActions.length - 1
          roundLeft: FoamyI18n.rightToLeft ? lastInLastRow : firstInLastRow
          roundRight: FoamyI18n.rightToLeft ? firstInLastRow : lastInLastRow
        }
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

  component FooterButton: Controls.AbstractButton {
    id: footerButton
    required property var entry
    property bool leftDivider: false
    property bool roundLeft: false
    property bool roundRight: false
    readonly property bool copied: entry.kind === "code" && root.copiedCode === entry.value
    readonly property string glyph: copied ? "󰄬" : entry.glyph
    readonly property string label: entry.kind === "code" ? (copied ? FoamyI18n.t("Copied %1", entry.value) : FoamyI18n.t("Copy %1", entry.value)) : entry.text
    // In keyboard mode the number key that presses it leads the label.
    text: root.selected && entry.number <= 9 ? FoamyI18n.number(entry.number) + "  " + label : label
    implicitHeight: Math.max(Style.space(34), footerLabel.implicitHeight + Style.space(12))
    enabled: !root.busy
    Accessible.name: text
    hoverEnabled: true
    background: Rectangle {
      bottomLeftRadius: footerButton.roundLeft ? Math.max(0, root.radius - 1) : 0
      bottomRightRadius: footerButton.roundRight ? Math.max(0, root.radius - 1) : 0
      color: Qt.tint(root.surface, Util.alpha(root.ink, footerButton.hovered ? 0.10 : 0.055))
      Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Util.alpha(root.ink, 0.15) }
      Rectangle { visible: footerButton.leftDivider; anchors.left: parent.left; width: 1; height: parent.height; color: Util.alpha(root.ink, 0.15) }
      Rectangle {
        anchors.fill: parent; anchors.margins: 2
        visible: footerButton.activeFocus
        color: "transparent"; border.width: 1; border.color: Commons.Color.accent
        radius: Style.cornerRadius
      }
    }
    contentItem: Item {
      RowLayout {
        id: footerLabel
        objectName: "notificationFooterLabel"
        anchors.centerIn: parent
        width: Math.min(implicitWidth, Math.max(0, parent.width - Style.space(16)))
        spacing: Style.space(7)
        Text {
          visible: footerButton.glyph.length > 0
          text: footerButton.glyph
          font.family: root.glyphFamily
          font.pixelSize: FoamyStyle.font.title
          color: footerButton.copied ? root.copiedColor : root.ink
          Layout.alignment: Qt.AlignVCenter
        }
        Text {
          Layout.fillWidth: true
          Layout.minimumWidth: 0
          text: footerButton.text
          textFormat: Text.PlainText
          font.family: root.fontFamily
          font.pixelSize: FoamyStyle.font.caption
          color: footerButton.copied ? root.copiedColor : root.ink
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideRight
        }
      }
    }
    Controls.ToolTip.visible: hovered && footerLabel.implicitWidth > width - Style.space(16)
    Controls.ToolTip.text: text
    onClicked: {
      if (entry.kind === "action") {
        root.actionsExpanded = false
        root.actionRequested(entry.value)
      } else root.featureRequested(entry.kind, entry.value)
    }
  }

  component FeatureButton: Controls.AbstractButton {
    id: featureButton
    implicitWidth: featureLabel.implicitWidth + Style.space(16)
    implicitHeight: featureLabel.implicitHeight + Style.space(8)
    enabled: !root.busy
    Accessible.name: text
    background: Rectangle {
      radius: FoamyStyle.radius
      color: Qt.tint(root.surface, Util.alpha(root.ink, featureButton.hovered || featureButton.activeFocus ? 0.12 : 0.04))
      border.width: 1
      border.color: featureButton.activeFocus ? Commons.Color.accent : Util.alpha(root.ink, 0.2)
    }
    contentItem: Text {
      id: featureLabel
      text: featureButton.text
      textFormat: Text.PlainText
      font.family: root.fontFamily
      font.pixelSize: FoamyStyle.font.caption
      color: root.ink
      horizontalAlignment: Text.AlignHCenter
      verticalAlignment: Text.AlignVCenter
      elide: Text.ElideRight
    }
  }

  component ActionButton: Controls.AbstractButton {
    id: actionButton
    required property var actionData
    implicitWidth: label.implicitWidth + Style.space(16)
    implicitHeight: label.implicitHeight + Style.space(8)
    enabled: !root.busy
    Accessible.name: actionData.text
    background: Rectangle {
      radius: FoamyStyle.radius
      color: Qt.tint(root.surface, Util.alpha(root.ink, actionButton.hovered ? 0.1 : 0.04))
      border.width: 1
      border.color: Util.alpha(root.ink, 0.2)
    }
    contentItem: Text {
      id: label
      text: actionButton.actionData.text
      textFormat: Text.PlainText
      font.family: root.fontFamily
      font.pixelSize: FoamyStyle.font.caption
      color: root.ink
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
    color: Util.alpha(Commons.Color.accent, 0.55)
    antialiasing: true
  }
  Canvas {
    id: urgentEdge
    visible: root.urgency === 2
    width: Math.max(curveRadius + 1, Style.space(3)); height: root.height
    property color accent: Commons.Color.urgent
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
