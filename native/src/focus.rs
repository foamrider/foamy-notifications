//! Window selection (`focus`, `focus-configured`) and monitor context
//! (`context`) through `hyprctl`, as the Python helper did it.

use std::io::Read;
use std::net::Ipv6Addr;
use std::time::Duration;

use foamy_helper::child::Bounded;
use serde_json::{Map, Value, json};

use crate::pyjson::{self, numeric, py_eq, py_str, truthy};
use crate::{Error, LIMIT, get, iterate, paths};

/// Largest `hyprctl -j` answer.
const HYPR_LIMIT: usize = 4 * 1024 * 1024;
const HYPR_TIMEOUT: Duration = Duration::from_secs(3);

/// Runs a bounded child and turns timeouts and failures into the errors
/// `subprocess.run(check=True)` raised.
pub fn checked(
    child: Bounded,
    name: &str,
    seconds: u64,
) -> Result<foamy_helper::child::Output, Error> {
    let output = child.run()?;
    if output.timed_out {
        return Err(Error::Process {
            kind: "TimeoutExpired",
            message: format!("Command '{name}' timed out after {seconds} seconds"),
        });
    }
    if output.overflowed {
        return Err(Error::Process {
            kind: "SubprocessError",
            message: format!("Command '{name}' printed too much"),
        });
    }
    if !output.status.success() {
        let code = output.status.code().unwrap_or(-1);
        return Err(Error::Process {
            kind: "CalledProcessError",
            message: format!("Command '{name}' returned non-zero exit status {code}."),
        });
    }
    Ok(output)
}

/// `hyprctl -j <what>`, parsed; at most 4 MiB, 3 s.
pub fn hypr(what: &str) -> Result<Value, Error> {
    let output = Bounded::new("hyprctl")
        .args(["-j", what])
        .timeout(HYPR_TIMEOUT)
        .max_output(HYPR_LIMIT)
        .run()?;
    if output.overflowed {
        return Err(Error::Value("Hyprland response is too large".into()));
    }
    if output.timed_out {
        return Err(Error::Process {
            kind: "TimeoutExpired",
            message: format!("Command 'hyprctl -j {what}' timed out after 3 seconds"),
        });
    }
    if !output.status.success() {
        return Err(Error::Process {
            kind: "CalledProcessError",
            message: format!(
                "Command 'hyprctl -j {what}' returned non-zero exit status {}.",
                output.status.code().unwrap_or(-1)
            ),
        });
    }
    pyjson::loads(&output.stdout).map_err(Error::Value)
}

/// Python's `str.isspace()` (and the regex `\s`).
fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.casefold()`: Unicode lowercase plus the full case foldings that
/// differ from it in common text.
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            'ß' | 'ẞ' => out.push_str("ss"),
            'ς' => out.push('σ'),
            'µ' => out.push('μ'),
            'ſ' => out.push('s'),
            'ŉ' => out.push_str("ʼn"),
            '\u{345}' | '\u{1fbe}' => out.push('ι'),
            'ϐ' => out.push('β'),
            'ϑ' => out.push('θ'),
            'ϕ' => out.push('φ'),
            'ϖ' => out.push('π'),
            'ϰ' => out.push('κ'),
            'ϱ' => out.push('ρ'),
            'ϵ' => out.push('ε'),
            'ẛ' => out.push('ṡ'),
            'ﬀ' => out.push_str("ff"),
            'ﬁ' => out.push_str("fi"),
            'ﬂ' => out.push_str("fl"),
            'ﬃ' => out.push_str("ffi"),
            'ﬄ' => out.push_str("ffl"),
            'ﬅ' | 'ﬆ' => out.push_str("st"),
            c => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// One DNS label: 1–63 of `[a-z0-9-]`, not starting or ending with `-`.
fn label(part: &str) -> bool {
    let bytes = part.as_bytes();
    (1..=63).contains(&bytes.len())
        && bytes
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || *b == b'-')
        && bytes[0] != b'-'
        && bytes[bytes.len() - 1] != b'-'
}

/// A complete hostname line (`(?:label\.)+[a-z]…`, case-insensitive).
fn hostname_line(line: &str) -> bool {
    if line.chars().count() > 253 {
        return false;
    }
    let parts: Vec<&str> = line.split('.').collect();
    parts.len() >= 2
        && parts.iter().all(|part| label(part))
        && parts[parts.len() - 1].as_bytes()[0].is_ascii_alphabetic()
}

fn starts_ci(text: &[char], at: usize, prefix: &str) -> bool {
    let mut i = at;
    for p in prefix.chars() {
        match text.get(i) {
            Some(c) if c.eq_ignore_ascii_case(&p) => i += 1,
            _ => return false,
        }
    }
    true
}

/// `https?://` at `at` (case-insensitive); the index after it.
fn scheme(text: &[char], at: usize) -> Option<usize> {
    if !starts_ci(text, at, "http") {
        return None;
    }
    let mut i = at + 4;
    if matches!(text.get(i), Some('s' | 'S')) && starts_ci(text, i + 1, "://") {
        i += 1;
    }
    starts_ci(text, i, "://").then_some(i + 3)
}

/// The URL of a leading link: `<a … href="https?://…" …>` (the last
/// `href` before the tag ends) or a bare `https?://…` at the start.
fn leading_url(text: &[char]) -> Option<String> {
    let quote = |c: &char| *c == '"' || *c == '\'';
    if text.len() >= 3 && text[0] == '<' && matches!(text[1], 'a' | 'A') && is_space(text[2]) {
        let end = text[2..]
            .iter()
            .position(|c| *c == '>')
            .map_or(text.len(), |i| i + 2);
        for at in (3..end).rev() {
            if !starts_ci(text, at, "href=") || !text.get(at + 5).is_some_and(quote) {
                continue;
            }
            let Some(start) = scheme(text, at + 6) else {
                continue;
            };
            let Some(length) = text[start..].iter().position(quote) else {
                continue;
            };
            if length == 0 || !text[start + length + 1..].contains(&'>') {
                continue;
            }
            return Some(text[at + 6..start + length].iter().collect());
        }
    }
    let start = scheme(text, 0)?;
    let length = text[start..]
        .iter()
        .position(|c| is_space(*c) || *c == '<')
        .unwrap_or(text.len() - start);
    (length > 0).then(|| text[..start + length].iter().collect())
}

fn invalid_ipv6() -> Error {
    Error::Value("Invalid IPv6 URL".into())
}

/// urlsplit's `_check_bracketed_host`: an IPv6 address (with an optional
/// zone) or an IPvFuture literal.
fn check_bracketed_host(host: &str) -> Result<(), Error> {
    if let Some(rest) = host.strip_prefix(['v', 'V']) {
        let (digits, tail) = rest.split_once('.').unwrap_or(("", ""));
        if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_hexdigit()) || tail.is_empty() {
            return Err(Error::Value("IPvFuture address is invalid".into()));
        }
        return Ok(());
    }
    let address = host.split_once('%').map_or(host, |(address, _)| address);
    if address.parse::<Ipv6Addr>().is_err() {
        return Err(Error::Value(format!(
            "'{host}' does not appear to be an IPv4 or IPv6 address"
        )));
    }
    Ok(())
}

/// `urlparse(url).hostname or ''` for an `http(s)://` URL, with urlsplit's
/// bracket checks (which raise).
fn url_hostname(url: &str) -> Result<String, Error> {
    let url: String = url
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let rest = url.split_once(':').map_or("", |(_, rest)| rest);
    let Some(rest) = rest.strip_prefix("//") else {
        return Ok(String::new());
    };
    let netloc = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    if netloc.contains('[') != netloc.contains(']') {
        return Err(invalid_ipv6());
    }
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, host)| host);
    let bracketed_netloc = netloc.contains('[');
    let host = if let Some((before, bracketed)) = hostinfo.split_once('[') {
        let (host, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
        if !before.is_empty() || (!port.is_empty() && !port.starts_with(':')) {
            return Err(invalid_ipv6());
        }
        check_bracketed_host(host)?;
        host
    } else {
        let host = hostinfo.split_once(':').map_or(hostinfo, |(host, _)| host);
        if bracketed_netloc {
            check_bracketed_host(host)?;
        }
        host
    };
    let (name, zone) = host
        .split_once('%')
        .map_or((host, None), |(name, zone)| (name, Some(zone)));
    Ok(match zone {
        Some(zone) => format!("{}%{zone}", name.to_lowercase()),
        None => name.to_lowercase(),
    })
}

/// The browser origin a notification body starts with: a bare hostname on
/// the first line (Vivaldi) or a leading link's host; `''` otherwise.
pub fn origin_of(body: &Value) -> Result<String, Error> {
    let Value::String(body) = body else {
        return Ok(String::new());
    };
    let text = body.trim_start_matches(is_space);
    let first = text.split('\n').next().unwrap_or("").trim_matches(is_space);
    if hostname_line(first) {
        return Ok(first.to_ascii_lowercase());
    }
    let chars: Vec<char> = text.chars().collect();
    match leading_url(&chars) {
        Some(url) => url_hostname(&url),
        None => Ok(String::new()),
    }
}

fn object(value: &Value) -> Result<&Map<String, Value>, Error> {
    value.as_object().ok_or_else(|| {
        Error::Type(format!(
            "'{}' object has no attribute 'get'",
            crate::type_name(value)
        ))
    })
}

fn class_of(client: &Value) -> Result<String, Error> {
    let client = object(client)?;
    Ok(casefold(&py_str(client.get("class").unwrap_or(&json!("")))))
}

fn miss(reason: &'static str, message: &'static str) -> Error {
    Error::Focus { reason, message }
}

/// The address of the one window that belongs to the sender.
pub fn select_window(data: &Map<String, Value>, clients: &Value) -> Result<String, Error> {
    let empty = json!("");
    let app = casefold(&py_str(data.get("app").unwrap_or(&empty)));
    let entry = py_str(data.get("desktopEntry").unwrap_or(&empty));
    let desktop = casefold(entry.strip_suffix(".desktop").unwrap_or(&entry));
    let browser = ["chrom", "vivaldi", "brave", "edge", "opera"]
        .iter()
        .any(|x| app.contains(x));
    let origin = if browser {
        origin_of(data.get("body").unwrap_or(&empty))?
    } else {
        String::new()
    };
    let mut mapped = Value::String(String::new());
    if !origin.is_empty() {
        let mappings = data.get("mappings").cloned().unwrap_or(json!([]));
        for mapping in iterate(&mappings)? {
            let fields = mapping.as_object().ok_or_else(|| {
                Error::Type(format!(
                    "'{}' object is not subscriptable",
                    crate::type_name(&mapping)
                ))
            })?;
            let Value::String(candidate) = get(fields, "origin")? else {
                return Err(Error::Type("'origin' has no attribute 'lower'".into()));
            };
            if candidate.to_lowercase() == origin {
                mapped = get(fields, "windowClass")?.clone();
                break;
            }
        }
    }
    let clients = iterate(clients)?;
    let mut matches = Vec::new();
    if truthy(&mapped) {
        let Value::String(mapped) = &mapped else {
            return Err(Error::Type(
                "windowClass has no attribute 'casefold'".into(),
            ));
        };
        let mapped = casefold(mapped);
        for client in &clients {
            if class_of(client)? == mapped {
                matches.push(client);
            }
        }
    } else if !origin.is_empty() {
        let prefixes =
            ["chrome-", "vivaldi-", "msedge-", "brave-"].map(|p| format!("{p}{origin}__"));
        for client in &clients {
            let class = class_of(client)?;
            if prefixes.iter().any(|p| class.starts_with(p.as_str())) {
                matches.push(client);
            }
        }
        if matches.is_empty() {
            return Err(miss(
                "browser",
                "No window for this browser app; configure browserMappings",
            ));
        }
    } else {
        for client in &clients {
            if (!desktop.is_empty() && class_of(client)? == desktop)
                || (!app.is_empty() && class_of(client)? == app)
            {
                matches.push(client);
            }
        }
        if matches.is_empty() && !app.is_empty() {
            for client in &clients {
                let found = class_of(client)?.contains(&app) || {
                    let fields = object(client)?;
                    fields.get("initialClass") == Some(&json!("org.omarchy.agent"))
                        && casefold(&py_str(fields.get("initialTitle").unwrap_or(&empty)))
                            .contains(&app)
                };
                if found {
                    matches.push(client);
                }
            }
        }
        // Screenshot tools and scripts notify after their window (if any) is gone.
        if matches.is_empty() {
            return Err(miss("none", "The sending app has no window"));
        }
    }
    if matches.len() != 1 {
        if !origin.is_empty() {
            return Err(miss(
                "browser",
                "Window target is ambiguous or unavailable; configure browserMappings",
            ));
        }
        return Err(miss("ambiguous", "Several windows match the sending app"));
    }
    let address = py_str(object(matches[0])?.get("address").unwrap_or(&empty));
    let valid = address
        .strip_prefix("0x")
        .is_some_and(|hex| !hex.is_empty() && hex.bytes().all(|b| b.is_ascii_hexdigit()));
    if !valid {
        return Err(Error::Value("Invalid window address".into()));
    }
    Ok(address)
}

/// `focus`: brings the sender's window forward; `none` when it has none.
pub fn focus(data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let address = match select_window(data, &hypr("clients")?) {
        Ok(address) => address,
        // Nothing to bring forward is not a failure; the caller decides what a click then does.
        Err(Error::Focus { reason: "none", .. }) => {
            out.extend_from_slice(b"none\n");
            return Ok(());
        }
        Err(error) => return Err(error),
    };
    // The address is validated and passed as an argument, never interpolated sender text.
    let lua = format!("hl.dsp.focus({{ window = \"address:{address}\" }})");
    let first = Bounded::new("hyprctl")
        .args(["dispatch", &lua])
        .timeout(HYPR_TIMEOUT)
        .run()?;
    if first.timed_out {
        return Err(Error::Process {
            kind: "TimeoutExpired",
            message: "Command 'hyprctl dispatch' timed out after 3 seconds".into(),
        });
    }
    if !first.status.success() || first.overflowed {
        let legacy = Bounded::new("hyprctl")
            .args(["dispatch", "focuswindow", &format!("address:{address}")])
            .timeout(HYPR_TIMEOUT);
        checked(legacy, "hyprctl dispatch focuswindow", 3)?;
    }
    Ok(())
}

/// `focus-configured`: `focus` with the browser mappings from
/// `~/.config/omarchy/shell.json` (the host reads only that path).
pub fn focus_configured(data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let config = paths::home()?
        .join(".config")
        .join("omarchy")
        .join("shell.json");
    let mut mappings = json!([]);
    if std::fs::metadata(config.path()).is_ok_and(|meta| meta.is_file()) {
        let mut raw = Vec::new();
        std::fs::File::open(config.path())?
            .take(LIMIT as u64 + 1)
            .read_to_end(&mut raw)?;
        if raw.len() > LIMIT {
            return Err(Error::Value(
                "Notification settings exceed the size limit".into(),
            ));
        }
        let settings = pyjson::loads(&raw).map_err(Error::Value)?;
        let settings = object(&settings)?;
        let plugins = settings.get("plugins").cloned().unwrap_or(json!([]));
        let entry = iterate(&plugins)?
            .into_iter()
            .find(|plugin| {
                plugin
                    .as_object()
                    .is_some_and(|p| p.get("id") == Some(&json!("foamy.notifications")))
            })
            .unwrap_or(json!({}));
        mappings = entry.get("browserMappings").cloned().unwrap_or(json!([]));
        let valid = mappings.as_array().is_some_and(|list| {
            list.len() <= 32
                && list.iter().all(|m| {
                    m.as_object().is_some_and(|m| {
                        m.get("origin").is_some_and(Value::is_string)
                            && m.get("windowClass").is_some_and(Value::is_string)
                    })
                })
        });
        if !valid {
            return Err(Error::Value("Invalid browserMappings settings".into()));
        }
    }
    let empty = json!("");
    let mut request = Map::new();
    for name in ["app", "desktopEntry", "body"] {
        request.insert(name.into(), data.get(name).unwrap_or(&empty).clone());
    }
    request.insert("mappings".into(), mappings);
    focus(&request, out)
}

fn number(value: &Value, what: &str) -> Result<f64, Error> {
    numeric(value).ok_or_else(|| {
        Error::Type(format!(
            "unsupported operand type for {what}: '{}'",
            crate::type_name(value)
        ))
    })
}

fn nested_id(map: &Map<String, Value>, key: &str) -> Result<Value, Error> {
    match map.get(key) {
        None => Ok(Value::Null),
        Some(inner) => Ok(object(inner)?.get("id").cloned().unwrap_or(Value::Null)),
    }
}

/// `context`: which monitors show a fullscreen window (`fullscreen`) and
/// which one holds the pointer (`pointer`).
pub fn context(data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let monitors = hypr("monitors")?;
    let clients = if data.get("fullscreen").is_some_and(truthy) {
        hypr("clients")?
    } else {
        json!([])
    };
    let point = if data.get("pointer").is_some_and(truthy) {
        hypr("cursorpos")?
    } else {
        json!({})
    };
    let point = object(&point)?;
    let far = json!(-1e9);
    let mut fullscreen = Vec::new();
    let mut pointer = json!("");
    let clients = iterate(&clients)?;
    for monitor in iterate(&monitors)? {
        let monitor = object(&monitor)?;
        let mut workspace = nested_id(monitor, "specialWorkspace")?;
        if !truthy(&workspace) {
            workspace = nested_id(monitor, "activeWorkspace")?;
        }
        let mut full = false;
        for client in &clients {
            let client = object(client)?;
            if py_eq(&nested_id(client, "workspace")?, &workspace)
                && client
                    .get("fullscreen")
                    .is_some_and(|f| py_eq(f, &json!(2)))
            {
                full = true;
                break;
            }
        }
        if full {
            fullscreen.push(get(monitor, "name")?.clone());
        }
        let scale = monitor
            .get("scale")
            .filter(|s| truthy(s))
            .cloned()
            .unwrap_or(json!(1));
        let scale = number(&scale, "/")?;
        let mut width = number(get(monitor, "width")?, "/")? / scale;
        let mut height = number(get(monitor, "height")?, "/")? / scale;
        let transform = number(monitor.get("transform").unwrap_or(&json!(0)), "%")?;
        if transform.rem_euclid(2.0) != 0.0 {
            std::mem::swap(&mut width, &mut height);
        }
        let x = number(get(monitor, "x")?, "<=")?;
        let px = number(point.get("x").unwrap_or(&far), "<=")?;
        if x <= px && px < x + width {
            let y = number(get(monitor, "y")?, "<=")?;
            let py = number(point.get("y").unwrap_or(&far), "<=")?;
            if y <= py && py < y + height {
                pointer = get(monitor, "name")?.clone();
            }
        }
    }
    let reply = json!({"fullscreen": fullscreen, "pointer": pointer});
    out.extend_from_slice(pyjson::dumps(&reply).as_bytes());
    out.push(b'\n');
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn origin(body: &str) -> String {
        origin_of(&json!(body)).unwrap()
    }

    #[test]
    fn origins() {
        assert_eq!(origin("teams.microsoft.com\nAlex"), "teams.microsoft.com");
        assert_eq!(
            origin(" \u{1c}TEAMS.Microsoft.COM \r\nx"),
            "teams.microsoft.com"
        );
        assert_eq!(
            origin("<a href=\"https://Teams.Microsoft.com/\">t</a>"),
            "teams.microsoft.com"
        );
        assert_eq!(origin("<a\thref='http://x.example:80/'>"), "x.example");
        assert_eq!(
            origin("<a x href=\"https://a.example/\" href=\"https://b.example/\">"),
            "b.example"
        );
        assert_eq!(origin("<a href=\"https://a>b.example/\">"), "a>b.example");
        assert_eq!(origin("https://user@h.example:1/x y"), "h.example");
        assert_eq!(origin("https://[::1]:80/"), "::1");
        assert!(origin_of(&json!("https://[::1/")).is_err());
        assert!(origin_of(&json!("https://[nope]/")).is_err());
        assert_eq!(origin("httpx://a.example"), "");
        assert_eq!(origin("https://"), "");
        assert_eq!(origin("<a href=\"https://a.example/\""), "");
        assert_eq!(origin("x.com/path"), "");
        assert_eq!(origin("a-.com"), "");
        assert_eq!(origin("1.2"), "");
        assert_eq!(origin_of(&json!(5)).unwrap(), "");
    }

    #[test]
    fn casefolds() {
        assert_eq!(casefold("Straße ΣΑΣ ﬁ"), "strasse σασ fi");
    }
}
