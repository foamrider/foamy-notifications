// Notification service for the omarchy shell.

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.Notifications
import qs.Commons

import "components"
import "NotificationLogic.js" as NotificationLogic
import "Preferences.js" as Preferences
import "BrowserIdentity.js" as BrowserIdentity

Item {
  id: service

  // Injected by omarchy-shell (the first-party service loader).
  property var shell: null

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  readonly property string home: Quickshell.env("HOME")
  // History + DND live under XDG_STATE_HOME: they're persistent user state
  // (the notifications received, the last-set DND preference), not
  // regeneratable cache that a `rm -rf ~/.cache` should wipe.
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/"
  readonly property string settingsPath: stateDir + "notifications.json"
  // One file per on-screen popup, so live toasts survive shell restarts.
  // A file exists exactly as long as its popup is showing: written when the
  // toast appears, moved into historyDir when it expires, is dismissed, or is
  // acted upon.
  readonly property string popupStateDir: stateDir + "notifications/"
  // The notifications that already left the screen, one file each, trimmed to
  // the newest historyLimit. This directory IS the history: `showHistory`
  // replays exactly what has been moved in here.
  readonly property string historyDir: popupStateDir + "history/"
  // Copies of the avatars/images persisted entries reference — the sender's
  // originals don't outlive the notification (see persistablePopup). Each
  // copy lives and dies with the JSON file whose stem it carries.
  readonly property string imagesDir: popupStateDir + "images/"
  // Corner radius is shared with the menu and shell panels.
  // It uses twice Hyprland's current decoration:rounding value.
  readonly property int cornerRadius: Style.cornerRadius * 2
  // Toasts are fixed to the top-right corner. They only clear the omarchy bar
  // when the bar occupies the top or right edge, so left/bottom bars do not
  // pull notification popups away from the expected top-right location.
  // Falls back to the bar's default size (26 horizontal / 28 vertical) when
  // shell.bar isn't reachable so the popup never lands on top of the bar.
  readonly property string barPosition: shell && shell.barConfig ? String(shell.barConfig.position || "top") : "top"
  readonly property bool barVertical: barPosition === "left" || barPosition === "right"
  readonly property int defaultBarSize: barVertical ? Style.bar.sizeVertical : Style.bar.sizeHorizontal
  readonly property int liveBarSize: shell && shell.bar && !shell.bar.barHidden ? Math.max(0, shell.bar.barSize) : defaultBarSize
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
  onPreferencesChanged: { scheduleRebuild(); refreshContext() }

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
    return NotificationLogic.shouldBypassDnd(notification, NotificationUrgency.Critical)
  }

  function snapshotOf(notification) {
    return NotificationLogic.snapshotOf(notification, Date.now())
  }

  // A notification nobody looks back at:
  //   - the freedesktop `transient` hint is set ("popup only, don't store")
  //   - app_name is "notify-send" (the CLI default — means the sender
  //     didn't bother declaring an identity, so it's almost certainly
  //     ephemeral test/feedback noise)
  //   - app_name is "omarchy-action" (Omarchy's own user-action toasts —
  //     the user just triggered them)
  // Their toasts still land in history like any other once they've been on
  // screen; the distinction only decides whether a DND-silenced one is worth
  // recording at all.
  function isEphemeral(notification) {
    var transient = false
    try {
      transient = !!(notification.hints && notification.hints["transient"])
    } catch (e) { transient = false }
    return transient || NotificationLogic.isEphemeralApp(String(notification.appName || ""))
  }

  function handleNotification(notification) {
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
    if ((service.doNotDisturb && !shouldBypassDnd(notification)) || suppressForFullscreen(snapshot.urgency)) {
      // The toast never shows, so the only record a silenced notification
      // can leave is a history entry. Write it straight into history —
      // "what did I miss while silenced" is exactly what history is for.
      if (!isEphemeral(notification)) {
        writeSilenced(notification, snapshot)
        return
      }
      delete liveRefs[snapshot.originalId]
      delete liveKeys[snapshot.originalId]
      notification.tracked = false
      return
    }

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
    writeHistoryFile(written, function() {
      var updated = null
      try {
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
    if (busyKeys[String(timestamp) + "-" + String(originalId)] || handledKeys[String(timestamp) + "-" + String(originalId)]) return
    // A newer notification may have taken this id over, and the object may
    // outlive its popup — in both cases there is nothing here to refresh.
    if (service.liveRefs[originalId] !== notification) return

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
    keys.forEach(function(key) { service.releaseHistoryAction(key) })
    // Removing history while a popup is visible must not retain its callback later.
    removeKeys(keys, "historyDismissed")
  }

  function releaseHistoryBefore(timestamp) {
    // Include silenced notifications whose history write is still queued.
    var keys = Object.keys(liveKeys).map(function(id) { return liveKeys[id] }).filter(function(key) {
      return Number(key.split("-")[0]) <= timestamp
    })
    releaseHistoryKeys(keys)
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
      popupModel.remove(i)
    }
  }

  function dismissPopup(index) {
    removePopup(index, "dismiss")
  }

  function expirePopup(index) {
    removePopup(index, "expire")
  }

  function removePopup(index, reason) {
    if (index < 0 || index >= popupModel.count) return
    var entry = popupModel.get(index)
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
      enqueuePopupFileJob(["python3", helper, "focus"], function(focused) {
        // Failed focus must leave both popup persistence and center history retryable.
        if (focused) service.commitHandled(row, keys)
        else service.finishAction(row.key, "Could not identify the window. Configure browserMappings or dismiss this popup.")
      }, JSON.stringify({app:row.app, desktopEntry:row.desktopEntry, body:row.body, mappings:preferences.browserMappings}))
    } else finishAction(row.key, "This action is no longer available.")
  }

  function commitHandled(row, keys, beforeDismiss) {
    enqueuePopupFileJob(["python3", helper, "forget"], function(ok) {
      if (!ok) { service.finishAction(row.key, "Could not mark this notification handled. Try again."); return }
      keys.forEach(function(key) { service.handledKeys[key] = true })
      service.notifyCenter(keys)
      if (beforeDismiss) beforeDismiss()
      service.removeKeys(keys, "handled")
      keys.forEach(function(key) { service.releaseHistoryAction(key) })
      service.finishAction(row.key, "")
    }, JSON.stringify({keys:keys}))
  }

  function notifyCenter(keys) {
    if (!centerEnabled) return
    // Public IPC updates the in-memory badge; the helper already committed disk tombstones.
    enqueuePopupFileJob(["omarchy-shell", "foamy.notification-center.store", "handled", keys.join(",")])
  }

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

  Process {
    id: ensureDirsProc
    command: ["mkdir", "-p", service.stateDir, service.popupStateDir, service.historyDir, service.imagesDir]
    running: false
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

  // Entries are either { command, done } for a file job or { read: true } for
  // a replay's directory read. Queueing the read rather than running it beside
  // the queue is what makes it a barrier: it takes its place in line, so the
  // history it sees is the one that existed when the replay was asked for.
  // Everything queued after it — a clear, an archive, a silenced write — waits
  // for it, and no amount of later traffic can push it back.
  property var popupFileQueue: []

  // Done callback of the job popupFileProc is currently running.
  property var runningPopupFileJobDone: null
  property string runningPayload: ""

  function enqueuePopupFileJob(command, done, payload, replaceKey) {
    // Coalesce only adjacent, not-yet-started snapshots. Reads, actions and deletes stay barriers.
    var queue = popupFileQueue.slice()
    var last = queue.length ? queue[queue.length - 1] : null
    var job = { command: command, done: done || null, payload: payload || "", replaceKey: replaceKey || "" }
    if (replaceKey && last && last.replaceKey === replaceKey && !last.done) queue[queue.length - 1] = job
    else queue.push(job)
    popupFileQueue = queue
    runNextPopupFileJob()
  }

  function enqueueHistoryRead() {
    popupFileQueue = popupFileQueue.concat([{ read: true }])
    runNextPopupFileJob()
  }

  function runNextPopupFileJob() {
    if (readHistoryProc.running || popupFileProc.running) return
    if (popupFileQueue.length === 0) return

    var job = popupFileQueue[0]
    popupFileQueue = popupFileQueue.slice(1)

    if (job.read) {
      startHistoryRead()
      return
    }

    runningPayload = job.payload || ""
    popupFileProc.command = job.command
    service.runningPopupFileJobDone = job.done || null
    popupFileProc.stdinEnabled = true
    popupFileProc.running = true
  }

  Process {
    id: popupFileProc
    running: false
    stdinEnabled: true
    onStarted: { if (service.runningPayload) write(service.runningPayload); stdinEnabled = false }
    stderr: StdioCollector { id: fileErrors }
    onExited: function(exitCode, exitStatus) {
      var ok = exitCode === 0 && exitStatus === 0
      if (!ok) { service.lastError = "Notification operation failed: " + fileErrors.text.trim(); console.warn(service.lastError) }
      var done = service.runningPopupFileJobDone
      service.runningPopupFileJobDone = null
      if (done) {
        try {
          done(ok)
        } catch (e) {
          console.warn("notifications: file job callback failed:", e)
        }
      }
      service.runNextPopupFileJob()
    }
  }

  function persistPopupFile(snapshot) {
    var persistable = NotificationLogic.persistablePopup(snapshot, imagesDir)
    enqueuePopupFileJob(["python3", helper, "write"], null, JSON.stringify({entry:persistable.entry, copies:persistable.copies}), NotificationLogic.imageStem(snapshot))
  }

  function deletePopupFileFor(row) {
    if (!row) return
    // History replays and the "no recent notifications" placeholder never
    // had a file — rm -f on the computed paths is a harmless no-op there.
    enqueuePopupFileJob(["bash", "-c",
      "rm -f \"$1/$2.json\" \"$3/$2\"-*", "--",
      popupStateDir, NotificationLogic.imageStem(row), imagesDir])
  }

  // ---------------------------------------------------- history
  //
  // A popup that leaves the screen keeps its file — it just moves one level
  // down, into historyDir. Trimming happens right there in the same shell
  // job: the names sort numerically by their leading millisecond timestamp,
  // so everything but the newest historyLimit files is the tail to drop,
  // image copies included. Callers set $hist, $limit and $imgs first.
  readonly property string trimHistoryScript:
    "ls -1 \"$hist\" 2>/dev/null | sort -n | head -n \"-$limit\" | while IFS= read -r stale; do rm -f \"$hist/$stale\" \"$imgs/${stale%.json}\"-*; done"

  function archivePopupFileFor(row) {
    if (!row) return
    // A history replay or the empty-history placeholder has no file to move;
    // the failed mv leaves the history untouched, trimming included. Image
    // copies stay put — live and archived entries share imagesDir.
    enqueuePopupFileJob(["bash", "-c",
      "mkdir -p \"$1\" || exit 0\n" +
      "hist=\"$1\" limit=\"$2\" imgs=\"$5\"\n" +
      "mv -f \"$4/$3\" \"$1/$3\" 2>/dev/null || exit 0\n" +
      trimHistoryScript, "--",
      historyDir,
      String(historyLimit),
      NotificationLogic.popupFileName(row),
      popupStateDir,
      imagesDir])
  }

  // Record a notification that never made it to the screen (DND silenced it),
  // straight into history. Same file format as an archived popup, so the
  // replay can't tell the two apart.
  //
  // With the center enabled, live default actions remain tracked after writing.
  // Other silenced notifications are released once their latest content is saved.
  function writeHistoryFile(entry, done) {
    if (!entry) { if (done) done(true); return }
    var persistable = NotificationLogic.persistablePopup(entry, imagesDir)
    enqueuePopupFileJob(["python3", helper, "write"], done, JSON.stringify({entry:persistable.entry, copies:persistable.copies, history:true}))
  }

  function clearHistory() {
    Object.keys(retainedHistory).forEach(function(key) { service.releaseHistoryAction(key) })
    enqueuePopupFileJob(["bash", "-c",
      "for f in \"$1\"/*.json; do\n" +
      "  [[ -e $f ]] || continue\n" +
      "  stale=\"${f##*/}\"\n" +
      "  rm -f \"$f\" \"$2/${stale%.json}\"-*\n" +
      "done", "--", historyDir, imagesDir])
  }

  // A restart can kill a queued job between its cp and its JSON write,
  // leaving copies no JSON-derived cleanup can name. Swept at startup,
  // through the queue so in-flight copies aren't mistaken for orphans.
  function sweepOrphanImages() {
    enqueuePopupFileJob(["bash", "-c",
      "for img in \"$3\"/*; do\n" +
      "  [[ -e $img ]] || continue\n" +
      "  [[ $img == *.tmp ]] && { rm -f -- \"$img\"; continue; }\n" +
      "  stem=\"${img##*/}\"\n" +
      "  stem=\"${stem%-*}\"\n" +
      "  [[ -e $1/$stem.json || -e $2/$stem.json ]] || rm -f \"$img\"\n" +
      "done", "--", popupStateDir, historyDir, imagesDir])
  }

  Process {
    id: readHistoryProc
    running: false
    stdinEnabled: true
    onStarted: { write('{"history":true}'); stdinEnabled = false }
    // Let the file queue go again, whatever the read did — a failed or empty
    // read must not leave archives and clears parked behind it forever.
    onExited: service.runNextPopupFileJob()
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: service.replayHistory(text)
    }
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
    if (readHistoryProc.running || service.historyReadQueued) return "ok"
    service.replayCarryOver = liveRowsForReplay()
    service.historyReadQueued = true
    enqueueHistoryRead()
    return "ok"
  }

  function startHistoryRead() {
    service.historyReadQueued = false
    readHistoryProc.command = ["python3", helper, "read"]
    readHistoryProc.stdinEnabled = true
    readHistoryProc.running = true
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
      rows.push(NotificationLogic.persistablePopup({
        id: row.id,
        originalId: row.originalId,
        app: row.app,
        appIcon: row.appIcon,
        summary: row.summary,
        body: row.body,
        image: row.image,
        glyph: row.glyph || "",
        execArgv: row.execArgv || "",
        urgency: row.urgency,
        timestamp: row.timestamp
      }, imagesDir).entry)
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
        glyph: "󰂚",
        execArgv: "",
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

  Process {
    id: restorePopupsProc
    running: false
    stdinEnabled: true
    onStarted: { write("{}"); stdinEnabled = false }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: service.restorePopups(text)
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
    path: service.settingsPath
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
    if (service.settingsLoaded) return

    var parsed = NotificationLogic.parseSettings(raw)
    if (parsed.error) console.warn("notifications: settings parse failed:", parsed.errorMessage || "")

    if (parsed.dnd !== null) {
      service._hydrating = true
      persisted.doNotDisturb = parsed.dnd
      service._hydrating = false
    }

    service.settingsLoaded = true
    // Versions before the history moved into its own directory kept every
    // notification in here. Rewrite once so that dead payload doesn't sit in
    // the file until the next DND toggle happens to clear it.
    if (parsed.legacy) service.scheduleSettingsSave()
  }

  function flushSettings() {
    settingsFile.setText(JSON.stringify({ version: 3, dnd: persisted.doNotDisturb }, null, 2) + "\n")
  }

  Component.onCompleted: {
    ensureDirsProc.running = true
    configFile.reload()
    refreshContext()
    // Once mkdir has had a tick, load the existing settings file. FileView
    // surfaces an empty string when the file doesn't exist; loadSettings
    // handles that path.
    Qt.callLater(function() {
      settingsFile.reload()
      // Re-show popups that were on screen when the previous shell died.
      // The glob-through-bash tolerates a missing/empty dir (first run).
      // awk 1 (not cat) so a torn file missing its trailing newline can't
      // glue itself onto the next file and take a valid popup down with it.
      restorePopupsProc.command = ["python3", service.helper, "read"]
      restorePopupsProc.running = true
      // Safe beside the restore read: it only re-persists entries whose
      // JSON exists, exactly the images the sweep keeps.
      service.sweepOrphanImages()
    })
  }

  // ---------------------------------------------------- IPC

  IpcHandler {
    target: "notifications"

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
    if (!preferences.suppressFullscreen && preferences.monitor !== "pointer") { handleNotification(notification); return }
    notification.tracked = true
    // Retain live objects only briefly while the compositor snapshot is in flight.
    if (pendingNotifications.length >= 100) { writeSilenced(notification, snapshotOf(notification)); return }
    pendingNotifications = pendingNotifications.concat([notification])
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
        try { service.handleNotification(pending[i]) } catch(e) { console.warn("foamy.notifications: sender closed before delivery") }
      }
      service.scheduleRebuild()
    }
  }
  Connections {
    target: Hyprland
    function onRawEvent(event) {
      if (["fullscreen","activewindowv2","workspacev2","focusedmon","monitoradded","monitorremoved","activespecial"].indexOf(event.name) >= 0) service.refreshContext()
    }
  }

  // One frame clock keeps expiry and the line in sync without per-card timers.
  FrameAnimation {
    running: service.displayGroups.some(function(group) {
      return service.durationFor(group.urgency, group.expireTimeout) > 0
    })
    onTriggered: {
      var elapsedMs = frameTime * 1000
      if (elapsedMs <= 0) return
      var expired = [], advanced = false
      var hoverKeys = Object.keys(service.hoveredKeys)
      for (var i = 0; i < service.displayGroups.length; i++) {
        var group = service.displayGroups[i]
        var hovered = hoverKeys.some(function(k) { return k.endsWith("|" + group.key) && service.hoveredKeys[k] })
        if (hovered || service.busyKeys[group.key]) continue
        if (service.durationFor(group.urgency,group.expireTimeout) <= 0) continue
        for (var j = 0; j < group.keys.length; j++) {
          var key = group.keys[j]
          service.lifetimes[key] -= elapsedMs
          advanced = true
          if (service.lifetimes[key] <= 0) expired.push(key)
        }
      }
      // In-place map updates need one signal per shared frame for the card bindings.
      if (advanced) service.lifetimesChanged()
      if (expired.length) service.removeKeys(expired,"expire")
    }
  }

  IpcHandler {
    target: "foamy.notifications"
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
        centerEnabled:service.centerEnabled,configError:service.configError,lastError:service.lastError,busy:popupFileProc.running || service.popupFileQueue.length>0})
    }
  }

  Variants {
    model: Quickshell.screens
    PanelWindow {
      id: popupWindow
      required property var modelData
      screen: modelData
      readonly property bool selected: service.targetMonitors.indexOf(screen.name) >= 0
      readonly property bool fullscreen: service.preferences.suppressFullscreen && service.fullscreenMonitors.indexOf(screen.name) >= 0
      // Keep the surface mapped until the final card finishes fading out.
      visible: selected && (popupColumn.groups.length > 0 || popupColumn.implicitHeight > 0)
      WlrLayershell.namespace: "omarchy-notifications"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
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
      NotificationStack {
        id: popupColumn
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: popupWindow.placement.margins.top + Style.space(8)
        anchors.rightMargin: popupWindow.placement.margins.right + Style.space(8)
        width: Math.max(1,Math.min(Style.space(service.preferences.width),popupWindow.width - anchors.rightMargin - Style.space(8)))
        spacing: Style.space(8)
        groups: service.displayGroups.filter(function(group) { return !popupWindow.fullscreen || (group.urgency === 2 && service.preferences.fullscreenCritical) })
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
              NumberAnimation { target: card; property: "opacity"; from: 0; to: 1; duration: 180; easing.type: Easing.OutCubic }
              NumberAnimation { target: card; property: "entranceOffset"; from: Style.space(8); to: 0; duration: 180; easing.type: Easing.OutCubic }
            }
            NumberAnimation { id: exitFade; target: card; property: "opacity"; to: 0; duration: 120; easing.type: Easing.OutCubic }
            enabled: !retired
            width: popupColumn.width
            app: modelData.app
            appIcon: modelData.appIcon
            favicon: modelData.favicon || ""
            image: modelData.image
            compact: service.preferences.compact
            showTimeoutIndicator: service.preferences.showTimeoutIndicator
            timeoutMs: service.durationFor(modelData.urgency, modelData.expireTimeout)
            remainingMs: showTimeoutIndicator ? service.remainingFor(modelData) : 0
            showImages: service.preferences.showImages
            imageSize: service.preferences.imageSize
            summary: modelData.summary
            body: modelData.body
            glyph: modelData.glyph
            urgency: modelData.urgency
            timestamp: modelData.timestamp
            count: modelData.keys.length
            actions: JSON.parse(modelData.actionsJson || "[]")
            busy: !!service.busyKeys[modelData.key]
            error: service.actionErrors[modelData.key] || ""
            fontFamily: service.shell && service.shell.bar ? service.shell.bar.fontFamily : Style.font.family
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
            onCloseRequested: service.removeKeys(modelData.keys,"dismiss")
            onCardClicked: service.activateGroup(modelData,"default")
            onActionRequested: function(identifier) { service.activateGroup(modelData,identifier) }
          }
      }
    }
  }

}
