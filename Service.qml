// Notification service for the omarchy shell.

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.Pipewire
import Quickshell.Services.Notifications
import qs.Commons

import "kit"
import "components"
import "NotificationLogic.js" as NotificationLogic
import "Features.js" as Features
import "Security.js" as Security
import "Preferences.js" as Preferences
import "BrowserIdentity.js" as BrowserIdentity

Item {
  id: service

  // Injected by omarchy-shell (the first-party service loader).
  property var shell: null

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  readonly property string home: Quickshell.env("HOME")
  // Foamy keeps its own state directory. Omarchy 4.5 owns omarchy/notifications
  // (its upgrade migration deletes the 4.0 per-file layout found there), so the
  // helper moves Foamy's old files here once, before anything reads them.
  // Layout and IPC contract: README "Notification Center contract".
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/foamy-notifications/"
  readonly property string settingsPath: stateDir + "state.json"
  // One file per on-screen popup, written when the toast appears and moved
  // into historyDir when it expires or is dismissed; handled ones are removed.
  readonly property string popupStateDir: stateDir + "popups/"
  // The notifications that already left the screen, newest historyLimit.
  readonly property string historyDir: stateDir + "history/"
  // Normalized copies of the images persisted entries reference.
  readonly property string imagesDir: stateDir + "images/"
  // Keys handled here (clicked/acted on), newest last; the center reads it.
  readonly property string handledPath: stateDir + "handled"
  // True once the one-time migration ran and live popups were restored.
  property bool stateMigrated: false
  property bool storeReady: false
  // Toasts are fixed to the top-right corner. They only clear the omarchy bar
  // when the bar occupies the top or right edge, so left/bottom bars do not
  // pull notification popups away from the expected top-right location.
  // Falls back to the bar's default size (26 horizontal / 28 vertical) when
  // shell.bar isn't reachable so the popup never lands on top of the bar.
  readonly property string barPosition: shell && shell.barConfig ? String(shell.barConfig.position || "top") : "top"
  readonly property bool barVertical: barPosition === "left" || barPosition === "right"
  readonly property int defaultBarSize: barVertical ? Style.bar.sizeVertical : Style.bar.sizeHorizontal
  readonly property int liveBarSize: {
    var bar = shell && shell.bar ? shell.bar : null
    if (!bar) return defaultBarSize
    if (bar.barHidden) return 0
    // 4.5: a notch can thicken the bar on one screen; 4.0 has barSize only.
    var size = Number(typeof bar.barSizeFor === "function" ? bar.barSizeFor(service.focusedMonitor) : bar.barSize)
    return isFinite(size) && size > 0 ? size : defaultBarSize
  }
  readonly property int barClearance: liveBarSize + Style.gapsOut

  // Live Notification objects by originalId, kept OUT of the ListModels: a
  // QObject stored in a model role becomes a dangling C++ pointer when the
  // server destroys the notification (sender close, DND untrack, dismiss),
  // and the next read of that role segfaults in QQmlListModel::data. A JS
  // map only holds a wrapper, which degrades to a catchable error instead.
  property var liveRefs: ({})
  property var liveKeys: ({})
  // Primitive snapshots only; live QObjects stay in liveRefs until explicitly closed.
  property var retainedHistory: ({})

  // PersistentProperties handles in-process QML reloads. The on-disk
  // notifications.json file is the cross-restart backstop — its `dnd` key
  // is hydrated into persisted.doNotDisturb on startup and written back via
  // a debounced save timer.
  PersistentProperties {
    id: persisted
    reloadableId: "omarchy-notifications"
    property bool doNotDisturb: false
    onDoNotDisturbChanged: {
      // Suppress the write that load-time hydration would otherwise trigger.
      if (service._hydrating) return
      service.scheduleSettingsSave()
    }
  }

  // Guards onDoNotDisturbChanged while we're hydrating from disk so the
  // hydration assignment doesn't immediately schedule a write-back.
  property bool _hydrating: false

  readonly property alias doNotDisturb: persisted.doNotDisturb

  function setDoNotDisturb(value) {
    persisted.doNotDisturb = !!value
  }

  // popupModel feeds the on-screen toast stack. History text stays on disk;
  // retainedHistory keeps bounded snapshots for callbacks owned by live senders.
  //
  // Aliased as a property so consumers outside this Item's id scope can bind
  // to it. QML ids aren't visible to external consumers without the alias.
  property alias popupModel: popupModel
  ListModel { id: popupModel; onCountChanged: service.scheduleRebuild() }

  // How many notifications the history directory keeps, and therefore how
  // many `showHistory` can replay.
  readonly property int historyLimit: 100

  property var preferences: Preferences.defaults()
  property string configError: ""
  property string lastError: ""
  readonly property string helper: Qt.resolvedUrl("bin/notification-helper.py").toString().replace(/^file:\/\//, "")

  function durationFor(urgency, expireTimeout) {
    return Preferences.duration(preferences, urgency, expireTimeout)
  }

  FileView {
    id: configFile
    path: (Quickshell.env("XDG_CONFIG_HOME") || service.home + "/.config") + "/omarchy/shell.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var parsed = Preferences.parse(text())
      service.configError = parsed.error
      if (parsed.settings) {
        service.preferences = parsed.settings
        var layout = JSON.parse(text()).bar || {}
        service.centerEnabled = JSON.stringify(layout).indexOf('"foamy.notification-center"') >= 0
      }
      else console.warn("foamy.notifications configuration:", parsed.error)
    }
    onLoadFailed: service.configError = "Cannot read notification settings; using last valid settings"
  }
  onPreferencesChanged: { scheduleRebuild(); refreshContext(); pokeCenter("preferences") }

  // DND bypass: only let through notifications we trust to be intentional
  // and rare.
  //   - omarchy-action: a user-action confirmation toast ("Theme changed",
  //     "Screenshot saved"). The user JUST did something — their feedback
  //     should show.
  //   - urgency=critical AND app_name=notify-send: bare-CLI emergency alerts.
  //     Trusted because it's almost always omarchy or system shell scripts —
  //     chat apps set app_name to their brand (Discord/Slack/Vesktop), which
  //     falls outside this rule.
  function shouldBypassDnd(notification) {
    return NotificationLogic.shouldBypassDnd(notification, NotificationUrgency.Critical, preferences.dndSilenceCritical)
  }

  property var snoozes: ({})
  property double quietNow: Date.now()
  readonly property bool anySnooze: Object.keys(snoozes).some(function(key) { return snoozes[key] > quietNow })
  readonly property double globalSnoozeUntil: snoozes["*"] > quietNow ? snoozes["*"] : 0

  function isQuiet(row) {
    var quiet = doNotDisturb || snoozes["*"] > Date.now() || snoozes[Features.source(row)] > Date.now()
    return quiet && !shouldBypassDnd({appName:row.app, urgency:row.urgency})
  }

  function snoozeSource(source, minutes) {
    var duration = Number(minutes)
    if ([15, 30, 60, 240].indexOf(duration) < 0 || (source !== "*" && !/^(app|web):.{1,256}$/.test(source))) return "invalid"
    var next = Features.quietState(snoozes, Date.now())
    if (!next[source] && Object.keys(next).length >= 128) return "full"
    next[source] = Date.now() + duration * 60000
    snoozes = next
    quietNow = Date.now()
    scheduleSettingsSave()
    // Hide current matching cards without discarding their history or callbacks.
    for (var i = popupModel.count - 1; i >= 0; i--)
      if ((source === "*" || Features.source(popupModel.get(i)) === source) && !busyKeys[NotificationLogic.imageStem(popupModel.get(i))]) dismissPopup(i)
    return "ok"
  }

  function resumeSnoozes() {
    snoozes = ({})
    scheduleSettingsSave()
    return "ok"
  }

  Timer {
    interval: 1000; repeat: true; running: service.anySnooze
    onTriggered: {
      service.quietNow = Date.now()
      var next = Features.quietState(service.snoozes, service.quietNow)
      if (JSON.stringify(next) !== JSON.stringify(service.snoozes)) { service.snoozes = next; service.scheduleSettingsSave() }
    }
  }

  readonly property var sharingCandidates: {
    var nodes = Pipewire.nodes.values, out = []
    for (var i = 0; i < nodes.length && out.length < 64; i++)
      if (nodes[i].type === PwNodeType.VideoSource) out.push(nodes[i])
    return out
  }
  PwObjectTracker { objects: service.sharingCandidates }
  readonly property bool sharingActive: Pipewire.ready && sharingCandidates.some(function(node) { return node.ready && Features.isSharing(node.properties) })
  property bool sharingOfferHandled: false
  property real sharingPromptHeight: Style.space(140)
  readonly property string sharingPromptScreen: targetMonitors.length ? targetMonitors[0] : ""
  readonly property bool sharingOfferPending: preferences.offerSnoozeWhenSharing && sharingActive && !sharingOfferHandled && !doNotDisturb && !globalSnoozeUntil
  onSharingActiveChanged: sharingOfferHandled = sharingActive && (doNotDisturb || globalSnoozeUntil > 0)
  onDoNotDisturbChanged: if (doNotDisturb && sharingActive) sharingOfferHandled = true
  onGlobalSnoozeUntilChanged: if (globalSnoozeUntil > 0 && sharingActive) sharingOfferHandled = true

  function offersFor(row) { return Features.offers(row) }

  function featureAction(group, kind, value) {
    // Resolve the current card so stale delegates cannot act on replacement text.
    var row = displayGroups.find(function(row) { return row.key === group.key })
    if (!row || busyKeys[row.key]) return
    if (kind === "snooze") {
      if (snoozeSource(Features.source(row), value) !== "ok") finishAction(row.key, "Could not snooze this sender. Resume an existing snooze and try again.")
      return
    }
    if (kind === "exec") { runExecAction(row, Number(value)); return }
    var offers = Features.offers(row)
    if (kind === "code" && offers.codes.split(" ").indexOf(value) >= 0) {
      if (clipboardProc.running) { finishAction(row.key, "Clipboard is busy. Try again."); return }
      clipboardProc.entryKey = row.key
      clipboardProc.value = value
      clipboardProc.mode = "copy"
      clipboardProc.running = true
    } else if (kind === "link" && value === offers.link && Security.safeHttpUrl(value)) {
      if (!Qt.openUrlExternally(Security.safeHttpUrl(value))) finishAction(row.key, "Could not open the link. Try again.")
    }
  }

  // An omarchy-notification-send --action button. Its argv runs like the click
  // command: handling is persisted first, because the command may restart the shell.
  function runExecAction(group, index) {
    var actions = Security.parseExecActions(group.execActions)
    if (!actions || !(index >= 0 && index < actions.length)) { finishAction(group.key, "This action is no longer available."); return }
    var row = Object.assign({}, group)
    var keys = group.keys.slice()
    var busy = Object.assign({}, busyKeys); busy[row.key] = true; busyKeys = busy
    actionErrors = ({})
    lastError = ""
    var argv = actions[index].argv
    commitHandled(row, keys, function() { Util.execArgv(argv) })
  }

  property string clipboardSecret: ""
  property string clipboardCopiedKey: ""
  Timer {
    id: clipboardExpiry; interval: 60000
    onTriggered: {
      if (!service.clipboardSecret) return
      if (clipboardProc.running) { restart(); return }
      clipboardProc.entryKey = ""
      clipboardProc.value = service.clipboardSecret
      clipboardProc.mode = "clear"
      clipboardProc.running = true
    }
  }
  Process {
    id: clipboardProc
    property string entryKey: ""
    property string value: ""
    property string mode: "copy"
    command: ["bash", Qt.resolvedUrl("bin/notification-clipboard").toString().replace(/^file:\/\//, ""), mode]
    stdinEnabled: true
    onStarted: { write(value + "\n"); stdinEnabled = false }
    onExited: function(code, status) {
      if (code === 0 && status === 0 && mode === "copy") {
        service.clipboardSecret = value
        service.clipboardCopiedKey = entryKey
        clipboardExpiry.restart()
        service.finishAction(entryKey, "")
      } else if (mode === "copy") service.finishAction(entryKey, "Could not copy the code. Check wl-copy and try again.")
      else {
        if (code !== 0 || status !== 0) service.reportPersistenceError("Could not check or clear the verification-code clipboard")
        service.clipboardSecret = ""
        service.clipboardCopiedKey = ""
      }
      value = ""
      stdinEnabled = true
    }
  }

  function snapshotOf(notification) {
    return NotificationLogic.snapshotOf(notification, Date.now())
  }

  property var normalizedImages: ({})
  property var imageWriteVersions: ({})
  property double nextImageWriteVersion: 0

  function acceptNormalizedImages(result) {
    if (!result || !result.sources || !/^[0-9]+-[0-9]+$/.test(result.key)
        || imageWriteVersions[result.key] !== result.version) return
    for (var i = 0; i < popupModel.count; i++) {
      var row = popupModel.get(i)
      if (NotificationLogic.imageStem(row) !== result.key) continue
      // A queued conversion must never overwrite the image of a newer replacement.
      if (String(row.appIcon || "") !== result.sources.appIcon || String(row.image || "") !== result.sources.image) return
      var images = Object.assign({}, normalizedImages)
      images[result.key] = result
      normalizedImages = images
      return
    }
  }

  function popupImageFor(row, role) {
    var source = String(row[role] || "")
    if (role === "appIcon" && NotificationLogic.themeIcon(source)) return source
    if (isRestoredRow(row)) return NotificationLogic.normalizedImage(source, imagesDir)
    var result = normalizedImages[NotificationLogic.imageStem(row)]
    if (!result || result.sources[role] !== source) return ""
    return NotificationLogic.normalizedImage(result[role], imagesDir)
  }

  function forgetNormalizedImages(row, keepVersion) {
    var images = Object.assign({}, normalizedImages)
    delete images[NotificationLogic.imageStem(row)]
    normalizedImages = images
    if (!keepVersion) delete imageWriteVersions[NotificationLogic.imageStem(row)]
  }

  function shouldArchiveSilenced(snapshot) {
    // Critical CLI alerts explicitly silenced by DND or snooze still need history.
    // Respect transient senders and keep routine CLI feedback out of history.
    return !snapshot.transient && (!NotificationLogic.isEphemeralApp(snapshot.app)
      || (isQuiet(snapshot) && preferences.dndSilenceCritical && snapshot.urgency === NotificationUrgency.Critical))
  }

  // Raw buffers have no readable size in QML. Keep only primitives and release
  // before a compositor query or persistence worker can hold the native object.
  function detachRawNotification(notification) {
    if (!NotificationLogic.notificationWithinLimits(notification)) {
      rejectNotification(notification, "Notification update exceeds sender limits")
      return
    }
    var originalId = notification.id
    var key = liveRefs[originalId] === notification ? liveKeys[originalId] : ""
    var snapshot = key ? NotificationLogic.replacementSnapshot(notification, originalId, Number(key.split("-")[0])) : snapshotOf(notification)
    snapshot.actionsJson = "[]"
    // Replacement hints are cached until their own property signal fires. Finish
    // copying the group's remaining changes into primitives before deferred delivery;
    // closing below still releases the native object without waiting for a worker.
    if (notification.tracked) {
      function finishSnapshot() {
        var updated = NotificationLogic.replacementSnapshot(notification, originalId, snapshot.timestamp)
        updated.actionsJson = "[]"
        Object.assign(snapshot, updated)
      }
      for (var signalIndex = 0; signalIndex < updateSignals.length; signalIndex++)
        notification[updateSignals[signalIndex]].connect(finishSnapshot)
    }
    var historyOnly = key && !!retainedHistory[key]
    var pendingIndex = pendingNotifications.findIndex(function(job) { return job.notification === notification })
    if (key) {
      delete retainedHistory[key]
      delete liveKeys[originalId]
      delete liveRefs[originalId]
      cancelPendingWrites([key])
    }
    if (pendingIndex >= 0) pendingNotifications[pendingIndex] = {snapshot:snapshot}
    notification.tracked = false
    if (pendingIndex >= 0) return
    if (!key) { receiveDetachedSnapshot(snapshot); return }
    // The first popup insert may already be deferred; update it after that insert.
    Qt.callLater(function() {
      if (service.handledKeys[key]) return
      for (var i = 0; i < popupModel.count; i++) {
        if (NotificationLogic.imageStem(popupModel.get(i)) !== key) continue
        var roles = NotificationLogic.popupRoles()
        for (var r = 0; r < roles.length; r++) popupModel.setProperty(i, roles[r], snapshot[roles[r]])
        service.lifetimes[key] = service.durationFor(snapshot.urgency, snapshot.expireTimeout)
        if (!service.busyKeys[key]) service.persistPopupFile(snapshot)
        service.scheduleRebuild()
        return
      }
      if (!service.busyKeys[key]) service.handleDetachedSnapshot(snapshot, historyOnly)
    })
  }

  function receiveDetachedSnapshot(snapshot) {
    if (!preferences.suppressFullscreen && preferences.monitor !== "pointer") { handleDetachedSnapshot(snapshot, false); return }
    if (pendingNotifications.length >= 100) {
      reportPersistenceError("Notification monitor-context backlog is full")
      return
    }
    pendingNotifications = pendingNotifications.concat([{snapshot:snapshot}])
    refreshContext()
  }

  function handleDetachedSnapshot(snapshot, historyOnly) {
    if (historyOnly || isQuiet(snapshot) || suppressForFullscreen(snapshot.urgency)) {
      if (shouldArchiveSilenced(snapshot)) writeHistoryFile(snapshot)
      return
    }
    // Persistence degrades, display does not: a rejected write is reported and the toast still shows.
    persistPopupFile(snapshot)
    Qt.callLater(function() {
      removePopupsByOriginalId(snapshot.originalId, NotificationLogic.popupFileName(snapshot))
      popupModel.insert(0, snapshot)
      service.scheduleRebuild()
    })
  }

  function handleNotification(notification) {
    if (NotificationLogic.hasRawImage(notification)) { detachRawNotification(notification); return }
    if (!NotificationLogic.notificationWithinLimits(notification)
        || (!liveRefs[notification.id] && Object.keys(liveRefs).length >= 256)) {
      rejectNotification(notification, "Notification exceeds sender or live-callback limits")
      return
    }
    // Without `tracked = true` the Notification object is destroyed as soon
    // as this signal handler returns, which would null out the `ref` we just
    // captured for the popup card.
    notification.tracked = true
    var snapshot = snapshotOf(notification)
    var previousKey = liveKeys[snapshot.originalId]
    if (previousKey) delete retainedHistory[previousKey]
    liveRefs[snapshot.originalId] = notification
    liveKeys[snapshot.originalId] = NotificationLogic.imageStem(snapshot)
    // Guard the delete: a newer notification may have reused this originalId
    // (freedesktop replaces_id) and taken over the map slot.
    notification.closed.connect(function() {
      if (service.liveRefs[snapshot.originalId] !== notification) return
      delete service.liveRefs[snapshot.originalId]
      delete service.liveKeys[snapshot.originalId]
      delete service.retainedHistory[NotificationLogic.imageStem(snapshot)]
      // Action labels are primitives; remove them when their live callback dies.
      Qt.callLater(function() {
        for (var i = 0; i < popupModel.count; i++) {
          var row = popupModel.get(i)
          if (row.originalId === snapshot.originalId && row.timestamp === snapshot.timestamp)
            popupModel.setProperty(i,"actionsJson","[]")
        }
        service.scheduleRebuild()
      })
    })

    // DND bypass rules: chat apps abuse urgency=critical to force
    // visibility, so critical alone isn't enough — we also require the
    // sender to be CLI-style. See shouldBypassDnd().
    if (isQuiet(snapshot) || suppressForFullscreen(snapshot.urgency)) {
      // The toast never shows, so the only record a silenced notification
      // can leave is a history entry. Write it straight into history —
      // "what did I miss while silenced" is exactly what history is for.
      if (shouldArchiveSilenced(snapshot)) {
        writeSilenced(notification, snapshot)
        return
      }
      delete liveRefs[snapshot.originalId]
      delete liveKeys[snapshot.originalId]
      notification.tracked = false
      return
    }

    // A full persistence queue only costs restart survival; the popup still shows.
    persistPopupFile(snapshot)
    watchForUpdates(notification, snapshot)
    // Qt.callLater avoids "QV4::Object::insertMember" crashes when a
    // Repeater is mid-incubation while we mutate its model.
    Qt.callLater(function() {
      removePopupsByOriginalId(snapshot.originalId, NotificationLogic.popupFileName(snapshot))
      popupModel.insert(0, snapshot)
      // An update that arrived while the insert was deferred found no row to
      // write to, and a property that already changed will not change again.
      // Reading the object once the row exists catches up on it.
      service.refreshPopup(notification, snapshot.originalId, snapshot.timestamp)
      service.scheduleRebuild()
    })
  }

  // Persist a silenced notification, held tracked until its content is
  // stable: untracking tells the sender its notification closed (Chromium
  // then deletes its avatar file), and a replaces_id update lands on this
  // object without a second onNotification — releasing on a stale snapshot
  // would drop it. Each catch-up write reuses the original file identity.
  function writeSilenced(notification, written) {
    writeHistoryFile(written, function(ok) {
      // A rejected/cancelled job is terminal; never enqueue a catch-up retry on overload.
      if (!ok) { service.releaseSilenced(notification, written.originalId); return }
      var updated = null
      try {
        if (!NotificationLogic.notificationWithinLimits(notification)) {
          service.rejectNotification(notification, "Notification update exceeds sender limits")
          return
        }
        updated = NotificationLogic.replacementSnapshot(notification, written.originalId, written.timestamp)
      } catch (e) {
        // Torn down by the server while the write was queued.
      }
      if (updated && NotificationLogic.popupRowChanged(written, updated)) {
        service.writeSilenced(notification, updated)
        return
      }
      if (service.retainHistoryAction(written, notification)) {
        service.watchForUpdates(notification, written)
        return
      }
      service.releaseSilenced(notification, written.originalId)
    })
  }

  // Let go of a DND-silenced notification once its history write has run.
  // The id may have been reused and the object torn down meanwhile.
  function releaseSilenced(notification, originalId) {
    if (liveRefs[originalId] === notification) {
      delete retainedHistory[liveKeys[originalId]]
      delete liveKeys[originalId]
      delete liveRefs[originalId]
    }
    try {
      notification.tracked = false
    } catch (e) {
      // Object already destroyed by the server — nothing left to release.
    }
  }

  function reportPersistenceError(message) {
    if (lastError !== message) console.warn("foamy.notifications:", message)
    lastError = message
  }

  function rejectNotification(notification, message) {
    reportPersistenceError(message)
    var key = liveKeys[notification.id]
    if (liveRefs[notification.id] === notification) {
      cancelPendingWrites([key])
      for (var i = 0; i < popupModel.count; i++) {
        var row = popupModel.get(i)
        if (NotificationLogic.imageStem(row) === key) popupModel.setProperty(i, "actionsJson", "[]")
      }
      releaseSilenced(notification, notification.id)
      scheduleRebuild()
    } else notification.tracked = false
  }

  // Everything the card draws. A change to any of these is a client updating
  // the notification in place, which is the only kind of update we ever hear
  // about after the popup exists.
  readonly property var updateSignals: [
    "summaryChanged", "bodyChanged", "appNameChanged", "appIconChanged",
    "imageChanged", "urgencyChanged", "expireTimeoutChanged", "hintsChanged", "actionsChanged", "desktopEntryChanged"
  ]

  // A client that updates a notification through replaces_id does not produce
  // a second onNotification: the server writes the new content onto the object
  // we are already holding. The card draws a snapshot copied out of that
  // object — deliberately, since the object itself must stay out of the model
  // — so nothing reaches the screen until we copy it again.
  function watchForUpdates(notification, snapshot) {
    function refresh() {
      service.refreshPopup(notification, snapshot.originalId, snapshot.timestamp)
    }

    for (var i = 0; i < updateSignals.length; i++) {
      var signal = notification[updateSignals[i]]
      if (signal && typeof signal.connect === "function") signal.connect(refresh)
    }
  }

  function refreshPopup(notification, originalId, timestamp) {
    if (service.liveRefs[originalId] === notification && NotificationLogic.hasRawImage(notification)) {
      detachRawNotification(notification)
      return
    }
    if (busyKeys[String(timestamp) + "-" + String(originalId)] || handledKeys[String(timestamp) + "-" + String(originalId)]) return
    // A newer notification may have taken this id over, and the object may
    // outlive its popup — in both cases there is nothing here to refresh.
    if (service.liveRefs[originalId] !== notification) return
    if (!NotificationLogic.notificationWithinLimits(notification)) {
      rejectNotification(notification, "Notification update exceeds sender limits")
      return
    }

    var updated
    try {
      updated = NotificationLogic.replacementSnapshot(notification, originalId, timestamp)
    } catch (e) {
      // Object torn down by the server while the signal was in flight.
      return
    }

    var roles = NotificationLogic.popupRoles()
    for (var i = 0; i < popupModel.count; i++) {
      var row = popupModel.get(i)
      if (!row || row.originalId !== originalId || row.timestamp !== timestamp) continue
      if (!NotificationLogic.popupRowChanged(row, updated)) return
      for (var r = 0; r < roles.length; r++) popupModel.setProperty(i, roles[r], updated[roles[r]])
      // The file name is the timestamp and id this popup was persisted under,
      // so the rewrite lands on the same file: a restart restores the version
      // last shown, and so does the copy that ends up in history.
      lifetimes[NotificationLogic.imageStem(updated)] = durationFor(updated.urgency, updated.expireTimeout)
      persistPopupFile(updated)
      scheduleRebuild()
      return
    }
    var key = String(timestamp) + "-" + String(originalId)
    if (retainedHistory[key] && NotificationLogic.popupRowChanged(retainedHistory[key], updated)) {
      retainedHistory[key] = updated
      writeHistoryFile(updated)
    }
  }

  function retainHistoryAction(row, notification) {
    var key = NotificationLogic.imageStem(row)
    if (!centerEnabled || row.transient || handledKeys[key] || liveKeys[row.originalId] !== key
        || liveRefs[row.originalId] !== notification) return false
    try {
      if (NotificationLogic.hasRawImage(notification)) { detachRawNotification(notification); return false }
      if (!notification.tracked || !notification.actions.some(function(action) { return action.identifier === "default" })) return false
    } catch (e) { return false }
    retainedHistory[key] = Object.assign({}, row)
    // Bound live callbacks by the same count as popup history, even if the center is never opened.
    var keys = Object.keys(retainedHistory).sort(function(a, b) {
      return Number(retainedHistory[a].timestamp) - Number(retainedHistory[b].timestamp)
    })
    while (keys.length > historyLimit) releaseHistoryAction(keys.shift())
    return !!retainedHistory[key]
  }

  function releaseHistoryAction(key) {
    var row = retainedHistory[key] || {originalId:Number(key.split("-")[1])}
    delete retainedHistory[key]
    if (liveKeys[row.originalId] !== key) return
    var ref = liveRefs[row.originalId]
    delete liveKeys[row.originalId]
    delete liveRefs[row.originalId]
    try { if (ref && ref.tracked) ref.dismiss() }
    catch (e) { console.warn("foamy.notifications: retained notification already closed") }
  }

  function releaseHistoryKeys(keys) {
    cancelPendingWrites(keys)
    keys.forEach(function(key) { service.releaseHistoryAction(key) })
    // Removing history while a popup is visible must not retain its callback later.
    removeKeys(keys, "historyDismissed")
    deleteDeferredArchives(function(key) { return keys.indexOf(key) >= 0 })
    // The center removed these durably; Foamy drops its own copies (the center never deletes them).
    // The helper takes at most 100 keys per job.
    for (var i = 0; i < keys.length; i += 100) enqueueHelperJob("drop", {keys: keys.slice(i, i + 100)})
  }

  function releaseHistoryBefore(timestamp) {
    // Include silenced notifications whose history write is still queued.
    var keys = Object.keys(liveKeys).map(function(id) { return liveKeys[id] }).filter(function(key) {
      return Number(key.split("-")[0]) <= timestamp
    })
    // Every eligible key: a retained callback left behind could rewrite the history dropped below.
    releaseHistoryKeys(keys)
    deleteDeferredArchives(function(key) { return Number(key.split("-")[0]) <= timestamp })
    enqueueHelperJob("drop", {before: Math.floor(timestamp)})
  }

  // A restored row carries an id from the previous server generation, and
  // the new server hands out ids from 1 again — so a fresh notification
  // with the same originalId is a coincidence, not the same notification.
  // The timestamp (via the file name) disambiguates: it travels with the
  // row through every model and file round-trip.
  function isRestoredRow(row) {
    return !!row && !!restoredPopups[NotificationLogic.popupFileName(row)]
  }

  // A notification arriving under an originalId a popup on screen already
  // holds supersedes it, so that row leaves the screen. Its file is deleted
  // rather than archived: the row taking its place archives itself when it
  // goes, and history would otherwise hold two entries for what the sender
  // means as one notification.
  // keepFileName is the replacement's own file: a same-millisecond
  // replacement shares the replaced row's filename, and the new write is
  // already queued — deleting that path here would erase the replacement's
  // only file.
  function removePopupsByOriginalId(originalId, keepFileName) {
    for (var i = popupModel.count - 1; i >= 0; i--) {
      var row = popupModel.get(i)
      if (!row || row.originalId !== originalId) continue
      // Not a replaces_id match — see isRestoredRow. Removing it here
      // would silently kill a restored critical alert on an unrelated ping.
      if (isRestoredRow(row)) continue
      if (NotificationLogic.popupFileName(row) !== keepFileName) deletePopupFileFor(row)
      forgetNormalizedImages(row, NotificationLogic.popupFileName(row) === keepFileName)
      popupModel.remove(i)
    }
  }

  function dismissPopup(index) {
    removePopup(index, "dismiss")
  }

  function dismissGroup(group, fromCloseButton) {
    if (busyKeys[group.key]) return
    if (!fromCloseButton || !preferences.closeButtonRemovesHistory) {
      removeKeys(group.keys, "dismiss")
      return
    }
    var row = Object.assign({}, group)
    var keys = group.keys.slice()
    var busy = Object.assign({}, busyKeys); busy[row.key] = true; busyKeys = busy
    actionErrors = ({})
    lastError = ""
    // Commit removal before closing so queued writes cannot restore the group.
    // Closing never invokes a sender action or the app-focus fallback.
    commitHandled(row, keys)
  }

  function expirePopup(index) {
    removePopup(index, "expire")
  }

  function removePopup(index, reason) {
    if (index < 0 || index >= popupModel.count) return
    var entry = popupModel.get(index)
    forgetNormalizedImages(entry)
    var originalId = entry ? entry.originalId : -1
    // A restored row has no live server object, and its old-generation id
    // may meanwhile belong to a fresh notification — resolving liveRefs by
    // id would dismiss that unrelated notification at the server.
    var restored = isRestoredRow(entry)
    var ref = !restored && originalId >= 0 ? liveRefs[originalId] : null
    // The popup is leaving the screen — for any reason — so its file must not
    // survive to the next shell restart. It becomes the newest history entry
    // instead. Rows that never had a file (a history replay, the empty-history
    // placeholder) archive to nothing, which the move tolerates.
    if (entry) {
      if (reason !== "handled" && !handledKeys[NotificationLogic.imageStem(entry)]) archivePopupFileFor(entry)
      delete lifetimes[NotificationLogic.imageStem(entry)]
      if (restored) delete restoredPopups[NotificationLogic.popupFileName(entry)]
    }
    var retained = ref && (reason === "expire" || reason === "dismiss") && retainHistoryAction(entry, ref)
    popupModel.remove(index)
    scheduleRebuild()
    if (retained) return
    if (ref) {
      try {
        if (ref.tracked) {
          if (reason === "expire" && typeof ref.expire === "function") ref.expire()
          else ref.dismiss()
        }
      } catch (e) {
        // Object already torn down by the server — nothing to dismiss.
      }
    }
  }

  function clearPopups() {
    while (popupModel.count > 0) dismissPopup(0)
  }

  // Run the popup's click action, then dismiss. Omarchy's own toasts carry the
  // action as an argv vector in the `execArgv` role (see execArgvFromHints),
  // which the persistence files preserve, so restored toasts stay clickable.
  // Third-party clients register a libnotify action under the canonical
  // identifier "default" instead; that one only works while the sender is live.
  function invokePopupDefault(index) {
    if (index < 0 || index >= popupModel.count) return
    var key = NotificationLogic.imageStem(popupModel.get(index))
    var group = displayGroups.filter(function(g) { return g.keys.indexOf(key) >= 0 })[0]
    if (group) activateGroup(group, "default")
  }

  function invokeLiveAction(row, identifier) {
    var key = NotificationLogic.imageStem(row)
    var retained = retainedHistory[key] && liveKeys[row.originalId] === key
    var ref = (!isRestoredRow(row) || retained) ? liveRefs[row.originalId] : null
    try {
      if (ref && ref.actions) {
        for (var i = 0; i < ref.actions.length; i++) {
          if (ref.actions[i].identifier === identifier) { ref.actions[i].invoke(); return true }
        }
      }
    } catch(e) { console.warn("foamy.notifications: notification action is no longer available") }
    return false
  }

  function invokeCenterDefault(key) {
    if (!/^[0-9]+-[0-9]+$/.test(key)) return "invalid"
    if (busyKeys[key]) return "busy"
    // Resolve only current live rows by full identity; archived IDs can be reused.
    for (var i = 0; i < popupModel.count; i++) {
      var row = Object.assign({}, popupModel.get(i))
      if (NotificationLogic.imageStem(row) !== key || isRestoredRow(row)) continue
      // Only the current native identity may delegate its command. Restored
      // and archived command vectors never cross the center's click boundary.
      var ref = liveKeys[row.originalId] === key ? liveRefs[row.originalId] : null
      var argv = ref && ref.tracked ? NotificationLogic.parseExecArgv(row.execArgv) : null
      if (argv) {
        row.key = key
        var busy = Object.assign({}, busyKeys); busy[key] = true; busyKeys = busy
        commitHandled(row, [key], function() { Util.execArgv(argv) })
        return "invoked"
      }
      if (!invokeLiveAction(row, "default")) return "unavailable"
      row.key = key
      var busy = Object.assign({}, busyKeys); busy[key] = true; busyKeys = busy
      // Use normal handling to remove the popup and its persisted copy as well.
      commitHandled(row, [key])
      return "invoked"
    }
    var retained = retainedHistory[key]
    if (retained && liveKeys[retained.originalId] === key && invokeLiveAction(retained, "default")) {
      var retainedRow = Object.assign({}, retained, {key:key})
      var busy = Object.assign({}, busyKeys); busy[key] = true; busyKeys = busy
      commitHandled(retainedRow, [key])
      return "invoked"
    }
    return "unavailable"
  }

  function activateGroup(group, identifier) {
    if (busyKeys[group.key]) return
    var row = Object.assign({}, group)
    var keys = group.keys.slice()
    var busy = Object.assign({}, busyKeys); busy[row.key] = true; busyKeys = busy
    actionErrors = ({})
    lastError = ""
    var argv = identifier === "default" ? NotificationLogic.parseExecArgv(row.execArgv) : null
    if (argv) {
      // Shell commands can restart Quickshell. Persist handling before launching them.
      commitHandled(row, keys, function() { Util.execArgv(argv) })
      return
    }
    var invoked = invokeLiveAction(row, identifier)
    if (invoked) commitHandled(row, keys)
    else if (identifier === "default") {
      enqueueHelperJob("focus", {app:row.app, desktopEntry:row.desktopEntry, body:row.body, mappings:preferences.browserMappings}, function(focused) {
        // Failed focus must leave both popup persistence and center history retryable.
        if (focused) service.commitHandled(row, keys)
        else service.finishAction(row.key, "Could not identify the window. Configure browserMappings or dismiss this popup.")
      })
    } else finishAction(row.key, "This action is no longer available.")
  }

  function commitHandled(row, keys, beforeDismiss) {
    enqueueHelperJob("forget", {keys: keys}, function(ok) {
      if (!ok) { service.finishAction(row.key, "Could not remove this notification from history. Try again."); return }
      // Keep prior writes on failure; only durable handling may cancel later snapshots.
      service.cancelPendingWrites(keys)
      keys.forEach(function(key) { service.handledKeys[key] = true })
      service.pruneHandledKeys()
      service.notifyCenter(keys)
      if (beforeDismiss) beforeDismiss()
      service.removeKeys(keys, "handled")
      keys.forEach(function(key) { service.releaseHistoryAction(key) })
      service.finishAction(row.key, "")
    })
  }

  // handledKeys guards late refreshes of handled rows; keep the newest only.
  function pruneHandledKeys() {
    var keys = Object.keys(handledKeys)
    if (keys.length <= 512) return
    keys.sort(function(a, b) { return Number(a.split("-")[0]) - Number(b.split("-")[0]) })
    var next = ({})
    keys.slice(-256).forEach(function(key) { next[key] = true })
    handledKeys = next
  }

  // The handled journal is already durable (helper `forget`); this only updates
  // the center's list now. Contract: README "Notification Center contract".
  function notifyCenter(keys) {
    if (!centerEnabled) return
    peer.call("foamy.notification-center.store", "handled", [keys.join(",")])
  }

  function pokeCenter(what) {
    if (centerEnabled) peer.call("foamy.notification-center.store", "peerChanged", [what])
  }

  FoamyPeerIpc { id: peer }

  function finishAction(key, error) {
    var busy = Object.assign({}, busyKeys); delete busy[key]; busyKeys = busy
    var errors = Object.assign({}, actionErrors)
    if (error) errors[key] = error; else delete errors[key]
    actionErrors = errors
  }

  function removeKeys(keys, reason) {
    for (var i = popupModel.count - 1; i >= 0; i--)
      if (keys.indexOf(NotificationLogic.imageStem(popupModel.get(i))) >= 0) removePopup(i, reason)
  }

  // ---------------------------------------------------- popup persistence
  //
  // Mirror every on-screen popup to its own file under popupStateDir so
  // toasts survive shell restarts (notably the restart `omarchy-update`
  // performs). Writes, moves and deletes go through one serialized queue: a
  // burst of replaces_id updates must not race a single reused Process, and
  // ordering guarantees a delete issued after a write wins.

  // Popups restored from a previous shell process, keyed by their file
  // name (timestamp-originalId) since ids alone repeat across server
  // generations. The replaces_id handling and liveRefs lookups must not
  // match these rows against fresh notifications.
  property var restoredPopups: ({})

  // Jobs are { op, payload, done } for the long-lived helper (`serve`): one
  // python process for the whole session, fed newline-delimited JSON, one job
  // in flight. Reads are jobs too, so a replay sees the history that existed
  // when it was asked for: everything queued after it waits for it.
  property var popupFileQueue: []
  readonly property int popupQueueLimit: 128
  readonly property int popupWriteLimit: 64
  readonly property int popupQueueByteLimit: 1048576
  readonly property int popupWriteByteLimit: 786432
  readonly property int popupPayloadLimit: 131072

  property var runningJob: null
  property double nextJobId: 0
  property bool runningImageWrite: false

  function rejectPopupFileJob(job, message) {
    reportPersistenceError(message)
    if (job.done) job.done(false, "")
    return false
  }

  function enqueuePopupJob(job) {
    var payload = job.payload || "{}"
    // Bound before copying the queue. UTF-8 accounts for escaped and non-ASCII sender data.
    if (payload.length > popupPayloadLimit || NotificationLogic.utf8Bytes(payload) > popupPayloadLimit)
      return rejectPopupFileJob(job, "Notification operation exceeds the 128 KiB payload limit")
    job.bytes = NotificationLogic.utf8Bytes(payload) + 64
    var queue = popupFileQueue.slice()
    var last = queue.length ? queue[queue.length - 1] : null
    // Callback-bearing writes, reads, actions and cleanup remain ordering barriers.
    var replace = job.replaceKey && last && last.replaceKey === job.replaceKey
      && last.historyWrite === job.historyWrite && !last.done && !job.done
    if (replace) queue.pop()
    var bytes = job.bytes, writes = job.writeKey ? 1 : 0, writeBytes = job.writeKey ? job.bytes : 0
    for (var i = 0; i < queue.length; i++) {
      bytes += queue[i].bytes
      if (queue[i].writeKey) { writes++; writeBytes += queue[i].bytes }
    }
    // Reserve half the slots and 256 KiB for cleanup/actions during sender floods.
    if (queue.length >= popupQueueLimit || bytes > popupQueueByteLimit
        || (job.writeKey && (writes > popupWriteLimit || writeBytes > popupWriteByteLimit)))
      return rejectPopupFileJob(job, "Notification persistence queue is full; new work was rejected")
    queue.push(job)
    popupFileQueue = queue
    runNextPopupFileJob()
    return true
  }

  function enqueueHelperJob(op, data, done, replaceKey, historyWrite) {
    return enqueuePopupJob({op:op, payload:JSON.stringify(data || {}), done:done || null,
      replaceKey:replaceKey || "", historyWrite:!!historyWrite, writeKey:op === "write" ? (replaceKey || "") : ""})
  }

  function cancelPendingWrites(keys, historyOnly) {
    var kept = [], cancelled = []
    for (var i = 0; i < popupFileQueue.length; i++) {
      var job = popupFileQueue[i]
      if (job.writeKey && (!historyOnly || job.historyWrite) && keys.indexOf(job.writeKey) >= 0) cancelled.push(job)
      else kept.push(job)
    }
    // Remove references before callbacks run, so release/re-entrancy cannot revisit a cancelled job.
    popupFileQueue = kept
    for (var c = 0; c < cancelled.length; c++) if (cancelled[c].done) cancelled[c].done(false, "")
    runNextPopupFileJob()
  }

  function runNextPopupFileJob() {
    if (!runningJob && popupFileQueue.length === 0 && Object.keys(deferredMoves).length) {
      var moves = deferredMoves, archive = [], remove = []
      deferredMoves = ({})
      for (var key in moves) (moves[key] === "delete" ? remove : archive).push(key)
      var batch = []
      if (archive.length) batch.push({op:"archive", payload:JSON.stringify({keys:archive}), bytes:0})
      if (remove.length) batch.push({op:"delete", payload:JSON.stringify({keys:remove}), bytes:0})
      popupFileQueue = batch
    }
    if (runningJob || popupFileQueue.length === 0) return
    if (!helperProc.running) { if (!helperRestart.running) helperRestart.start(); return }
    var job = popupFileQueue[0]
    popupFileQueue = popupFileQueue.slice(1)
    job.id = ++nextJobId
    runningJob = job
    runningImageWrite = job.op === "write" && !!job.replaceKey && !job.historyWrite
    helperProc.write('{"id":' + job.id + ',"op":' + JSON.stringify(job.op) + ',"data":' + job.payload + '}\n')
  }

  function helperReplied(line) {
    var reply
    try { reply = JSON.parse(line) } catch (e) { console.warn("foamy.notifications: invalid helper reply"); return }
    var job = runningJob
    if (!job || reply.id !== job.id) return
    runningJob = null
    var ok = reply.ok === true
    var out = String(reply.out || "")
    if (!ok) { lastError = "Notification operation failed: " + String(reply.error || ""); console.warn(lastError) }
    if (ok && runningImageWrite) {
      try { acceptNormalizedImages(JSON.parse(out)) }
      catch (e) { lastError = "Invalid notification image result"; console.warn(lastError) }
    }
    runningImageWrite = false
    if (job.done) {
      try { job.done(ok, out) } catch (e) { console.warn("notifications: file job callback failed:", e) }
    }
    runNextPopupFileJob()
  }

  Process {
    id: helperProc
    command: ["python3", "-I", service.helper, "serve"]
    running: false
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { service.helperReplied(line) } }
    stderr: SplitParser { onRead: function(line) { if (String(line).trim()) console.warn(String(line).trim()) } }
    onRunningChanged: if (running) service.runNextPopupFileJob()
    onExited: function(exitCode, exitStatus) {
      var job = service.runningJob
      service.runningJob = null
      service.runningImageWrite = false
      service.reportPersistenceError("Notification helper stopped; restarting it")
      if (job && job.done) { try { job.done(false, "") } catch (e) {} }
      helperRestart.start()
    }
  }
  Timer { id: helperRestart; interval: 1000; onTriggered: if (!helperProc.running) helperProc.running = true }

  function persistPopupFile(snapshot) {
    var safeSnapshot = Features.redact(snapshot)
    var persistable = NotificationLogic.persistablePopup(safeSnapshot, imagesDir)
    var version = ++nextImageWriteVersion
    imageWriteVersions[NotificationLogic.imageStem(snapshot)] = version
    var accepted = enqueueHelperJob("write", {entry:persistable.entry, copies:persistable.copies, imageVersion:version, sources:{appIcon:String(safeSnapshot.appIcon || ""), image:String(safeSnapshot.image || "")}}, null, NotificationLogic.imageStem(snapshot))
    if (!accepted) delete imageWriteVersions[NotificationLogic.imageStem(snapshot)]
    return accepted
  }

  function deletePopupFileFor(row) {
    if (!row) return
    cancelPendingWrites([NotificationLogic.imageStem(row)])
    // History replays and the placeholder never had a file; the delete is a no-op there.
    var key = NotificationLogic.imageStem(row)
    if (!enqueueHelperJob("delete", {key: key})) deferMove(key, "delete")
  }

  // Moves and deletes refused by a full queue (a sender flood) are not lost:
  // they run as one batched job once the queue drains. Bounded; anything past
  // the bound is archived by the restore age check on the next start.
  property var deferredMoves: ({})
  function deferMove(key, op) {
    if (!deferredMoves[key] && Object.keys(deferredMoves).length >= 2048) return
    var next = Object.assign({}, deferredMoves)
    next[key] = op
    deferredMoves = next
  }

  // ---------------------------------------------------- history
  //
  // A popup that leaves the screen keeps its file — it just moves one level
  // down, into historyDir. Trimming happens right there in the same shell
  // job: the names sort numerically by their leading millisecond timestamp,
  // so everything but the newest historyLimit files is the tail to drop,
  // image copies included. Callers set $hist, $limit and $imgs first.
  function archivePopupFileFor(row) {
    if (!row) return
    var key = NotificationLogic.imageStem(row)
    // Its popup write has not run yet: write it straight into history instead,
    // so a flood never leaves a popup file behind and needs no second job.
    var converted = false
    for (var i = 0; i < popupFileQueue.length; i++) {
      var job = popupFileQueue[i]
      if (job.op !== "write" || job.writeKey !== key || job.historyWrite) continue
      var data = JSON.parse(job.payload)
      data.history = true
      delete data.sources
      delete data.imageVersion
      job.payload = JSON.stringify(data)
      job.historyWrite = true
      converted = true
    }
    if (converted) return
    // A replay or the placeholder has no file to move; the helper tolerates that.
    if (!enqueueHelperJob("archive", {key: key})) deferMove(key, "archive")
  }

  // Record a notification that never made it to the screen (DND silenced it),
  // straight into history. Same file format as an archived popup, so the
  // replay can't tell the two apart.
  //
  // With the center enabled, live default actions remain tracked after writing.
  // Other silenced notifications are released once their latest content is saved.
  function writeHistoryFile(entry, done) {
    if (!entry) { if (done) done(true); return }
    var persistable = NotificationLogic.persistablePopup(Features.redact(entry), imagesDir)
    return enqueueHelperJob("write", {entry:persistable.entry, copies:persistable.copies, history:true}, done, NotificationLogic.imageStem(entry), true)
  }

  function clearHistory() {
    var keys = Object.keys(liveKeys).map(function(id) { return liveKeys[id] })
    Object.keys(retainedHistory).forEach(function(key) { service.releaseHistoryAction(key) })
    cancelPendingWrites(keys, true)
    // A deferred archive would run after the clear and refill history.
    deleteDeferredArchives(function() { return true })
    enqueueHelperJob("clear", {})
  }

  // Deferred archives (refused by a full queue) run once the queue drains, so
  // after any history clear/drop queued meanwhile. Turn those of cleared keys
  // into plain deletes: the popup is gone and its history was just removed.
  function deleteDeferredArchives(matches) {
    var next = Object.assign({}, deferredMoves), changed = false
    for (var key in next) if (next[key] === "archive" && matches(key)) { next[key] = "delete"; changed = true }
    if (changed) deferredMoves = next
  }

  // A restart can kill a job between its image copy and its JSON write.
  // Swept at startup, through the queue so in-flight copies aren't mistaken for orphans.
  function sweepOrphanImages() {
    enqueueHelperJob("sweep", {})
  }

  // Toasts that were on screen when the replay was asked for. The clear in
  // replayHistory archives them, but the directory read is already in flight
  // by then, so they're handed over in memory instead of being waited for.
  property var replayCarryOver: []

  // Set from the moment a read is queued until it starts, so a second
  // showHistory while one is still waiting its turn doesn't queue another.
  property bool historyReadQueued: false

  // Re-show what's in historyDir as toasts. The read goes through the file
  // queue and its own subprocess, so the replay lands in replayHistory once
  // the work queued ahead of it has finished.
  function showRecentHistory() {
    if (service.historyReadQueued) return "ok"
    service.replayCarryOver = liveRowsForReplay()
    service.historyReadQueued = true
    var queued = enqueueHelperJob("read", {history: true}, function(ok, out) {
      service.historyReadQueued = false
      service.replayHistory(ok ? out : "")
    })
    if (!queued) {
      service.historyReadQueued = false
      service.replayCarryOver = []
      return "busy"
    }
    return "ok"
  }

  // Copy the on-screen rows out of the model. The placeholder from an earlier
  // empty replay carries originalId -1 and is not a notification, so it is
  // left behind rather than replayed as one. The replay dismisses these
  // notifications, and senders delete their images on close — so the carried
  // rows point at the persisted copies, like the archived files they join.
  function liveRowsForReplay() {
    var rows = []
    for (var i = 0; i < popupModel.count; i++) {
      var row = popupModel.get(i)
      if (!row || row.originalId < 0) continue
      rows.push({
        id: row.id,
        originalId: row.originalId,
        app: row.app,
        appIcon: popupImageFor(row, "appIcon"),
        summary: row.summary,
        body: row.body,
        image: popupImageFor(row, "image"),
        glyph: row.glyph || "",
        execArgv: row.execArgv || "",
        execActions: row.execActions || "",
        urgency: row.urgency,
        timestamp: row.timestamp
      })
    }
    return rows
  }

  function replayHistory(raw) {
    var rows = NotificationLogic.historyRows(
      raw, service.replayCarryOver, NotificationUrgency.Normal, service.historyLimit)
    service.replayCarryOver = []

    // Replaying nothing at all looks like a dead keybinding, so say so.
    if (rows.length === 0) {
      popupModel.insert(0, {
        id: -1,
        originalId: -1,
        app: "omarchy-action",
        appIcon: "",
        summary: "No recent notifications",
        body: "",
        image: "",
        glyph: "\u{f009a}",
        execArgv: "",
        execActions: "",
        urgency: NotificationUrgency.Low,
        expireTimeout: 0,
        timestamp: Date.now()
      })
      return
    }

    clearPopups()
    // Rows arrive newest-first, and index 0 is the top of the toast stack.
    for (var i = 0; i < rows.length; i++) {
      // Replayed rows are restored rows: their notification died with the
      // sender long ago, so they must never resolve to a live server object
      // that has since been handed their old id.
      service.restoredPopups[NotificationLogic.popupFileName(rows[i])] = true
      rows[i].actionsJson = "[]"
      popupModel.append(rows[i])
    }
  }

  function restorePopups(raw) {
    var entries = NotificationLogic.parsePopupFiles(raw, NotificationUrgency.Normal)
    var now = Date.now()
    var live = []
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i]
      var duration = durationFor(entry.urgency, entry.expireTimeout)
      if (NotificationLogic.popupExpired(entry, duration, now) || now - entry.timestamp > preferences.restoreMaxAgeSec * 1000 || live.length >= 100) {
        // It would have expired on screen had the shell kept running, so it
        // gets archived exactly like an expiry that happened while it did.
        archivePopupFileFor(entry)
        continue
      }
      // Survivors restart with a full lifetime on purpose: shell restarts
      // are rare, and a full look after the restart flicker beats resuming
      // a toast with a second left on its clock. The reset is persisted as
      // an absolute deadline so a second restart while the toast is still
      // on screen judges it by the reset clock, not the original timestamp.
      if (duration > 0) {
        entry.deadline = now + duration
        persistPopupFile(entry)
        // deadline is persistence metadata, not a model role — fresh rows
        // never carry it, and ListModel roles must stay consistent.
        delete entry.deadline
      }
      live.push(entry)
    }
    if (live.length === 0) return

    Qt.callLater(function() {
      for (var j = 0; j < live.length; j++) {
        var restored = live[j]
        // A notification received while the restore was reading the dir can
        // already occupy this originalId with the same timestamp — then it
        // IS this entry, live with its own file, and must be left alone. A
        // different timestamp is indistinguishable between a genuine
        // cross-restart replaces_id and a new-generation id coincidence, so
        // show both: a briefly duplicated toast beats silently dropping a
        // restored critical alert.
        var duplicate = false
        for (var k = 0; k < popupModel.count; k++) {
          var row = popupModel.get(k)
          if (row && row.originalId === restored.originalId && row.timestamp === restored.timestamp) {
            duplicate = true
            break
          }
        }
        if (duplicate) continue
        // Append (entries are newest-first) so restored toasts stack in
        // their original order below anything that just arrived. Restored
        // popups have no liveRefs entry — the server object died with the
        // old shell — so dismissal and action fallbacks degrade gracefully.
        service.restoredPopups[NotificationLogic.popupFileName(restored)] = true
        popupModel.append(restored)
      }
    })
  }

  // ---------------------------------------------------- settings persistence

  FileView {
    id: settingsFile
    // Bound only after the one-time migration has carried DND over.
    path: service.stateMigrated ? service.settingsPath : ""
    watchChanges: false
    atomicWrites: true
    printErrors: false
    onLoaded: service.loadSettings(text())
    // First-run: the file doesn't exist yet. Without this branch,
    // `settingsLoaded` stays false forever and `scheduleSettingsSave` becomes
    // a no-op — so the file is never created and the DND preference vanishes
    // on shell restart.
    onLoadFailed: service.loadSettings("")
  }

  Timer {
    id: settingsSaveTimer
    interval: 200
    repeat: false
    onTriggered: service.flushSettings()
  }

  function scheduleSettingsSave() {
    if (!service.settingsLoaded) return
    settingsSaveTimer.restart()
  }

  property bool settingsLoaded: false

  function loadSettings(raw) {
    // FileView can fire onLoaded more than once during startup — the implicit
    // preload when `path` resolves, plus the explicit `settingsFile.reload()`
    // in Component.onCompleted can both end up calling here.
    if (service.settingsLoaded || !service.stateMigrated) return

    var parsed = NotificationLogic.parseSettings(raw)
    if (parsed.error) console.warn("notifications: settings parse failed:", parsed.errorMessage || "")

    if (parsed.dnd !== null) {
      service._hydrating = true
      persisted.doNotDisturb = parsed.dnd
      service._hydrating = false
    }

    try { service.snoozes = Features.quietState(JSON.parse(raw).snoozes, Date.now()) } catch (e) { service.snoozes = ({}) }
    service.settingsLoaded = true
    // Versions before the history moved into its own directory kept every
    // notification in here. Rewrite once so that dead payload doesn't sit in
    // the file until the next DND toggle happens to clear it.
    if (parsed.legacy) service.scheduleSettingsSave()
  }

  function flushSettings() {
    settingsFile.setText(JSON.stringify({ version: 5, dnd: persisted.doNotDisturb, snoozes: snoozes }, null, 2) + "\n")
  }

  Component.onCompleted: {
    configFile.reload()
    refreshContext()
    helperProc.running = true
    // Ordering: migrate the 4.0 layout first, then load DND, sweep orphans and
    // restore popups, all through the one queue, then tell the center.
    enqueueHelperJob("migrate", {}, function(ok) {
      if (!ok) service.reportPersistenceError("Could not prepare the notification state directory")
      service.stateMigrated = true
      service.sweepOrphanImages()
      service.enqueueHelperJob("read", {}, function(readOk, out) {
        if (readOk) service.restorePopups(out)
        service.storeReady = true
        service.pokeCenter("store")
      })
    })
  }

  Component.onDestruction: if (settingsLoaded) flushSettings()

  // ---------------------------------------------------- IPC

  // Keybindings run these directly when the shell resolves a service for them.
  function runShortcut(method) {
    if (typeof ipcHandler[method] !== "function" || ipcHandler[method].length !== 0) return false
    ipcHandler[method]()
    return true
  }

  // Stock-compatible target (4.0 and 4.5-rc2), so Omarchy's bindings and
  // scripts work: SUPER+comma runs `act`, SUPER+CTRL+comma runs `keyboard`.
  FoamyIpc {
    id: ipcHandler
    target: "notifications"

    function snoozeAll(minutes: string): string { return service.snoozeSource("*", minutes) }
    function unsnooze(key: string): string {
      if (!key) return service.resumeSnoozes()
      var next = Object.assign({}, service.snoozes)
      delete next[key]
      service.snoozes = next
      service.scheduleSettingsSave()
      return "ok"
    }
    function snoozes(): string { return JSON.stringify(service.snoozes) }

    function dndState(): string {
      return service.doNotDisturb ? "on" : "off"
    }

    function toggleDnd(): string {
      service.setDoNotDisturb(!service.doNotDisturb)
      return dndState()
    }

    function setDnd(value: string): string {
      var v = String(value || "").toLowerCase()
      var on = v === "true" || v === "1" || v === "on" || v === "yes"
      service.setDoNotDisturb(on)
      return dndState()
    }

    function isDnd(): string {
      return dndState()
    }

    // Dismiss, press the one button on, or click the newest card; with more
    // than one choice it hands the keyboard to the notifications.
    function act(): string { return service.actOnNewest() ? "ok" : "none" }

    // Arrows/j/k/Tab move, Enter clicks, Delete dismisses, 1-9 press a button, Escape gives the keyboard back.
    function keyboard(): string {
      if (service.keyboardMode) { service.stopKeyboard(); return "off" }
      return service.startKeyboard("") ? "on" : "none"
    }

    // Press button n (from 1) on the newest card.
    function invokeAction(n: int): string {
      var group = service.newestGroup()
      return group && service.runDeed(group, n - 1) ? "ok" : "none"
    }

    function count(): string { return String(service.displayGroups.length) }

    // Hold every card as hovering would (timers pause), or release them.
    function expand(): string {
      service.expanded = !service.expanded && service.displayGroups.length > 0
      return service.expanded ? "expanded" : "collapsed"
    }

    function probe(): string { return JSON.stringify(service.probeState()) }

    // Replay the notifications that have been moved into the history dir.
    function showHistory(): string {
      return service.showRecentHistory()
    }

    // `clear` forgets the recorded history; the toasts on screen stay put.
    function clear(): string {
      service.clearHistory()
      return "ok"
    }

    function dismissAll(): string {
      service.clearPopups()
      return "ok"
    }

    // Dismiss the most recent popup.
    function dismissOne(): string {
      if (popupModel.count === 0) return "none"
      service.dismissPopup(0)
      return "ok"
    }

    // Fire the default action on the most recent popup, then dismiss it.
    function invokeLast(): string {
      if (popupModel.count === 0) return "none"
      service.invokePopupDefault(0)
      return "ok"
    }

    // Take a toast off the screen by summary substring, used by the
    // first-run notifications once their action has been clicked.
    function dismiss(summary: string): string {
      var needle = String(summary || "")
      if (!needle) return "none"
      var hit = false
      for (var i = popupModel.count - 1; i >= 0; i--) {
        var row = popupModel.get(i)
        if (row && String(row.summary || "").indexOf(needle) !== -1) {
          service.dismissPopup(i)
          hit = true
        }
      }
      return hit ? "ok" : "none"
    }

    function ping(): string { return "ok" }
  }

  // ---------------------------------------------------- server

  NotificationServer {
    id: server
    keepOnReload: false
    imageSupported: true
    actionsSupported: true
    bodyMarkupSupported: true
    bodyHyperlinksSupported: false
    persistenceSupported: true

    onNotification: function(notification) {
      service.receiveNotification(notification)
    }
  }

  // A single clock owns expiry even when the same cards appear on several monitors.
  property var displayGroups: []
  property var lifetimes: ({})
  property var hoveredKeys: ({})
  property var busyKeys: ({})
  property var actionErrors: ({})
  property var handledKeys: ({})
  property bool centerEnabled: false
  onCenterEnabledChanged: {
    if (!centerEnabled) Object.keys(retainedHistory).forEach(function(key) { service.releaseHistoryAction(key) })
  }
  property bool rebuildPending: false
  property var pendingNotifications: []
  property var fullscreenMonitors: []
  property string pointerMonitor: ""
  readonly property string focusedMonitor: Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
  readonly property var screenNames: Quickshell.screens.map(function(s) { return s.name })
  readonly property var targetMonitors: Preferences.targetNames(preferences, screenNames, focusedMonitor, pointerMonitor)
  onTargetMonitorsChanged: scheduleRebuild()

  function scheduleRebuild() {
    if (rebuildPending) return
    rebuildPending = true
    Qt.callLater(function() { service.rebuildPending = false; service.rebuildGroups() })
  }

  BrowserIcons { id: browserIcons; onUpdated: service.scheduleRebuild() }

  function rebuildGroups() {
    var rows = []
    for (var i = 0; i < popupModel.count; i++) rows.push(Object.assign({}, popupModel.get(i)))
    var groups = Preferences.groups(rows, preferences.groupDuplicates, preferences.browserGrouping, BrowserIdentity)
    var retained = []
    for (var g = 0; g < groups.length; g++) {
      var group = groups[g]
      if (g >= preferences.maxVisible || suppressForFullscreen(group.urgency)) {
        removeKeys(group.keys, "dismiss")
      } else {
        if (group.keys.length > 100) {
          removeKeys(group.keys.slice(100), "dismiss")
          group.keys = group.keys.slice(0,100)
        }
        group.favicon = preferences.useBrowserFavicons ? browserIcons.source(group) : ""
        retained.push(group)
        for (var k = 0; k < group.keys.length; k++) {
          if (lifetimes[group.keys[k]] === undefined) lifetimes[group.keys[k]] = durationFor(group.urgency, group.expireTimeout)
        }
      }
    }
    displayGroups = retained
    if (preferences.useBrowserFavicons) browserIcons.warm(retained)
  }

  function remainingFor(group) {
    if (!group) return 0
    // A duplicate stack stays visible until its last member expires.
    return group.keys.reduce(function(remaining, key) {
      return Math.max(remaining, Number(service.lifetimes[key]) || 0)
    }, 0)
  }

  function suppressForFullscreen(urgency) {
    if (!preferences.suppressFullscreen || (urgency === 2 && preferences.fullscreenCritical)) return false
    // In all-monitor mode, a fullscreen output is hidden independently; other outputs remain usable.
    return targetMonitors.length > 0 && targetMonitors.every(function(name) { return fullscreenMonitors.indexOf(name) >= 0 })
  }

  function receiveNotification(notification) {
    if (!NotificationLogic.notificationWithinLimits(notification)) {
      rejectNotification(notification, "Notification exceeds sender limits")
      return
    }
    if (NotificationLogic.hasRawImage(notification)) { detachRawNotification(notification); return }
    // Watch immediately, including DND writes and native objects awaiting monitor context.
    notification.imageChanged.connect(function() {
      // Another property signal may have already detached this grouped replacement.
      if (!notification.tracked || !NotificationLogic.hasRawImage(notification)) return
      if (!NotificationLogic.notificationWithinLimits(notification)) {
        service.rejectNotification(notification, "Notification update exceeds sender limits")
        return
      }
      service.detachRawNotification(notification)
    })
    if (!preferences.suppressFullscreen && preferences.monitor !== "pointer") { handleNotification(notification); return }
    notification.tracked = true
    // Retain live objects only briefly while the compositor snapshot is in flight.
    if (pendingNotifications.length >= 100) {
      rejectNotification(notification, "Notification monitor-context backlog is full")
      return
    }
    pendingNotifications = pendingNotifications.concat([{notification:notification}])
    refreshContext()
  }

  function refreshContext() {
    if (preferences.suppressFullscreen || preferences.monitor === "pointer") contextRefresh.restart()
  }

  Timer {
    id: contextRefresh
    interval: 50
    onTriggered: {
      if (contextProc.running) { restart(); return }
      contextProc.stdinEnabled = true
      contextProc.running = true
    }
  }
  Process {
    id: contextProc
    command: ["python3", service.helper, "context"]
    stdinEnabled: true
    onStarted: { write(JSON.stringify({fullscreen:service.preferences.suppressFullscreen,pointer:service.preferences.monitor === "pointer"})); stdinEnabled = false }
    stdout: StdioCollector { id: contextOutput }
    stderr: StdioCollector {}
    onExited: function(exitCode, exitStatus) {
      if (exitCode === 0 && exitStatus === 0) {
        try {
          var data = JSON.parse(contextOutput.text)
          service.fullscreenMonitors = data.fullscreen
          service.pointerMonitor = data.pointer
        } catch(e) { service.lastError = "Cannot read monitor state" }
      } else { service.lastError = "Cannot query monitor state; notifications use the last known monitor"; console.warn(service.lastError) }
      var pending = service.pendingNotifications; service.pendingNotifications = []
      for (var i = 0; i < pending.length; i++) {
        try {
          if (pending[i].snapshot) service.handleDetachedSnapshot(pending[i].snapshot, false)
          else service.handleNotification(pending[i].notification)
        } catch(e) { console.warn("foamy.notifications: sender closed before delivery") }
      }
      service.scheduleRebuild()
    }
  }
  Connections {
    target: Hyprland
    function onRawEvent(event) {
      // Monitor context matters only while something is shown or waiting; arrivals refresh it themselves.
      if ((service.pendingNotifications.length || service.displayGroups.length)
          && ["fullscreen","activewindowv2","workspacev2","focusedmon","monitoradded","monitorremoved","activespecial"].indexOf(event.name) >= 0) service.refreshContext()
    }
  }

  // One clock keeps expiry and the line in sync without per-card timers. It
  // runs per frame only while the timeout line is drawn and motion is allowed;
  // otherwise a 200 ms tick is enough for expiry.
  readonly property bool anyTimed: displayGroups.some(function(group) {
    return service.durationFor(group.urgency, group.expireTimeout) > 0
  })
  readonly property bool smoothClock: anyTimed && preferences.showTimeoutIndicator && !FoamyMotion.reduceMotion
  FrameAnimation {
    running: service.smoothClock
    onTriggered: service.advanceLifetimes(frameTime * 1000)
  }
  Timer {
    interval: 200
    repeat: true
    running: service.anyTimed && !service.smoothClock
    property double last: 0
    onRunningChanged: last = Date.now()
    onTriggered: { var now = Date.now(); service.advanceLifetimes(now - last); last = now }
  }

  function advanceLifetimes(elapsedMs) {
    if (elapsedMs <= 0 || keyboardMode || expanded) return
    var expired = [], advanced = false
    var hoverKeys = Object.keys(hoveredKeys)
    for (var i = 0; i < displayGroups.length; i++) {
      var group = displayGroups[i]
      var hovered = hoverKeys.some(function(k) { return k.endsWith("|" + group.key) && service.hoveredKeys[k] })
      if (hovered || busyKeys[group.key]) continue
      if (durationFor(group.urgency, group.expireTimeout) <= 0) continue
      for (var j = 0; j < group.keys.length; j++) {
        var key = group.keys[j]
        lifetimes[key] -= elapsedMs
        advanced = true
        if (lifetimes[key] <= 0) expired.push(key)
      }
    }
    // In-place map updates need one signal per tick for the card bindings.
    if (advanced) lifetimesChanged()
    if (expired.length) removeKeys(expired, "expire")
  }

  // ---------------------------------------------------- keyboard, act, probe
  //
  // Like stock 4.5: notifications never take the keyboard on their own. The
  // keybinding (SUPER+CTRL+comma, or `act` with several choices) starts this;
  // Escape, a choice, a click or ten idle seconds give the keyboard back.
  property bool keyboardMode: false
  property string keyboardKey: ""
  property bool expanded: false
  onDisplayGroupsChanged: {
    if (!displayGroups.length) expanded = false
    if (keyboardMode && !groupFor(keyboardKey)) {
      if (displayGroups.length) keyboardKey = displayGroups[0].key
      else stopKeyboard()
    }
  }
  Timer { id: keyboardIdle; interval: 10000; onTriggered: service.stopKeyboard() }

  function groupFor(key) {
    for (var i = 0; i < displayGroups.length; i++) if (displayGroups[i].key === key) return displayGroups[i]
    return null
  }
  function newestGroup() { return displayGroups.length ? displayGroups[0] : null }

  // The card's buttons in the order it draws and numbers them.
  function deedsFor(group) {
    return group ? NotificationLogic.deeds(group, Features.offers(group)) : []
  }

  // Whether a click does something of the card's own (not just app focus).
  function clickable(group) {
    if (!group) return false
    if (NotificationLogic.parseExecArgv(group.execArgv)) return true
    var ref = !isRestoredRow(group) ? liveRefs[group.originalId] : null
    try {
      return !!(ref && ref.tracked && ref.actions && ref.actions.some(function(a) { return a.identifier === "default" }))
    } catch (e) { return false }
  }

  function runDeed(group, index) {
    var deed = deedsFor(group)[index]
    if (!deed || busyKeys[group.key]) return false
    if (deed.kind === "action") activateGroup(group, deed.value)
    else featureAction(group, deed.kind, deed.value)
    return true
  }

  function actOnNewest() {
    var group = newestGroup()
    if (!group) return false
    var deeds = deedsFor(group).length
    var click = clickable(group)
    if (deeds + (click ? 1 : 0) > 1) startKeyboard(group.key)
    else if (click) activateGroup(group, "default")
    else if (deeds) runDeed(group, 0)
    else dismissGroup(group, false)
    return true
  }

  // Whether an output draws this card: a fullscreen output (with fullscreen
  // suppression) only shows critical cards when those are allowed through.
  function showsGroupOn(name, group) {
    if (targetMonitors.indexOf(name) < 0) return false
    var fullscreen = preferences.suppressFullscreen && fullscreenMonitors.indexOf(name) >= 0
    return !fullscreen || (group.urgency === 2 && preferences.fullscreenCritical)
  }
  function screenShowing(group) {
    if (!group) return ""
    for (var i = 0; i < targetMonitors.length; i++) if (showsGroupOn(targetMonitors[i], group)) return targetMonitors[i]
    return ""
  }
  readonly property string keyboardScreen: keyboardMode ? screenShowing(groupFor(keyboardKey)) : ""

  function startKeyboard(key) {
    var shown = displayGroups.filter(function(group) { return service.screenShowing(group) !== "" })
    if (!shown.length) return false
    keyboardMode = true
    keyboardKey = groupFor(key) && screenShowing(groupFor(key)) ? key : shown[0].key
    keyboardIdle.restart()
    return true
  }

  function stopKeyboard() {
    keyboardIdle.stop()
    keyboardMode = false
    keyboardKey = ""
  }

  function moveSelection(step) {
    // Only cards some output draws: the keyboard never lands on a hidden one.
    for (var index = displayGroups.findIndex(function(group) { return group.key === service.keyboardKey }) + step;
         index >= 0 && index < displayGroups.length; index += step) {
      if (screenShowing(displayGroups[index])) { keyboardKey = displayGroups[index].key; return }
    }
  }

  // True when the key was ours.
  function keyboardPressed(key, text) {
    if (!keyboardMode) return false
    keyboardIdle.restart()
    var group = groupFor(keyboardKey)
    if (key === Qt.Key_Escape) stopKeyboard()
    else if (key === Qt.Key_Down || key === Qt.Key_J || key === Qt.Key_Tab) moveSelection(1)
    else if (key === Qt.Key_Up || key === Qt.Key_K || key === Qt.Key_Backtab) moveSelection(-1)
    else if (key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space) { stopKeyboard(); if (group) activateGroup(group, "default") }
    else if (key === Qt.Key_Delete || key === Qt.Key_Backspace) { if (group) dismissGroup(group, true) }
    else if (/^[1-9]$/.test(text)) {
      if (group && Number(text) <= deedsFor(group).length) { stopKeyboard(); runDeed(group, Number(text) - 1) }
    } else return false
    return true
  }

  function probeState() {
    return { toasts: displayGroups.length, popups: popupModel.count, doNotDisturb: doNotDisturb, expanded: expanded,
      keyboardMode: keyboardMode, keyboardKey: keyboardKey, displays: screenNames, focusedDisplay: focusedMonitor,
      targetDisplay: targetMonitors.length ? targetMonitors[0] : "", notificationDisplays: targetMonitors,
      fullscreenMonitors: fullscreenMonitors, storeReady: storeReady }
  }

  // ---------------------------------------------------- preferences (owned here)
  //
  // The center's settings page asks over IPC; Foamy writes its own entry.
  readonly property var centerPreferenceKeys: ["offerSnoozeWhenSharing", "dndSilenceCritical", "groupDuplicates", "useBrowserFavicons",
    "compact", "showImages", "closeButtonRemovesHistory", "normalTimeoutSec"]
  property string preferenceError: ""
  function savePreference(key, valueJson) {
    if (centerPreferenceKeys.indexOf(key) < 0) return "error: unknown setting"
    var value
    try { value = JSON.parse(String(valueJson || "").trim()) } catch (e) { return "error: invalid value" }
    var probe = {}; probe[key] = value; probe.id = "foamy.notifications"
    if (Preferences.parse(JSON.stringify({plugins: [probe]})).error) return "error: invalid value"
    if (prefProc.running) return "error: busy"
    preferenceError = ""
    prefProc.command = ["python3", "-I", Qt.resolvedUrl("bin/notification-preferences.py").toString().replace(/^file:\/\//, ""), "save", key, JSON.stringify(value)]
    prefProc.running = true
    return "saving"
  }
  Process {
    id: prefProc
    stdout: StdioCollector { id: prefOutput }
    stderr: StdioCollector {}
    onExited: function(code, status) {
      var result = null
      try { result = JSON.parse(prefOutput.text) } catch (e) {}
      service.preferenceError = code === 0 && status === 0 && result && result.ok ? "" : (result && result.error ? String(result.error) : "Could not save the setting")
      service.pokeCenter("preferences")
    }
  }

  FoamyIpc {
    target: "foamy.notifications"
    // Where the center reads Foamy's files (read-only). See README contract.
    function storeInfo(): string {
      return JSON.stringify({layout:"foamy-files", version:2, ready:service.storeReady,
        popups:service.popupStateDir.replace(/\/$/, ""), history:service.historyDir.replace(/\/$/, ""),
        handled:service.handledPath, state:service.settingsPath})
    }
    function preferences(): string {
      return JSON.stringify({settings:service.preferences, error:service.configError,
        saving:prefProc.running, saveError:service.preferenceError})
    }
    // Foamy writes its own shell.json entry; the center only asks.
    function setPreference(key: string, valueJson: string): string { return service.savePreference(key, valueJson) }
    function invokeDefault(key: string): string { return service.invokeCenterDefault(key) }
    function releaseHistory(keysCsv: string): string {
      var keys = keysCsv.split(",")
      if (keys.length > 100 || keys.some(function(key) { return !/^[0-9]+-[0-9]+$/.test(key) })) return "invalid"
      service.releaseHistoryKeys(keys)
      return "ok"
    }
    function releaseHistoryBefore(timestamp: string): string {
      if (!/^[0-9]{1,16}$/.test(timestamp)) return "invalid"
      service.releaseHistoryBefore(Number(timestamp))
      return "ok"
    }
    function invokeAction(key: string, identifier: string): string {
      var group = service.displayGroups.filter(function(g) { return g.key === key })[0]
      if (!group) return "none"
      service.activateGroup(group, identifier)
      return "ok"
    }
    function state(): string {
      return JSON.stringify({groups:service.displayGroups.map(function(g){return {key:g.key,count:g.keys.length,actions:JSON.parse(g.actionsJson || "[]").length}}),
        retainedActions:Object.keys(service.retainedHistory).length, pending:pendingNotifications.length, contextBusy:contextProc.running, popups:popupModel.count, doNotDisturb:service.doNotDisturb, monitors:service.targetMonitors,
        fullscreenMonitors:service.fullscreenMonitors, settings:service.preferences,
        snoozes:service.snoozes, sharingActive:service.sharingActive, sharingOfferPending:service.sharingOfferPending,
        centerEnabled:service.centerEnabled,configError:service.configError,lastError:service.lastError,
        queuedJobs:service.popupFileQueue.length, queuedBytes:service.popupFileQueue.reduce(function(total,job){return total+job.bytes},0),
        tracked:Object.keys(service.liveRefs).length, busy:service.runningJob !== null || service.popupFileQueue.length>0,
        keyboardMode:service.keyboardMode, expanded:service.expanded, storeReady:service.storeReady})
    }
  }

  Variants {
    model: service.sharingOfferPending ? Quickshell.screens.filter(function(screen) { return screen.name === service.sharingPromptScreen }).slice(0, 1) : []
    PanelWindow {
      required property var modelData
      screen: modelData
      anchors { top: true; right: true }
      margins { top: service.barClearance + Style.space(8); right: Style.space(8) }
      implicitWidth: Style.space(390)
      implicitHeight: sharingCard.implicitHeight
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.namespace: "foamy-notification-sharing"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      NotificationCard {
        id: sharingCard
        onImplicitHeightChanged: service.sharingPromptHeight = implicitHeight
        Component.onCompleted: service.sharingPromptHeight = implicitHeight
        width: parent.width
        app: FoamyI18n.t("Notifications")
        summary: FoamyI18n.t("Screen sharing is active")
        body: FoamyI18n.t("Snooze notifications while you share?")
        deeds: [{kind:"action", value:"snooze", text:FoamyI18n.t("Snooze for 30 minutes")}, {kind:"action", value:"keep", text:FoamyI18n.t("Keep notifications")}]
        onCloseRequested: service.sharingOfferHandled = true
        onHideRequested: service.sharingOfferHandled = true
        onCardClicked: service.sharingOfferHandled = true
        onActionRequested: function(identifier) {
          service.sharingOfferHandled = true
          if (identifier === "snooze") service.snoozeSource("*", 30)
        }
      }
    }
  }

  Variants {
    model: Quickshell.screens
    PanelWindow {
      id: popupWindow
      required property var modelData
      screen: modelData
      readonly property bool selected: service.targetMonitors.indexOf(screen.name) >= 0
      // Keep the surface mapped until the final card finishes fading out.
      visible: selected && (popupColumn.groups.length > 0 || popupColumn.implicitHeight > 0)
      WlrLayershell.namespace: "omarchy-notifications"
      WlrLayershell.layer: WlrLayer.Overlay
      // The output that actually shows the selected card owns the keyboard.
      readonly property bool takesKeyboard: service.keyboardMode && service.keyboardScreen === screen.name
      WlrLayershell.keyboardFocus: takesKeyboard ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
      exclusionMode: ExclusionMode.Ignore
      color: "transparent"
      anchors { top: true; bottom: true; left: true; right: true }
      mask: Region { item: popupColumn }
      readonly property var placement: NotificationLogic.popupPlacement(service.barPosition,service.barClearance,Style.gapsOut)
      function fitCards() {
        if (!selected || popupColumn.implicitHeight <= height - popupColumn.y - Style.space(8) || service.displayGroups.length < 2) return
        var last = service.displayGroups[service.displayGroups.length - 1]
        service.removeKeys(last.keys,"dismiss")
      }
      onHeightChanged: Qt.callLater(fitCards)
      Item {
        focus: popupWindow.takesKeyboard
        Keys.onPressed: function(event) { event.accepted = service.keyboardPressed(event.key, event.text) }
      }
      NotificationStack {
        id: popupColumn
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: popupWindow.placement.margins.top + Style.space(8) + (service.sharingOfferPending && popupWindow.screen.name === service.sharingPromptScreen ? service.sharingPromptHeight + Style.space(8) : 0)
        anchors.rightMargin: popupWindow.placement.margins.right + Style.space(8)
        width: Math.max(1,Math.min(Style.space(service.preferences.width),popupWindow.width - anchors.rightMargin - Style.space(8)))
        spacing: Style.space(8)
        groups: service.displayGroups.filter(function(group) { return service.showsGroupOn(popupWindow.screen.name, group) })
        onImplicitHeightChanged: Qt.callLater(popupWindow.fitCards)
        delegate:
          NotificationCard {
            id: card
            required property string entryJson
            readonly property var modelData: JSON.parse(entryJson)
            property bool retired: false
            property real entranceOffset: 0
            transform: Translate { y: card.entranceOffset }
            ListView.delayRemove: exitFade.running || popupColumn.holdPositions
            ListView.onRemove: {
              // Stop an unfinished entrance before fading; both must not own opacity.
              entrance.stop()
              retired = true
              exitFade.start()
              publishHover()
            }
            ParallelAnimation {
              id: entrance
              NumberAnimation { target: card; property: "opacity"; from: 0; to: 1; duration: FoamyMotion.fadeIn; easing.type: Easing.OutCubic }
              NumberAnimation { target: card; property: "entranceOffset"; from: Style.space(8); to: 0; duration: FoamyMotion.fadeIn; easing.type: Easing.OutCubic }
            }
            NumberAnimation { id: exitFade; target: card; property: "opacity"; to: 0; duration: FoamyMotion.fadeOut; easing.type: Easing.OutCubic }
            enabled: !retired
            width: popupColumn.width
            app: modelData.app
            appIcon: service.popupImageFor(modelData, "appIcon")
            favicon: modelData.favicon || ""
            image: service.popupImageFor(modelData, "image")
            compact: service.preferences.compact
            showTimeoutIndicator: service.preferences.showTimeoutIndicator
            timeoutMs: service.durationFor(modelData.urgency, modelData.expireTimeout)
            remainingMs: showTimeoutIndicator ? service.remainingFor(modelData) : 0
            showImages: service.preferences.showImages
            imageSize: service.preferences.imageSize
            // Negative IDs identify the plugin-owned empty-history message.
            summary: modelData.originalId < 0 ? FoamyI18n.t(modelData.summary) : modelData.summary
            body: modelData.body
            glyph: modelData.glyph
            urgency: modelData.urgency
            timestamp: modelData.timestamp
            count: modelData.keys.length
            actions: JSON.parse(modelData.actionsJson || "[]")
            offers: service.offersFor(modelData)
            copiedCode: service.clipboardCopiedKey === modelData.key ? service.clipboardSecret : ""
            snoozeAvailable: true
            onFeatureRequested: function(kind, value) { service.stopKeyboard(); service.featureAction(modelData, kind, value) }
            busy: !!service.busyKeys[modelData.key]
            error: service.actionErrors[modelData.key] || ""
            selected: service.keyboardMode && service.keyboardKey === modelData.key
            deeds: service.deedsFor(modelData)
            property string registeredHoverKey: ""
            function publishHover() {
              if (!modelData) return
              var next = popupWindow.screen.name + "|" + modelData.key
              // A duplicate can change the representative key without replacing this card.
              if (registeredHoverKey && registeredHoverKey !== next) delete service.hoveredKeys[registeredHoverKey]
              registeredHoverKey = next
              if (retired) delete service.hoveredKeys[next]
              else service.hoveredKeys[next] = hovered
            }
            onHoveredChanged: publishHover()
            onModelDataChanged: publishHover()
            Component.onCompleted: { publishHover(); entrance.start() }
            Component.onDestruction: delete service.hoveredKeys[registeredHoverKey]
            // A click is the pointer taking over: it ends keyboard mode.
            onCloseRequested: { service.stopKeyboard(); service.dismissGroup(modelData, true) }
            onHideRequested: { service.stopKeyboard(); service.dismissGroup(modelData, false) }
            onCardClicked: { service.stopKeyboard(); service.activateGroup(modelData,"default") }
            onActionRequested: function(identifier) { service.stopKeyboard(); service.activateGroup(modelData,identifier) }
          }
      }
    }
  }

}
