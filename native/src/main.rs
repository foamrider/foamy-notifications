//! `foamy-notifications-helper`: Foamy Notifications' persistence, window
//! focus, monitor context and favicon helper. It replaces
//! `bin/notification-helper.py` and `bin/browser-favicon.py` with the same
//! contract.
//!
//! # Invocation
//!
//! `foamy-notifications-helper <op>` with the umask 077, where `<op>` is:
//!
//! - `serve`: the long-lived worker (`Service.qml`). It first creates
//!   `$XDG_STATE_HOME/foamy-notifications/{,popups,history,images}` (default
//!   `~/.local/state`, a set variable is used as is), mode 0700 (chmod'ed).
//!   Then each standard input line of at most 135168 bytes (131072 + 4096,
//!   newline included) is one job `{"id": …, "op": "<op>", "data": {…}}`
//!   and gets one reply line `{"id": …, "ok": bool, "out": "<what the op
//!   printed>", "error": "<message or ''>"}` (Python `json.dumps` format,
//!   ASCII only), plus `"reason"` (`ambiguous` or `browser`) for a failed
//!   focus. A longer line, or a last line without a newline, is discarded
//!   and answered with `id` null and `Notification exceeds the storage
//!   limit`; malformed JSON, a non-object job, a non-object `data` (a falsy
//!   one is `{}`) and an unknown `op` are answered as failed with the id (if
//!   any). `out` is empty when the job failed. End of input exits 0.
//! - `favicon`: `foamy_helper::favicon::run()`, the contract of
//!   `bin/browser-favicon.py` (a request object of at most 4096 bytes on
//!   standard input, one `{"source": …, "status": …}` line).
//! - any other op runs once: its `data` is one JSON object of at most
//!   131072 bytes on standard input; the op prints to standard output.
//!   A failure (also a missing or unknown op, read before nothing else)
//!   prints `foamy.notifications: <message>` on standard error and exits 1.
//!
//! # Operations
//!
//! Keys are `<timestamp>-<originalId>` (`[0-9]+-[0-9]+`, from Python's
//! `str()` of the two fields). Files are written atomically (temp file,
//! fsync, rename, mode 0600; missing directories are created 0700).
//!
//! - `write {entry, history?, copies?, sources?, imageVersion?}`: drops
//!   `actionsJson`, normalizes the images (below) and writes
//!   `popups/<key>.json` or `history/<key>.json` (`json.dumps` + newline).
//!   A history write removes the popup file and trims history to the newest
//!   100 (by timestamp, then name), with their images. With `sources` it
//!   prints `{"key", "version": imageVersion (default 0), "sources",
//!   "appIcon", "image"}`. `copies` (`[{from, to}]`) maps
//!   `file://images/<key>-appIcon|image` to the file actually read.
//! - `read {history?}`: prints the newest 1000 valid rows of popups or
//!   history (regular files of at most 131072 bytes, objects with a string
//!   `summary`, a truthy `timestamp` and a key equal to the file name),
//!   normalized; a row that changed is rewritten first.
//! - `forget {keys}` (≤ 100): appends the keys to the `handled` journal
//!   (the previous 64 KiB read, invalid and repeated keys dropped, the
//!   newest 1000 kept, one per line), then drops them.
//! - `archive {keys ≤ 2048 | key}`: moves `popups/<key>.json` (a regular
//!   file, not a symlink) to history, then trims history.
//! - `delete {keys ≤ 2048 | key}`: removes the popup file and every
//!   `images/<key>-*`.
//! - `clear {}`: removes every history file and its images.
//! - `drop {before: int ≥ 0}` drops every popup and history key with a
//!   timestamp ≤ `before`; `drop {keys ≤ 100}` drops those keys (files and
//!   images).
//! - `sweep {}`: removes `images/*.tmp`, `images/.foamy-*` and image files
//!   of keys that have neither a popup nor a history file.
//! - `migrate {}`: once (marker `.migrated-from-omarchy-notifications`),
//!   moves Foamy's 4.0 files from `$XDG_STATE_HOME/omarchy/notifications`
//!   (`<key>.json`, `history/<key>.json`, `images/<key>-<role>[-<sha>.png]`;
//!   stock files stay) and writes `state.json` (`{"version": 5, "dnd",
//!   "snoozes"}` from `omarchy/notifications.json`, else `quiet.json`).
//!   Prints `{"migrated": true, "moved": n}` or `{"migrated": false}`.
//! - `focus {app, desktopEntry, body, mappings}`: reads `hyprctl -j clients`
//!   and picks exactly one window: for a Chromium-family sender whose body
//!   starts with a hostname line or a link, the `mappings` window class or a
//!   `chrome-|vivaldi-|msedge-|brave-<origin>__*` class; otherwise the
//!   desktop entry or app as class (exact, casefolded), then as a substring
//!   or an `org.omarchy.agent` window title. Then
//!   `hyprctl dispatch 'hl.dsp.focus({ window = "address:0x…" })'` (the
//!   address checked against `0x[0-9a-fA-F]+`), and on failure
//!   `hyprctl dispatch focuswindow address:0x…`. A sender without a window
//!   prints `none` and succeeds; several windows fail with reason
//!   `ambiguous`, an unresolved browser origin with reason `browser`.
//! - `focus-configured {app, desktopEntry, body}`: `focus` with the
//!   `browserMappings` (≤ 32) of `foamy.notifications` in
//!   `~/.config/omarchy/shell.json` (≤ 131072 bytes).
//! - `context {fullscreen?, pointer?}`: `hyprctl -j monitors` (and
//!   `clients`, `cursorpos` when asked); prints `{"fullscreen": [monitor
//!   names with a fullscreen (2) window on their active or special
//!   workspace], "pointer": "<monitor under the cursor, scaled and
//!   rotated>"}`.
//!
//! Every `hyprctl` call has a 3 s deadline and at most 4 MiB of output.
//!
//! # Images
//!
//! An `appIcon` that is a theme icon name (`[A-Za-z0-9_.-]+`) stays. Any
//! other `appIcon`/`image` becomes `''` unless it is an absolute path or a
//! `file://` URI without host, query or fragment of a regular file (no
//! final symlink, no FIFO) of at most 5 MiB. That file is converted to
//! `images/<key>-<role>-<sha256 of the input>.png` (reused when present and
//! valid; older PNGs of the same key and role are removed): `file -b
//! --mime-type` (2 s) picks the coder (PNG, JPEG, GIF, WEBP, BMP, ICO, SVG
//! through RSVG on stdin), then in a private scratch directory `magick
//! -background none <coder>:<input>[0] -auto-orient -resize 720x720>
//! -strip PNG:<output>` with `MAGICK_CONFIGURE_PATH=<plugin>/bin`
//! (`bin/policy.xml`), the working directory an empty directory, 5 s, and
//! rlimits core 0, address space 512 MiB, file size 5 MiB, CPU 5 s. The
//! result must be a PNG of 1–720 px per side. A rejected image prints
//! `foamy.notifications: <role> rejected: <kind>` on standard error and
//! keeps the text. `imageFormat` becomes 1.
//!
//! Children run in their own process group, are killed as a group at the
//! deadline, and die with the helper. A panic exits 101 (no core dump).

use std::ffi::OsStr;
use std::fs::{self, DirBuilder};
use std::io::{self, BufRead, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::DirBuilderExt;
use std::process::ExitCode;

use serde_json::{Map, Value, json};

mod focus;
mod images;
mod ops;
mod paths;
mod pyjson;
mod sha;

use paths::PyPath;

/// Largest one-shot request and notification file.
pub const LIMIT: usize = 131072;
/// Largest `serve` line, newline included.
const LINE_LIMIT: usize = LIMIT + 4096;

/// What the Python helper raised; `message` is its `str()`.
#[derive(Debug)]
pub enum Error {
    /// ValueError (also JSON errors).
    Value(String),
    /// KeyError: the key's repr.
    Key(String),
    /// TypeError or AttributeError.
    Type(String),
    /// OSError and its subclasses.
    Os(io::Error),
    /// A child process failed (`CalledProcessError`, `TimeoutExpired`).
    Process { kind: &'static str, message: String },
    /// No single window to focus.
    Focus {
        reason: &'static str,
        message: &'static str,
    },
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Error {
        Error::Os(error)
    }
}

impl Error {
    pub fn message(&self) -> String {
        match self {
            Error::Value(message) | Error::Type(message) | Error::Key(message) => message.clone(),
            Error::Os(error) => error.to_string(),
            Error::Process { message, .. } => message.clone(),
            Error::Focus { message, .. } => (*message).into(),
        }
    }

    /// The Python exception type's name.
    pub fn kind(&self) -> &'static str {
        match self {
            Error::Value(_) => "ValueError",
            Error::Key(_) => "KeyError",
            Error::Type(_) => "TypeError",
            Error::Os(error) => match error.kind() {
                io::ErrorKind::NotFound => "FileNotFoundError",
                io::ErrorKind::PermissionDenied => "PermissionError",
                io::ErrorKind::AlreadyExists => "FileExistsError",
                io::ErrorKind::IsADirectory => "IsADirectoryError",
                io::ErrorKind::NotADirectory => "NotADirectoryError",
                _ => "OSError",
            },
            Error::Process { kind, .. } => kind,
            Error::Focus { .. } => "FocusMiss",
        }
    }
}

/// Python's type name of a JSON value.
pub fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(number) if number.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// `map[key]`, a KeyError when missing.
pub fn get<'a>(map: &'a Map<String, Value>, key: &str) -> Result<&'a Value, Error> {
    map.get(key).ok_or_else(|| Error::Key(format!("'{key}'")))
}

/// What `for item in value` yields in Python: list items, the characters
/// of a string, the keys of a dict.
pub fn iterate(value: &Value) -> Result<Vec<Value>, Error> {
    match value {
        Value::Array(items) => Ok(items.clone()),
        Value::String(text) => Ok(text.chars().map(|c| Value::String(c.into())).collect()),
        Value::Object(map) => Ok(map.keys().map(|k| Value::String(k.clone())).collect()),
        other => Err(Error::Type(format!(
            "'{}' object is not iterable",
            type_name(other)
        ))),
    }
}

/// `[0-9]+-[0-9]+`.
pub fn is_key(text: &str) -> bool {
    let digits = |part: &str| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit());
    text.split_once('-')
        .is_some_and(|(a, b)| digits(a) && digits(b))
}

/// `mkdir(parents=True, exist_ok=True, mode=0o700)`.
pub fn mkdir_p(path: &PyPath) -> Result<(), Error> {
    DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path.path())?;
    Ok(())
}

/// `unlink(missing_ok=True)`.
pub fn remove_missing_ok(path: &PyPath) -> Result<(), Error> {
    match fs::remove_file(path.path()) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error.into()),
        _ => Ok(()),
    }
}

/// The entries of a directory as `(name, path)`; none when it can't be read.
pub fn list_dir(dir: &PyPath) -> Vec<(Vec<u8>, PyPath)> {
    let Ok(entries) = fs::read_dir(dir.path()) else {
        return Vec::new();
    };
    entries
        .flatten()
        .map(|entry| {
            let name = entry.file_name().as_bytes().to_vec();
            let path = dir.join_bytes(&name);
            (name, path)
        })
        .collect()
}

/// Writes `data` to `path` atomically, mode 0600, creating the directory.
pub fn atomic(path: &PyPath, data: &[u8]) -> Result<(), Error> {
    mkdir_p(&path.parent())?;
    foamy_helper::fs::write_atomic(path.path(), data, 0o600)?;
    Ok(())
}

type Operation = fn(&Map<String, Value>, &mut Vec<u8>) -> Result<(), Error>;

fn operation(name: &str) -> Option<Operation> {
    Some(match name {
        "write" => ops::write,
        "read" => ops::read,
        "forget" => ops::forget,
        "focus" => focus::focus,
        "focus-configured" => focus::focus_configured,
        "context" => focus::context,
        "archive" => ops::archive,
        "delete" => ops::delete,
        "clear" => ops::clear,
        "drop" => ops::drop,
        "sweep" => ops::sweep,
        "migrate" => ops::migrate,
        _ => return None,
    })
}

/// `OPERATIONS[op]` for a JSON `op`: a KeyError with its repr, or a
/// TypeError for a list or object.
fn lookup(op: &Value) -> Result<Operation, Error> {
    match op {
        Value::String(name) => operation(name).ok_or_else(|| Error::Key(pyjson::repr(op))),
        Value::Array(_) | Value::Object(_) => {
            Err(Error::Type(format!("unhashable type: '{}'", type_name(op))))
        }
        other => Err(Error::Key(pyjson::repr(other))),
    }
}

/// `readline(limit)`: up to `limit` bytes, through the first newline.
fn read_line<R: BufRead>(input: &mut R, limit: usize) -> io::Result<Vec<u8>> {
    let mut line = Vec::new();
    while line.len() < limit {
        let buffer = match input.fill_buf() {
            Ok(buffer) => buffer,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        if buffer.is_empty() {
            break;
        }
        let room = limit - line.len();
        let take = buffer
            .iter()
            .take(room)
            .position(|&b| b == b'\n')
            .map_or(room.min(buffer.len()), |i| i + 1);
        line.extend_from_slice(&buffer[..take]);
        input.consume(take);
        if line.ends_with(b"\n") {
            break;
        }
    }
    Ok(line)
}

fn job(line: &[u8], reply: &mut Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let job = pyjson::loads(line).map_err(Error::Value)?;
    let Value::Object(job) = job else {
        return Err(Error::Type(format!(
            "'{}' object has no attribute 'get'",
            type_name(&job)
        )));
    };
    reply.insert("id".into(), job.get("id").cloned().unwrap_or(Value::Null));
    let data = job
        .get("data")
        .filter(|d| pyjson::truthy(d))
        .cloned()
        .unwrap_or(json!({}));
    let Value::Object(data) = data else {
        return Err(Error::Value("Expected an object".into()));
    };
    let run = lookup(job.get("op").unwrap_or(&Value::Null))?;
    run(&data, out)
}

/// The long-lived worker: one JSON reply line per job line.
fn serve() -> Result<(), Error> {
    ops::private_dirs()?;
    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();
    loop {
        let mut line = read_line(&mut input, LINE_LIMIT)?;
        if line.is_empty() {
            return Ok(());
        }
        let mut reply = Map::new();
        reply.insert("id".into(), Value::Null);
        reply.insert("ok".into(), json!(false));
        reply.insert("out".into(), json!(""));
        reply.insert("error".into(), json!(""));
        let mut out = Vec::new();
        let outcome = if line.ends_with(b"\n") {
            job(&line, &mut reply, &mut out)
        } else {
            // Oversized: discard the rest of the line, then answer the job as failed.
            while !line.is_empty() && !line.ends_with(b"\n") {
                line = read_line(&mut input, LINE_LIMIT)?;
            }
            Err(Error::Value(
                "Notification exceeds the storage limit".into(),
            ))
        };
        match outcome {
            Ok(()) => {
                reply.insert("ok".into(), json!(true));
                reply.insert(
                    "out".into(),
                    Value::String(String::from_utf8_lossy(&out).into_owned()),
                );
            }
            Err(error) => {
                let message = error.message();
                let message = if message.is_empty() {
                    error.kind().to_string()
                } else {
                    message
                };
                reply.insert("error".into(), Value::String(message));
                if let Error::Focus { reason, .. } = error {
                    reply.insert("reason".into(), json!(reason));
                }
            }
        }
        let mut text = pyjson::dumps(&Value::Object(reply)).into_bytes();
        text.push(b'\n');
        output.write_all(&text)?;
        output.flush()?;
    }
}

/// A one-shot op: the request from standard input, output on standard output.
fn once(op: &OsStr) -> Result<(), Error> {
    let mut raw = Vec::new();
    io::stdin()
        .lock()
        .take(LIMIT as u64 + 1)
        .read_to_end(&mut raw)?;
    if raw.len() > LIMIT {
        return Err(Error::Value(
            "Notification exceeds the storage limit".into(),
        ));
    }
    let Value::Object(data) = pyjson::loads(&raw).map_err(Error::Value)? else {
        return Err(Error::Value("Expected an object".into()));
    };
    let name = op.to_str().unwrap_or("");
    let run =
        operation(name).ok_or_else(|| Error::Key(pyjson::repr(&json!(op.to_string_lossy()))))?;
    let mut out = Vec::new();
    let result = run(&data, &mut out);
    let mut stdout = io::stdout().lock();
    stdout.write_all(&out)?;
    stdout.flush()?;
    result
}

fn main() -> ExitCode {
    foamy_helper::process::exit_on_panic(101);
    let Some(op) = std::env::args_os().nth(1) else {
        foamy_helper::process::set_umask(0o077);
        eprintln!("foamy.notifications: list index out of range");
        return ExitCode::FAILURE;
    };
    if op == "favicon" {
        return foamy_helper::favicon::run();
    }
    foamy_helper::process::set_umask(0o077);
    let result = if op == "serve" { serve() } else { once(&op) };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("foamy.notifications: {}", error.message());
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys() {
        assert!(is_key("1-2"));
        assert!(is_key("0012-30"));
        for bad in ["1", "-1", "1-", "1-2-3", "a-1", "1-2\n", "١-1", "1.0-2"] {
            assert!(!is_key(bad), "{bad}");
        }
    }

    #[test]
    fn lines_are_bounded_like_readline() {
        let mut input = io::Cursor::new(b"ab\ncdefgh\n\nx".to_vec());
        assert_eq!(read_line(&mut input, 4).unwrap(), b"ab\n");
        assert_eq!(read_line(&mut input, 4).unwrap(), b"cdef");
        assert_eq!(read_line(&mut input, 4).unwrap(), b"gh\n");
        assert_eq!(read_line(&mut input, 4).unwrap(), b"\n");
        assert_eq!(read_line(&mut input, 4).unwrap(), b"x");
        assert_eq!(read_line(&mut input, 4).unwrap(), b"");
    }

    #[test]
    fn op_lookup_errors_read_like_python() {
        assert_eq!(lookup(&json!("nope")).err().unwrap().message(), "'nope'");
        assert_eq!(lookup(&json!(null)).err().unwrap().message(), "None");
        assert_eq!(
            lookup(&json!(["read"])).err().unwrap().message(),
            "unhashable type: 'list'"
        );
        assert!(lookup(&json!("read")).is_ok());
    }
}
