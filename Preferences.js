function defaults() {
  return { lowTimeoutSec: 5, normalTimeoutSec: 8, criticalTimeoutSec: 0,
    maxVisible: 3, groupDuplicates: true, monitor: "focused", monitorName: "",
    suppressFullscreen: false, fullscreenCritical: true, width: 390,
    useBrowserFavicons: true, browserGrouping: "browser", browserMappings: [], restoreMaxAgeSec: 300, showImages: true, imageSize: 56, compact: true, showTimeoutIndicator: true }
}
function parse(raw) {
  var settings = defaults()
  try {
    var config = JSON.parse(raw)
    var entries = Array.isArray(config.plugins) ? config.plugins : []
    var entry = entries.filter(function(e) { return e && e.id === "foamy.notifications" })[0] || {}
    var numbers = { lowTimeoutSec: [1,120], normalTimeoutSec: [1,120], criticalTimeoutSec: [0,120], maxVisible: [1,10], width: [280,600], restoreMaxAgeSec: [0,3600], imageSize: [40,72] }
    for (var key in numbers) {
      if (entry[key] === undefined) continue
      var range = numbers[key]
      if (typeof entry[key] !== "number" || !isFinite(entry[key]) || Math.floor(entry[key]) !== entry[key] || entry[key] < range[0] || entry[key] > range[1]) throw Error("Invalid " + key)
      settings[key] = entry[key]
    }
    for (var name of ["groupDuplicates", "suppressFullscreen", "fullscreenCritical", "showImages", "compact", "showTimeoutIndicator", "useBrowserFavicons"]) {
      if (entry[name] === undefined) continue
      if (typeof entry[name] !== "boolean") throw Error("Invalid " + name)
      settings[name] = entry[name]
    }
    if (entry.browserGrouping !== undefined) {
      if (["browser", "hostname", "none"].indexOf(entry.browserGrouping) < 0) throw Error("Invalid browserGrouping")
      settings.browserGrouping = entry.browserGrouping
    }
    if (entry.monitor !== undefined) {
      if (["focused","pointer","named","all"].indexOf(entry.monitor) < 0) throw Error("Invalid monitor")
      settings.monitor = entry.monitor
    }
    if (entry.monitorName !== undefined) {
      if (typeof entry.monitorName !== "string" || entry.monitorName.length > 128) throw Error("Invalid monitorName")
      settings.monitorName = entry.monitorName
    }
    if (settings.monitor === "named" && !settings.monitorName) throw Error("monitorName is required for a named monitor")
    if (entry.browserMappings !== undefined) {
      if (!Array.isArray(entry.browserMappings) || entry.browserMappings.length > 32) throw Error("Invalid browserMappings")
      entry.browserMappings.forEach(function(m) {
        if (!m || typeof m.origin !== "string" || !/^[a-z0-9.-]+$/i.test(m.origin) || typeof m.windowClass !== "string" || !m.windowClass || m.windowClass.length > 256) throw Error("Each browser mapping needs an origin hostname and exact windowClass")
      })
      settings.browserMappings = entry.browserMappings
    }
    return { settings: settings, error: "" }
  } catch(e) { return { settings: null, error: String(e) } }
}
function duration(settings, urgency, requested) {
  var seconds = urgency === 2 ? settings.criticalTimeoutSec : urgency === 0 ? settings.lowTimeoutSec : settings.normalTimeoutSec
  if (urgency === 2) return seconds * 1000
  // Preserve sender-requested reading time within a bounded maximum.
  return Math.min(120000, Math.max(seconds * 1000, Number(requested) || 0))
}
function groupKey(row) {
  // Different click targets must never merge, even if their visible text matches.
  return JSON.stringify([row.app,row.desktopEntry,row.summary,row.body,row.appIcon,row.image,row.urgency,row.execArgv,row.actionsJson])
}
function groups(rows, enabled, browserGrouping, browserIdentity) {
  browserIdentity = browserIdentity || (typeof require === "function" ? require("./BrowserIdentity.js") : null)
  var result = [], seen = {}
  rows.forEach(function(row) {
    var identity = String(row.timestamp) + "-" + String(row.originalId)
    var isBrowser = browserIdentity && browserIdentity.browser(row)
    var key = enabled && !(isBrowser && browserGrouping === "none") ? groupKey(row) : identity
    if (seen[key] !== undefined) { result[seen[key]].keys.push(identity); return }
    seen[key] = result.length
    var copy = {}
    for (var field in row) copy[field] = row[field]
    copy.key = identity
    copy.keys = [identity]
    result.push(copy)
  })
  return result
}
function targetNames(settings, names, focused, pointer) {
  if (settings.monitor === "all") return names.slice()
  var name = settings.monitor === "named" ? settings.monitorName : settings.monitor === "pointer" ? pointer : focused
  return [names.indexOf(name) >= 0 ? name : names.indexOf(focused) >= 0 ? focused : names[0]].filter(Boolean)
}
if (typeof module !== "undefined") module.exports = {defaults:defaults,parse:parse,duration:duration,groups:groups,targetNames:targetNames}
