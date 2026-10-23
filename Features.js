.pragma library
.import "Detect.js" as Detect
.import "Security.js" as Security
.import "BrowserIdentity.js" as BrowserIdentity

// Decode only text entities; decoded markup is never passed to a renderer.
function plain(text) {
  return String(text || "").replace(/<[^>]*>/g, " ").replace(/&#(x[0-9a-f]+|[0-9]+);/gi, function(_, n) {
    var code = n.charAt(0).toLowerCase() === "x" ? parseInt(n.slice(1), 16) : Number(n)
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : " "
  }).replace(/&(amp|lt|gt|quot|apos|nbsp);/g, function(_, n) {
    return {amp:"&",lt:"<",gt:">",quot:'"',apos:"'",nbsp:" "}[n]
  }).replace(/<[^>]*>/g, " ")
}
function offers(row) {
  var body = String(row.body || "")
  // A browser's leading origin identifies the sender, not an offered destination.
  if (BrowserIdentity.hostname(row)) body = body.replace(/^\s*<a\b[^>]*>[^<]*<\/a>\s*(?:<br\s*\/?>)?/i, "").replace(/^\s*(?:https?:\/\/[^\s<]+|[a-z0-9.-]+)\s*\n/i, "")
  var found = Detect.scan(plain(row.summary), plain(body))
  if (!found.link) {
    var anchor = /<a\b[^>]*href=["']([^"']+)["']/i.exec(body)
    if (anchor) {
      found.link = Security.safeHttpUrl(plain(anchor[1]))
      found.meeting = ["meet.google.com", "teams.microsoft.com", "teams.live.com", "zoom.us"].indexOf(Security.hostOf(found.link)) >= 0 || /\.zoom\.us$/.test(Security.hostOf(found.link))
    }
  }
  return found
}
function redact(row) {
  var codes = Detect.codes(plain(String(row.summary || "") + " " + String(row.body || "")))
  var out = Object.assign({}, row)
  if (!codes.length) return out
  // Metadata and image/command paths can repeat the secret: save a placeholder only.
  for (var key in out) if (typeof out[key] === "string") out[key] = ""
  out.summary = "Verification code"
  out.body = "Code hidden from history"
  out.actionsJson = "[]"
  return out
}
function source(row) {
  var host = BrowserIdentity.hostname(row)
  return host ? "web:" + host : "app:" + String(row.app || "Unknown app").slice(0, 256)
}
function quietState(raw, now) {
  var result = {}
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return result
  Object.keys(raw).slice(0, 128).forEach(function(key) {
    var until = raw[key]
    if ((key === "*" || /^(app|web):.{1,256}$/.test(key)) && typeof until === "number" && isFinite(until) && until > now && until <= now + 86400000)
      result[key] = until
  })
  return result
}
function isSharing(properties) {
  return properties && properties["media.class"] === "Video/Source" && String(properties["media.name"] || "").indexOf("xdph-streaming-") === 0
}
