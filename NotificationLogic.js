function isChromiumDerived(app, appIcon) {
  var source = (String(app || "") + "\n" + String(appIcon || "")).toLowerCase()
  return source.indexOf("chrom") >= 0 || source.indexOf("brave") >= 0 ||
         source.indexOf("vivaldi") >= 0 || source.indexOf("microsoft-edge") >= 0 ||
         source.indexOf("opera") >= 0
}

// True when a `<...>` run is an image tag, so the name is read the way Qt's
// parser reads it: after the `<`, the leading run of letters and digits.
//
// Skip everything up to that run rather than matching the separator, because
// there is no JavaScript expression for what Qt skips. QQuickStyledText calls
// skipSpace(), which is QChar::isSpace(), and that set is not `\s`: Qt counts
// U+0085 NEL and `\s` does not, while `\s` counts U+FEFF and Qt does not. A
// name read with `\s` therefore misses a tag written as `<`, U+0085, `img`:
// Qt skips the NEL, reads `img` and issues the GET, while the regex finds no
// name at all and the tag is kept. Measured against Qt 6.11.2.
//
// Over-skipping is the safe direction. It can only classify more runs as
// images, and dropping a run never manufactures a tag: a dropped run joins two
// stretches of text that each contain no `<`.
function isImageTag(tag) {
  var name = /^<[^A-Za-z0-9]*([A-Za-z0-9]+)/.exec(tag)
  return !!name && name[1].toLowerCase() === "img"
}

// The body renders as StyledText so notifications can use the markup the
// body-markup capability advertises (see Service.qml). StyledText honours
// <img src>, and a remote src makes the shell issue an unauthenticated GET
// with no user action, so image tags go before the renderer sees them.
//
// Work in whole tags, never in substrings of one. A `<` opens a tag that runs
// to the next `>`, nested `<` and all, and only a tag whose own name is `img`
// is dropped.
//
// That is the conservative bound, not Qt's exact one: Qt lets a `>` inside a
// quoted attribute value pass without closing the tag, so a Qt tag can be
// longer than the run taken here. Do not "correct" this to match Qt. Taking
// the shorter run only ever splits one Qt tag into several, and a split can
// only expose an `<img` to be dropped, never hide one — whereas honouring
// quotes would let `<b title="a>b"><img src="http://host/x.png">` through.
//
// Deleting a substring is what makes a naive `/<img[^>]*>/g` unsafe. Given
//
//   <im<img src="http://a/decoy.png">g src="http://a/beacon.png">
//
// Qt reads ONE malformed tag named `im` and renders nothing, but removing the
// inner match closes the surviving halves up into `<img src=".../beacon.png">`
// — a live tag the input never contained. The stripper would be manufacturing
// the very thing it exists to remove.
//
// Because every `<` opens a tag, the text between tags never contains one, so
// dropping a tag cannot splice its neighbours into a new one. That makes a
// single pass sufficient, with no re-scanning and no input bound to police.
function stripImageTags(text) {
  var out = ""
  var i = 0

  while (i < text.length) {
    var open = text.indexOf("<", i)
    if (open === -1) {
      out += text.slice(i)
      break
    }

    out += text.slice(i, open)

    // An unterminated tag at the end of the string still reaches the renderer,
    // which closes it itself, so treat the remainder as one tag.
    var close = text.indexOf(">", open)
    var tag = close === -1 ? text.slice(open) : text.slice(open, close + 1)

    if (!isImageTag(tag)) out += tag
    i = close === -1 ? text.length : close + 1
  }

  return out
}

// The card renders Markup.render(sanitizeBody(…)) (stock 4.5 allowlist
// semantics, see Markup.js). sanitizeBody only drops image tags and a
// Chromium-family sender's leading origin label before that.
function sanitizeBody(body, app, appIcon) {
  var text = stripImageTags(String(body || ""))
  if (!isChromiumDerived(app, appIcon)) return text

  return text
    .replace(/^\s*<a\b[^>]*>\s*(?:https?:\/\/|www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:\/[^<\s]*)?\s*<\/a>\s*/i, "")
    .replace(/^\s*(?:https?:\/\/|www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:\/\S*)?\s+/i, "")
}

function summaryStartsWithGlyph(summary) {
  var text = String(summary || "").replace(/^\s+/, "")
  if (!text) return false

  var offset = 1
  var first = text.charCodeAt(0)
  if (first >= 0xd800 && first <= 0xdbff && text.length > 1) offset = 2

  var spaces = 0
  while (offset < text.length && text.charAt(offset) === " ") {
    spaces++
    offset++
  }

  return spaces >= 2
}

function shouldBypassDnd(notification, criticalUrgency, silenceCritical) {
  if (silenceCritical && notification && notification.urgency === criticalUrgency) return false
  var appName = String((notification && notification.appName) || "")
  if (appName === "omarchy-action") return true
  return appName === "notify-send" && notification && notification.urgency === criticalUrgency
}

function isEphemeralApp(appName) {
  var name = String(appName || "")
  return name === "notify-send" || name === "omarchy-action"
}

function stringHint(hints, name) {
  try {
    if (hints) {
      var value = hints[name]
      if (value !== undefined && value !== null) return String(value)
    }
  } catch (e) {
  }
  return ""
}

// Check sender fields before tracking its QObject or retaining a snapshot/callback.
function notificationWithinLimits(notification) {
  var n = notification || {}
  var limits = {appName:256, appIcon:4096, summary:4096, body:16384, desktopEntry:512, image:12288}
  for (var name in limits) if (String(n[name] || "").length > limits[name]) return false
  var hints = {"image-path":4096, "image_path":4096, "omarchy-glyph":64, "omarchy-exec-argv":8192, "omarchy-actions":8192}
  for (var hint in hints) if (stringHint(n.hints, hint).length > hints[hint]) return false
  var actions = n.actions || []
  if (actions.length > 32) return false
  for (var i = 0; i < actions.length; i++) {
    if (String(actions[i].identifier || "").length > 256 || String(actions[i].text || "").length > 512) return false
  }
  return true
}

function limitedString(value, limit) {
  var text = String(value || "")
  return text.length <= limit ? text : ""
}

// QML has no TextEncoder; count the bytes that the helper receives, including surrogate pairs.
function utf8Bytes(text) {
  var bytes = 0
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code < 128) bytes++
    else if (code < 2048) bytes += 2
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length
        && text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) { bytes += 4; i++ }
    else bytes += 3
  }
  return bytes
}

function glyphFromHints(hints) {
  return limitedString(stringHint(hints, "omarchy-glyph"), 64)
}

// The click action: a JSON argv string from omarchy-notification-send
// --exec. Carried as data so a toast restored after a shell restart stays
// clickable (a libnotify action can't — its sender is gone). Run via
// Util.execArgv as bash positional parameters, never a shell string, so
// attacker-controlled values (a title, a filename) can't become commands.
function execArgvFromHints(hints) {
  return limitedString(stringHint(hints, "omarchy-exec-argv"), 8192)
}

// Validate a persisted omarchy-exec-argv into a runnable argv, or null. This is
// a STRUCTURAL check only: it fails closed on a malformed hint (non-array, a
// non-string or empty program, or a leading-dash program that argv would read as
// an option). It does not judge intent — a well-formed ["bash","-c",…] is
// accepted. WHICH senders may set this hint is a separate boundary: any
// session-bus process can, by the freedesktop protocol's design (see
// docs/notifications.md), which is equivalent to same-uid code execution.
function parseExecArgv(value) {
  var text = String(value || "")
  if (!text) return null

  var parsed
  try {
    parsed = JSON.parse(text)
  } catch (e) {
    return null
  }

  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > 32) return null
  for (var i = 0; i < parsed.length; i++) {
    // A NUL cannot travel in an argv: Qt would cut the argument short there.
    if (typeof parsed[i] !== "string" || parsed[i].length > 4096 || parsed[i].indexOf("\u0000") >= 0) return null
  }
  if (!parsed[0] || parsed[0].charAt(0) === "-") return null
  return parsed
}

// The omarchy-actions hint (4.5 `omarchy-notification-send --action label prog … --with`):
// up to three buttons, each a label and an argv held to the click's rules.
// Same structural validation as stock Security.parseExecActions.
function parseExecActions(value) {
  var text = String(value || "")
  if (!text || text.length > 8192) return null
  var parsed
  try { parsed = JSON.parse(text) } catch (e) { return null }
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > 3) return null
  var out = []
  for (var i = 0; i < parsed.length; i++) {
    var entry = parsed[i]
    if (!entry || typeof entry !== "object" || typeof entry.label !== "string") return null
    var label = entry.label.slice(0, 256)
    var argv = parseExecArgv(Array.isArray(entry.argv) ? JSON.stringify(entry.argv) : entry.argv)
    if (!label.trim() || !argv) return null
    out.push({ label: label, argv: argv })
  }
  return out
}

function execActionsFromHints(hints) {
  var actions = parseExecActions(limitedString(stringHint(hints, "omarchy-actions"), 8192))
  return actions ? JSON.stringify(actions) : ""
}

// The card's buttons in the order it draws and the number keys count them:
// Omarchy --action buttons, then the sender's own actions (settings last),
// then copying each detected code, then the offered link.
function deeds(row, offers) {
  var out = []
  var exec = parseExecActions(row && row.execActions) || []
  for (var e = 0; e < exec.length; e++) out.push({ kind: "exec", value: String(e), text: exec[e].label })
  var actions = []
  try { actions = JSON.parse((row && row.actionsJson) || "[]") } catch (err) { actions = [] }
  var settings = []
  for (var i = 0; i < actions.length; i++) {
    var deed = { kind: "action", value: String(actions[i].identifier), text: String(actions[i].text || actions[i].identifier) }
    if (/^settings$/i.test(deed.value)) settings.push(deed); else out.push(deed)
  }
  var codes = String((offers && offers.codes) || "").split(" ").filter(Boolean)
  for (var c = 0; c < codes.length; c++) out.push({ kind: "code", value: codes[c], text: "" })
  if (offers && offers.link) out.push({ kind: "link", value: offers.link, text: "", meeting: !!offers.meeting })
  return out.concat(settings)
}

function shouldRenderCompactGlyph(glyph, iconSource, singleLineToast) {
  return String(glyph || "").length > 0 && String(iconSource || "").length === 0 && !!singleLineToast
}

function actionRows(notification) {
  var rows = []
  var actions = notification && notification.actions ? notification.actions : []
  for (var i = 0; i < actions.length && rows.length < 8; i++) {
    var action = actions[i]
    if (action && action.identifier !== "default" && String(action.identifier || "").length <= 256)
      rows.push({identifier: String(action.identifier), text: String(action.text || action.identifier).slice(0, 80)})
  }
  return rows
}

// Quickshell removes raw pixel hints before QML can validate their buffer.
// Only qsimage is the native pixel-buffer provider; theme icons use a separate provider.
function hasRawImage(notification) {
  return String((notification && notification.image) || "").indexOf("image://qsimage/") === 0
}

// Never request the native pixmap provider; prefer an explicit local-file hint.
function popupImage(notification) {
  var n = notification || {}
  var path = localImageFile(n.image)
  if (!path) path = localImageFile(stringHint(n.hints, "image-path"))
  if (!path) path = localImageFile(stringHint(n.hints, "image_path"))
  return path ? "file://" + path.split("/").map(encodeURIComponent).join("/") : ""
}

function themeIcon(value) {
  return /^[A-Za-z0-9_.-]+$/.test(String(value || "")) ? String(value) : ""
}

// Only the helper's content-addressed PNGs may cross into the popup renderer.
function normalizedImage(value, imagesDir) {
  var prefix = "file://" + String(imagesDir || "").split("/").map(encodeURIComponent).join("/")
  var source = String(value || "")
  if (!imagesDir || source.indexOf(prefix) !== 0) return ""
  return /^[0-9]+-[0-9]+-(appIcon|image)-[a-f0-9]{64}\.png$/.test(source.slice(prefix.length)) ? source : ""
}

function snapshotOf(notification, timestamp) {
  var n = notification || {}
  var id = n.id || 0
  var expireTimeout = Number(n.expireTimeout || 0)
  if (!isFinite(expireTimeout) || expireTimeout < 0) expireTimeout = 0
  return {
    id: id,
    originalId: id,
    app: limitedString(n.appName, 256),
    appIcon: limitedString(n.appIcon, 4096),
    summary: String(n.summary || "").slice(0, 4096),
    body: String(n.body || "").slice(0, 16384),
    desktopEntry: limitedString(n.desktopEntry, 512),
    transient: !!n.transient,
    actionsJson: JSON.stringify(actionRows(n)),
    image: popupImage(n),
    glyph: glyphFromHints(n.hints),
    execArgv: execArgvFromHints(n.hints),
    execActions: execActionsFromHints(n.hints),
    urgency: n.urgency,
    expireTimeout: expireTimeout,
    timestamp: timestamp === undefined ? Date.now() : timestamp
  }
}

// Everything the popup card draws, and therefore everything an in-place
// update has to write through to the row and its file.
var POPUP_ROLES = ["desktopEntry", "transient", "actionsJson", "app", "appIcon", "summary", "body", "image", "glyph", "execArgv", "execActions", "urgency", "expireTimeout"]

function popupRoles() {
  return POPUP_ROLES
}

// Whether a refresh has anything to write. Each property a client updates
// emits its own signal, and the catch-up refresh after a row is inserted
// usually finds the object exactly as it was snapshotted — without this,
// one update would rewrite the file several times over.
function popupRowChanged(row, updated) {
  var current = row || {}
  var next = updated || {}
  for (var i = 0; i < POPUP_ROLES.length; i++) {
    var role = POPUP_ROLES[i]
    if (current[role] !== next[role]) return true
  }
  return false
}

// A client updating a notification through replaces_id keeps the identity of
// the popup it took over: the file name is the timestamp and id the popup was
// first persisted under, and the restore, replace and archive paths all key
// off that name. Only what the card draws comes from the updated object.
function replacementSnapshot(notification, originalId, timestamp) {
  var updated = snapshotOf(notification, timestamp)
  updated.id = originalId
  updated.originalId = originalId
  return updated
}

function historyEntry(value, normalUrgency) {
  var e = value || {}
  return {
    id: e.id || 0,
    originalId: e.originalId || e.id || 0,
    app: e.app || "",
    appIcon: e.appIcon || "",
    summary: e.summary || "",
    body: e.body || "",
    desktopEntry: String(e.desktopEntry || ""),
    transient: !!e.transient,
    actionsJson: "[]",
    image: e.image || "",
    glyph: e.glyph || "",
    execArgv: e.execArgv || "",
    execActions: parseExecActions(e.execActions) ? String(e.execActions) : "",
    urgency: typeof e.urgency === "number" ? e.urgency : normalUrgency,
    expireTimeout: 0,
    timestamp: e.timestamp || 0
  }
}

// notifications.json holds nothing but the last-set DND preference now that
// history is a directory of files. Older versions kept `pending`/`past`
// (and, older still, `entries`) arrays in there; their presence is reported
// so the service can rewrite the file without the dead payload.
function parseSettings(raw) {
  var text = String(raw || "").trim()
  if (!text) return { error: false, dnd: null, legacy: false }

  try {
    var parsed = JSON.parse(text)
    return {
      error: false,
      dnd: parsed && typeof parsed.dnd === "boolean" ? parsed.dnd : null,
      legacy: !!(parsed && (parsed.pending || parsed.past || parsed.entries))
    }
  } catch (e) {
    return { error: true, errorMessage: String(e), dnd: null, legacy: false }
  }
}

// ---------------------------------------------------- popup persistence
//
// Each on-screen popup is mirrored to its own file under
// $XDG_STATE_HOME/foamy-notifications/popups/ so toasts survive shell restarts
// (e.g. the restart `omarchy-update` performs). The file exists exactly as
// long as the popup is on screen or waiting in the overflow queue (marked
// `queued`, see planStack): it is written when the toast arrives and
// moved into the history/ subdirectory when the toast expires, is dismissed,
// or dismissed. Handled actions remove the file. History keeps the newest 100.

function popupEntry(value, normalUrgency) {
  var entry = historyEntry(value, normalUrgency)
  var expire = Number((value || {}).expireTimeout || 0)
  if (!isFinite(expire) || expire < 0) expire = 0
  entry.expireTimeout = expire
  // Absolute expiry deadline, set only when a restore resets a surviving
  // popup's display lifetime or a card shows after waiting in the overflow
  // queue. Kept out of the entry entirely when unset so restored rows match
  // the roles of freshly received ones.
  var deadline = Number((value || {}).deadline || 0)
  if (isFinite(deadline) && deadline > 0) entry.deadline = deadline
  // Set while the card waits in the overflow queue (planStack): it has not
  // shown yet, so its display clock has not started.
  if ((value || {}).queued === true) entry.queued = true
  return entry
}

function popupFileName(entry) {
  return imageStem(entry) + ".json"
}

// ---------------------------------------------------- persisted images
//
// A notification's images only exist while it is live: Chromium-family
// senders (all Omarchy web apps) delete their scoped /tmp files on close,
// and image-data hints surface as in-process image:// URLs that die with
// the server object. Persisted entries therefore reference their own
// copies, named by the entry's file stem so cleanup can find them from
// the JSON file name alone.

var PERSISTED_IMAGE_ROLES = ["appIcon", "image"]

function imageStem(entry) {
  var e = entry || {}
  return String(e.timestamp || 0) + "-" + String(e.originalId || 0)
}

// The filesystem path behind a file-backed image value, or "" for anything
// a copy can't capture: themed icon names, in-process image:// URLs, empty.
function localImageFile(value) {
  var s = limitedString(value, 12288)
  if (s.indexOf("file://") === 0) {
    s = s.slice(7)
    try { s = decodeURIComponent(s) } catch (e) {}
  }
  return s.length <= 4096 && s.charAt(0) === "/" ? s : ""
}

// The entry as it should hit the disk, plus the copies that make it true.
// File-backed images redirect to their copy under imagesDir; dead image://
// URLs drop to "" (the card falls back to the app icon). Already-redirected
// values map onto themselves and produce no copy, keeping restores no-ops.
function persistablePopup(entry, imagesDir) {
  var e = entry || {}
  var out = {}
  for (var key in e) out[key] = e[key]
  var copies = []
  for (var i = 0; i < PERSISTED_IMAGE_ROLES.length; i++) {
    var role = PERSISTED_IMAGE_ROLES[i]
    var value = String(out[role] || "")
    if (!value) continue
    var source = localImageFile(value)
    if (source) {
      var copy = String(imagesDir || "") + imageStem(e) + "-" + role
      if (source !== copy) copies.push({ from: source, to: copy })
      out[role] = "file://" + copy
    } else if (value.indexOf("image://") === 0) {
      out[role] = ""
    }
  }
  return { entry: out, copies: copies }
}

function serializePopup(entry, normalUrgency) {
  // Compact (single-line) on purpose: restore cats every file together and
  // parses line by line, which only works when each file is one line.
  return JSON.stringify(popupEntry(entry, normalUrgency))
}

// Parse the concatenation of every persisted popup file into entries,
// newest-first. Deliberately NO dedupe by originalId: ids restart from 1
// with every server process, so two files sharing an id are usually
// different generations — dropping the older one would silently discard a
// restored critical alert the moment a fresh notification reuses its id.
// The one case that leaves a genuine duplicate (a crash between a
// replacement's write and the replaced file's delete) merely re-shows a
// superseded toast, which expires or is dismissed and cleans itself up.
function parsePopupFiles(raw, normalUrgency) {
  var lines = String(raw || "").split("\n")
  var entries = []
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim()
    if (!line) continue
    try {
      var value = JSON.parse(line)
      if (value && typeof value === "object") entries.push(popupEntry(value, normalUrgency))
    } catch (e) {
      // A torn write from a crash mid-save — skip the line, keep the rest.
    }
  }
  entries.sort(function(a, b) { return (b.timestamp || 0) - (a.timestamp || 0) })
  return entries
}

// A persisted popup whose lifetime already ran out would have expired on
// screen had the shell kept running, so it is not restored. duration 0 means
// the popup never expires (critical urgency) and always survives restarts.
// A restore-reset deadline outranks the original timestamp: without it, a
// second restart would judge a re-shown toast by a clock that no longer
// governs its display and drop it while it is still on screen.
function popupExpired(entry, duration, now) {
  var deadline = Number((entry || {}).deadline || 0)
  if (isFinite(deadline) && deadline > 0) return Number(now) >= deadline
  var lifetime = Number(duration || 0)
  if (!isFinite(lifetime) || lifetime <= 0) return false
  return (Number(now) - Number((entry || {}).timestamp || 0)) >= lifetime
}

// ---------------------------------------------------- overflow queue
//
// At most `limit` cards show; the others wait for a free slot instead of going
// to history. Every rebuild asks planStack which groups (model order, newest
// first) show, in what order, and which wait. Priority, highest first:
//
//   0. shown cards that must stay (`pinned`: hovered, busy, keyboard-selected),
//      all of them even past the limit, then displays that cannot wait
//      (`canWait` false: a history replay, the empty-history message);
//   1. critical cards, shown or waiting, the newest arrival first;
//   2. other shown cards, the longest shown first;
//   3. other waiting cards, oldest arrival first.
//
// The first `limit` show. So a critical arrival takes the slot of the
// non-critical card shown last, which waits again (at the head of the other
// cards: it arrived before those waiting). With only criticals on screen, a
// new one takes the slot of the oldest unpinned critical, which waits again,
// not to history, and shows again when a slot frees: criticals rotate, so
// ones that never expire (criticalTimeoutSec 0) cannot hold every slot and
// keep later alerts waiting. When the limit drops (the screen is full) the
// non-critical card shown last waits again (the one that did not fit), else
// the oldest critical. A display that cannot wait and does not fit is
// dropped. `since` orders shown cards (higher is shown later; restored cards
// have none and count as the earliest, oldest first). Arrival is a key's
// timestamp unless `arrivals` says otherwise; a group's is its oldest key's,
// so a duplicate keeps the group's turn. Waiting cards are returned in queue
// order, the order they would show in: criticals newest first, then the other
// cards in arrival order.
//
// Display order (`ranks`, higher is higher on the stack): shown cards keep
// their place, cards that waited join the bottom, so nothing on screen moves
// for them, and new arrivals go on top, newest first. `top`/`bottom` are the
// rank counters to continue from, `seq` the `since` counter.
//
// The queue holds at most `queueLimit` notifications: past it the oldest
// waiting non-critical one (else the oldest waiting one) is `evicted`.
//
// `held` groups (arrivals while the pointer is over the stack) wait whatever
// their urgency and whatever room there is: they take no slot until the hold
// ends. A group on screen, or one that cannot wait, is never held.
function planStack(groups, options) {
  var o = options || {}
  var limit = Math.max(1, Math.floor(Number(o.limit)) || 1)
  var shown = o.shown || {}, waited = o.waiting || {}, ranks = o.ranks || {}, arrivals = o.arrivals || {}, since = o.since || {}
  var pinned = o.pinned || function() { return false }
  var canWait = o.canWait || function() { return true }
  var held = o.held || function() { return false }
  function arrivalOf(key) {
    var value = arrivals[key] !== undefined ? Number(arrivals[key]) : Number(String(key).split("-")[0])
    return isFinite(value) ? value : 0
  }
  var entries = []
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i]
    var keys = group.keys && group.keys.length ? group.keys : [group.key]
    var entry = {group: group, index: i, shown: false, waited: false, rank: null, since: null, arrival: Infinity, canWait: !!canWait(group)}
    for (var k = 0; k < keys.length; k++) {
      if (shown[keys[k]]) entry.shown = true
      if (waited[keys[k]]) entry.waited = true
      var rank = Number(ranks[keys[k]])
      if (ranks[keys[k]] !== undefined && isFinite(rank) && (entry.rank === null || rank > entry.rank)) entry.rank = rank
      var shownSince = Number(since[keys[k]])
      if (since[keys[k]] !== undefined && isFinite(shownSince) && (entry.since === null || shownSince < entry.since)) entry.since = shownSince
      entry.arrival = Math.min(entry.arrival, arrivalOf(keys[k]))
    }
    var critical = group.urgency === 2
    entry.held = !entry.shown && entry.canWait && !!held(group)
    if (entry.shown) entry.tier = pinned(group) ? 0 : critical ? 1 : 2
    else entry.tier = !entry.canWait ? 0 : critical ? 1 : 3
    entries.push(entry)
  }
  entries.sort(function(a, b) {
    if (a.tier !== b.tier) return a.tier - b.tier
    // Criticals: the newest first, shown or not; at equal arrival the one on screen stays.
    if (a.tier === 1) return b.arrival - a.arrival || (a.shown === b.shown ? 0 : a.shown ? -1 : 1) || a.index - b.index
    if (a.shown !== b.shown) return a.shown ? -1 : 1
    if (a.shown) {
      var first = a.since === null ? -Infinity : a.since, second = b.since === null ? -Infinity : b.since
      return first !== second ? first - second : b.index - a.index
    }
    if (!a.canWait) return a.index - b.index
    return a.arrival - b.arrival || b.index - a.index
  })
  var take = Math.max(limit, entries.filter(function(e) { return e.tier === 0 && e.shown }).length)
  var visible = [], waiting = [], dropped = []
  entries.filter(function(e) { return !e.held }).forEach(function(e, index) {
    if (index < take) visible.push(e)
    else if (e.canWait) waiting.push(e)
    else dropped.push(e.group)
  })
  entries.forEach(function(e) { if (e.held) waiting.push(e) })
  // Queue order, the order they show in: criticals newest first, then the
  // others oldest first (a card just sent back waits in its arrival turn too).
  function waitOrder(a, b) {
    var first = a.group.urgency === 2, second = b.group.urgency === 2
    if (first !== second) return first ? -1 : 1
    return first ? b.arrival - a.arrival || a.index - b.index : a.arrival - b.arrival || b.index - a.index
  }
  waiting.sort(waitOrder)

  var evicted = []
  var queueLimit = o.queueLimit === undefined ? Infinity : Math.max(0, Number(o.queueLimit))
  var rows = []
  waiting.forEach(function(e) {
    e.group.keys.forEach(function(key) { rows.push({key: key, critical: e.group.urgency === 2, arrival: arrivalOf(key)}) })
  })
  if (rows.length > queueLimit) {
    rows.sort(function(a, b) { return (a.critical ? 1 : 0) - (b.critical ? 1 : 0) || a.arrival - b.arrival })
    evicted = rows.slice(0, rows.length - queueLimit).map(function(row) { return row.key })
    waiting = waiting.filter(function(e) {
      e.group.keys = e.group.keys.filter(function(key) { return evicted.indexOf(key) < 0 })
      if (e.group.keys.length && e.group.keys.indexOf(e.group.key) < 0) e.group.key = e.group.keys[0]
      return e.group.keys.length > 0
    })
  }

  var top = Number(o.top) || 0, bottom = Number(o.bottom) || 0, seq = Number(o.seq) || 0
  var fresh = [], promoted = []
  visible.forEach(function(e) {
    if (e.shown && e.rank !== null) return
    if (!e.shown && e.waited) promoted.push(e)
    else fresh.push(e)
  })
  // Oldest first, so the newest arrival ends on top.
  fresh.sort(function(a, b) { return b.index - a.index })
  fresh.forEach(function(e) { e.rank = ++top })
  // In queue order: the next in line joins right under the stack, so a newer
  // critical sits above an older one and criticals above the other cards.
  promoted.sort(function(a, b) { return waitOrder(a, b) })
  promoted.forEach(function(e) { e.rank = --bottom })
  fresh.concat(promoted).forEach(function(e) { if (!e.shown || e.since === null) e.since = ++seq })
  visible.forEach(function(e) { if (e.since === null) e.since = ++seq })
  visible.sort(function(a, b) { return b.rank - a.rank })
  return {
    visible: visible.map(function(e) { return e.group }),
    ranks: visible.map(function(e) { return e.rank }),
    since: visible.map(function(e) { return e.since }),
    seq: seq,
    waiting: waiting.map(function(e) { return e.group }),
    dropped: dropped,
    evicted: evicted,
    top: top,
    bottom: bottom
  }
}

// ---------------------------------------------------- hold while hovered
//
// Like stock 4.5's deck: while the pointer is over the stack, a new card does
// not enter it, so nothing moves under the pointer. It waits in the overflow
// queue (planStack `held`) until the pointer leaves or the hold's maximum
// runs out, then shows as the arrival it is: on top, criticals first.
//
// Returns the keys to hold, as a map. A group is held when every key in it
// is: held already, or new (neither shown, waiting nor `released`). A group
// on screen, and one that cannot wait (a replay), is never held, and nothing
// is while `holding` is false. A card that waited before the hold keeps its
// turn in the queue and may still join the bottom of the stack.
function heldArrivals(groups, options) {
  var o = options || {}
  var out = {}
  if (!o.holding) return out
  var shown = o.shown || {}, waited = o.waiting || {}, held = o.held || {}, released = o.released || {}
  var canWait = o.canWait || function() { return true }
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i]
    var keys = group.keys && group.keys.length ? group.keys : [group.key]
    if (!canWait(group) || keys.some(function(key) { return !!shown[key] })) continue
    var hold = keys.every(function(key) { return !!held[key] || (!waited[key] && !released[key]) })
    if (hold) keys.forEach(function(key) { out[key] = true })
  }
  return out
}

function popupPlacement(barPosition, barClearance, gapsOut) {
  var position = String(barPosition || "top")
  var clearance = Number(barClearance)
  var gap = Number(gapsOut)
  if (!isFinite(clearance)) clearance = 0
  if (!isFinite(gap)) gap = 0

  return {
    anchors: { top: true, bottom: false, left: false, right: true },
    margins: {
      top: position === "top" ? clearance : gap,
      bottom: gap,
      left: gap,
      right: position === "right" ? clearance : gap
    }
  }
}

// The archived files are the history. They are read back exactly like the
// live popup files, then normalized into history rows: replaying a toast
// must not inherit the original's expire timeout or restore deadline, so it
// gets the standard on-screen lifetime for its urgency instead.
//
// liveRows are the toasts still on screen when the replay was asked for.
// They belong in it — they're the newest notifications there are — but the
// directory read races their archival, so they're carried across by hand and
// keyed by file name (timestamp + id) to drop the copy the read already saw.
function historyRows(raw, liveRows, normalUrgency, limit) {
  var max = limit === undefined || limit === null ? 10 : Number(limit)
  if (isNaN(max)) max = 10
  max = Math.max(0, max)

  var out = []
  var seen = {}
  function collect(rows) {
    for (var i = 0; i < rows.length; i++) {
      var entry = rows[i]
      if (!entry) continue
      var key = popupFileName(entry)
      if (seen[key]) continue
      seen[key] = true
      out.push(historyEntry(entry, normalUrgency))
    }
  }

  collect(Array.isArray(liveRows) ? liveRows : [])
  collect(parsePopupFiles(raw, normalUrgency))
  out.sort(function(a, b) { return (b.timestamp || 0) - (a.timestamp || 0) })
  return out.slice(0, max)
}

if (typeof module !== "undefined") {
  module.exports = {
    isChromiumDerived: isChromiumDerived,
    sanitizeBody: sanitizeBody,
    summaryStartsWithGlyph: summaryStartsWithGlyph,
    shouldBypassDnd: shouldBypassDnd,
    isEphemeralApp: isEphemeralApp,
    stringHint: stringHint,
    notificationWithinLimits: notificationWithinLimits,
    utf8Bytes: utf8Bytes,
    glyphFromHints: glyphFromHints,
    execArgvFromHints: execArgvFromHints,
    parseExecArgv: parseExecArgv,
    parseExecActions: parseExecActions,
    execActionsFromHints: execActionsFromHints,
    deeds: deeds,
    shouldRenderCompactGlyph: shouldRenderCompactGlyph,
    snapshotOf: snapshotOf,
    themeIcon: themeIcon,
    normalizedImage: normalizedImage,
    popupImage: popupImage,
    hasRawImage: hasRawImage,
    popupRoles: popupRoles,
    popupRowChanged: popupRowChanged,
    replacementSnapshot: replacementSnapshot,
    historyEntry: historyEntry,
    parseSettings: parseSettings,
    historyRows: historyRows,
    popupEntry: popupEntry,
    popupFileName: popupFileName,
    imageStem: imageStem,
    localImageFile: localImageFile,
    persistablePopup: persistablePopup,
    serializePopup: serializePopup,
    parsePopupFiles: parsePopupFiles,
    popupExpired: popupExpired,
    planStack: planStack,
    heldArrivals: heldArrivals,
    popupPlacement: popupPlacement
  }
}
