//! The persistence operations: popups, history, images, the handled
//! journal, sweeping and the one-time 4.0 migration.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

use serde_json::{Map, Value, json};

use crate::images::{image_name, legacy_name, normalize_image, sweepable};
use crate::paths::{self, PyPath, local_image};
use crate::pyjson::{self, py_eq, py_str, truthy};
use crate::{Error, LIMIT, atomic, get, is_key, iterate, list_dir, mkdir_p, remove_missing_ok};

const HISTORY_LIMIT: usize = 100;
const HANDLED_LIMIT: usize = 1000;
const READ_LIMIT: usize = 1000;
const MARKER: &str = ".migrated-from-omarchy-notifications";

fn popups() -> Result<PyPath, Error> {
    Ok(paths::base()?.join("popups"))
}

fn history() -> Result<PyPath, Error> {
    Ok(paths::base()?.join("history"))
}

fn images() -> Result<PyPath, Error> {
    Ok(paths::base()?.join("images"))
}

/// The base, popups, history and images directories, mode 0700.
pub fn private_dirs() -> Result<(), Error> {
    let base = paths::base()?;
    for folder in [base.clone(), popups()?, history()?, images()?] {
        mkdir_p(&folder)?;
        fs::set_permissions(folder.path(), fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn exists(path: &PyPath) -> bool {
    fs::metadata(path.path()).is_ok()
}

fn is_symlink(path: &PyPath) -> bool {
    fs::symlink_metadata(path.path()).is_ok_and(|meta| meta.file_type().is_symlink())
}

fn is_file(path: &PyPath) -> bool {
    fs::metadata(path.path()).is_ok_and(|meta| meta.is_file())
}

fn is_dir(path: &PyPath) -> bool {
    fs::metadata(path.path()).is_ok_and(|meta| meta.is_dir())
}

/// Up to `limit` bytes of a file (opened without blocking on a FIFO).
fn read_prefix(path: &PyPath, limit: usize) -> io::Result<Vec<u8>> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open(path.path())?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "not a regular file",
        ));
    }
    let mut data = Vec::new();
    file.take(limit as u64).read_to_end(&mut data)?;
    Ok(data)
}

/// `<key>.json` entries of `folder` (a missing folder has none), the key
/// matching `[0-9]+-[0-9]+`.
fn notification_files(folder: &PyPath) -> Vec<(String, PyPath)> {
    list_dir(folder)
        .into_iter()
        .filter_map(|(name, path)| {
            let stem = std::str::from_utf8(name.strip_suffix(b".json")?).ok()?;
            is_key(stem).then(|| (stem.to_string(), path))
        })
        .collect()
}

/// The timestamp part of a key as a number, compared without overflow.
fn stamp(key: &str) -> &str {
    let digits = key.split('-').next().unwrap_or("");
    let trimmed = digits.trim_start_matches('0');
    if trimmed.is_empty() { "0" } else { trimmed }
}

fn stamp_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    let (a, b) = (stamp(a), stamp(b));
    a.len().cmp(&b.len()).then_with(|| a.cmp(b))
}

/// Newest first: by timestamp, then by file name, descending.
fn newest_first(files: &mut [(String, PyPath)]) {
    files.sort_by(|a, b| stamp_cmp(&b.0, &a.0).then_with(|| b.1.name().cmp(a.1.name())));
}

fn key_of(entry: &Map<String, Value>) -> Result<String, Error> {
    Ok(format!(
        "{}-{}",
        py_str(get(entry, "timestamp")?),
        py_str(get(entry, "originalId")?)
    ))
}

fn line(value: &Value) -> Vec<u8> {
    let mut text = pyjson::dumps(value).into_bytes();
    text.push(b'\n');
    text
}

fn theme_icon(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

/// Replaces `appIcon` and `image` with private normalized PNG URIs (or
/// `''`), keeps theme icon names, and sets `imageFormat` 1.
fn normalize_entry(entry: &mut Map<String, Value>, copies: Option<&Value>) -> Result<(), Error> {
    let key = key_of(entry)?;
    if !is_key(&key) {
        return Err(Error::Value("Invalid notification identity".into()));
    }
    let images = images()?;
    let mut sources: Vec<(String, Value)> = Vec::new();
    if let Some(copies) = copies {
        for pair in iterate(copies)? {
            let pair = pair.as_object().ok_or_else(|| {
                Error::Type(format!(
                    "'{}' object is not subscriptable",
                    crate::type_name(&pair)
                ))
            })?;
            let Value::String(to) = get(pair, "to")? else {
                return Err(Error::Type(
                    "expected str, bytes or os.PathLike object".into(),
                ));
            };
            let destination = PyPath::new(to.as_bytes());
            let name = destination.name();
            if destination.parent() != images
                || (name != format!("{key}-appIcon").as_bytes()
                    && name != format!("{key}-image").as_bytes())
            {
                return Err(Error::Value("Invalid image destination".into()));
            }
            let from = get(pair, "from")?.clone();
            if let Some(text) = destination.text() {
                let uri = format!("file://{text}");
                sources.retain(|(existing, _)| *existing != uri);
                sources.push((uri, from));
            }
        }
    }
    for role in ["appIcon", "image"] {
        let value = entry.get(role).cloned().unwrap_or(json!(""));
        let Value::String(value) = value else {
            entry.insert(role.into(), json!(""));
            continue;
        };
        if role == "appIcon" && theme_icon(&value) {
            continue;
        }
        let lookup = sources
            .iter()
            .find(|(uri, _)| *uri == value)
            .map_or(Value::String(value), |(_, from)| from.clone());
        let source = lookup.as_str().and_then(local_image);
        entry.insert(role.into(), json!(""));
        let Some(source) = source else { continue };
        match normalize_image(&source, &key, role).and_then(|path| path.as_uri()) {
            Ok(uri) => {
                entry.insert(role.into(), Value::String(uri));
            }
            // Failed optional images retain text and actions, never the unsafe original.
            Err(error) => eprintln!("foamy.notifications: {role} rejected: {}", error.kind()),
        }
    }
    entry.insert("imageFormat".into(), json!(1));
    Ok(())
}

fn remove_images(key: &str) -> Result<(), Error> {
    let prefix = format!("{key}-");
    let (icon, image) = (format!("{key}-appIcon"), format!("{key}-image"));
    for (name, path) in list_dir(&images()?) {
        if name.starts_with(prefix.as_bytes())
            && (name == icon.as_bytes() || name == image.as_bytes() || image_name(&name).is_some())
        {
            remove_missing_ok(&path)?;
        }
    }
    Ok(())
}

fn drop_keys(keys: &[String]) -> Result<(), Error> {
    for key in keys {
        for folder in [popups()?, history()?] {
            remove_missing_ok(&folder.join(&format!("{key}.json")))?;
        }
        remove_images(key)?;
    }
    Ok(())
}

fn trim_history() -> Result<(), Error> {
    let mut files = notification_files(&history()?);
    newest_first(&mut files);
    for (key, path) in files.iter().skip(HISTORY_LIMIT) {
        remove_missing_ok(path)?;
        remove_images(key)?;
    }
    Ok(())
}

fn valid_keys(value: &Value, limit: usize, message: &str) -> Result<Vec<String>, Error> {
    let keys = value
        .as_array()
        .filter(|keys| keys.len() <= limit)
        .and_then(|keys| {
            keys.iter()
                .map(|key| key.as_str().filter(|key| is_key(key)).map(str::to_string))
                .collect::<Option<Vec<_>>>()
        });
    keys.ok_or_else(|| Error::Value(message.into()))
}

/// `keys` (at most `limit`) or the single `key`.
fn keys_or_key(data: &Map<String, Value>, limit: usize) -> Result<Vec<String>, Error> {
    match data.get("keys") {
        Some(keys) => valid_keys(keys, limit, "Invalid notification keys"),
        None => valid_keys(
            &json!([data.get("key").cloned().unwrap_or(Value::Null)]),
            100,
            "Invalid notification keys",
        ),
    }
}

pub fn write(data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let entry = get(data, "entry")?;
    let mut entry = entry
        .as_object()
        .cloned()
        .ok_or_else(|| Error::Type("cannot convert the entry to a dict".into()))?;
    // Native action callbacks belong to the live sender and cannot survive a restart.
    entry.shift_remove("actionsJson");
    let key = key_of(&entry)?;
    if !is_key(&key) {
        return Err(Error::Value("Invalid notification identity".into()));
    }
    let copies = data.get("copies").cloned().unwrap_or(json!([]));
    normalize_entry(&mut entry, Some(&copies))?;
    let to_history = data.get("history").is_some_and(truthy);
    let target = if to_history { history()? } else { popups()? };
    let entry = Value::Object(entry);
    atomic(&target.join(&format!("{key}.json")), &line(&entry))?;
    if to_history {
        // A history write means the popup is gone; an earlier (or in-flight) popup
        // write for the same key must not survive to be restored after a restart.
        // Images stay: the history entry references them.
        remove_missing_ok(&popups()?.join(&format!("{key}.json")))?;
        trim_history()?;
    }
    if data.contains_key("sources") {
        let empty = json!("");
        let reply = json!({
            "key": key,
            "version": data.get("imageVersion").cloned().unwrap_or(json!(0)),
            "sources": data["sources"],
            "appIcon": entry.get("appIcon").unwrap_or(&empty),
            "image": entry.get("image").unwrap_or(&empty),
        });
        out.extend_from_slice(&line(&reply));
    }
    Ok(())
}

pub fn read(data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    let folder = if data.get("history").is_some_and(truthy) {
        history()?
    } else {
        popups()?
    };
    let mut files = notification_files(&folder);
    newest_first(&mut files);
    let mut count = 0;
    for (key, path) in files {
        if count >= READ_LIMIT {
            break;
        }
        if is_symlink(&path) {
            continue;
        }
        let Ok(raw) = read_prefix(&path, LIMIT + 1) else {
            continue;
        };
        if raw.len() > LIMIT {
            continue;
        }
        let Ok(Value::Object(mut row)) = pyjson::loads(&raw) else {
            continue;
        };
        if !row.get("summary").is_some_and(Value::is_string)
            || !row.get("timestamp").is_some_and(truthy)
        {
            continue;
        }
        let empty = json!("");
        if format!(
            "{}-{}",
            py_str(&row["timestamp"]),
            py_str(row.get("originalId").unwrap_or(&empty))
        ) != key
        {
            continue;
        }
        let original = Value::Object(row.clone());
        if normalize_entry(&mut row, None).is_err() {
            continue;
        }
        let row = Value::Object(row);
        if !py_eq(&row, &original) && atomic(&path, &line(&row)).is_err() {
            continue;
        }
        out.extend_from_slice(&line(&row));
        count += 1;
    }
    Ok(())
}

/// Python's `str.split()` separators in ASCII text.
fn ascii_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c | 0x1c..=0x1f)
}

pub fn forget(data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    let keys = valid_keys(get(data, "keys")?, 100, "Invalid handled notification keys")?;
    // The handled journal is durable before an action that might restart the shell
    // runs; the center reads it (path from `storeInfo`) instead of being called here.
    let journal = paths::base()?.join("handled");
    let previous = File::open(journal.path())
        .and_then(|file| {
            let mut raw = Vec::new();
            file.take(64 * 1024).read_to_end(&mut raw)?;
            Ok(raw)
        })
        .unwrap_or_default();
    let ascii: Vec<u8> = previous.into_iter().filter(u8::is_ascii).collect();
    let mut lines: Vec<String> = ascii
        .split(|&b| ascii_space(b))
        .filter(|token| !token.is_empty())
        .filter_map(|token| std::str::from_utf8(token).ok())
        .filter(|token| is_key(token) && !keys.iter().any(|key| key == token))
        .map(str::to_string)
        .collect();
    lines.extend(keys.iter().cloned());
    let kept = &lines[lines.len().saturating_sub(HANDLED_LIMIT)..];
    atomic(&journal, format!("{}\n", kept.join("\n")).as_bytes())?;
    drop_keys(&keys)
}

pub fn archive(data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    // A popup leaving the screen moves into history; images stay with it.
    for key in keys_or_key(data, 2048)? {
        let source = popups()?.join(&format!("{key}.json"));
        if is_file(&source) && !is_symlink(&source) {
            fs::rename(
                source.path(),
                history()?.join(&format!("{key}.json")).path(),
            )?;
        }
    }
    trim_history()
}

pub fn delete(data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    for key in keys_or_key(data, 2048)? {
        remove_missing_ok(&popups()?.join(&format!("{key}.json")))?;
        let prefix = format!("{key}-");
        for (name, path) in list_dir(&images()?) {
            if name.starts_with(prefix.as_bytes()) {
                remove_missing_ok(&path)?;
            }
        }
    }
    Ok(())
}

pub fn clear(_data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    for (key, path) in notification_files(&history()?) {
        remove_missing_ok(&path)?;
        remove_images(&key)?;
    }
    Ok(())
}

pub fn drop(data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    // The center removed these durably: drop Foamy's own copies too.
    if let Some(before) = data.get("before") {
        let Some(before) = before.as_u64() else {
            return Err(Error::Value("Invalid cutoff".into()));
        };
        let limit = before.to_string();
        let mut keys = Vec::new();
        for folder in [popups()?, history()?] {
            for (key, _) in notification_files(&folder) {
                let digits = stamp(&key);
                if digits.len() < limit.len()
                    || (digits.len() == limit.len() && digits <= limit.as_str())
                {
                    keys.push(key);
                }
            }
        }
        drop_keys(&keys)
    } else {
        let keys = data.get("keys").cloned().unwrap_or(Value::Null);
        drop_keys(&valid_keys(&keys, 100, "Invalid notification keys")?)
    }
}

pub fn sweep(_data: &Map<String, Value>, _out: &mut Vec<u8>) -> Result<(), Error> {
    // A restart can kill a write between its image copy and its JSON.
    let images = images()?;
    if !is_dir(&images) {
        return Ok(());
    }
    for entry in fs::read_dir(images.path())? {
        let entry = entry?;
        let name = std::os::unix::ffi::OsStrExt::as_bytes(entry.file_name().as_os_str()).to_vec();
        let path = images.join_bytes(&name);
        if name.ends_with(b".tmp") || name.starts_with(b".foamy-") {
            if is_dir(&path) {
                // Like shutil.rmtree(ignore_errors=True): a symlink is left alone.
                if !is_symlink(&path) {
                    let _ = fs::remove_dir_all(path.path());
                }
            } else {
                remove_missing_ok(&path)?;
            }
            continue;
        }
        if let Some(key) = sweepable(&name)
            && !exists(&popups()?.join(&format!("{key}.json")))
            && !exists(&history()?.join(&format!("{key}.json")))
        {
            remove_missing_ok(&path)?;
        }
    }
    Ok(())
}

/// Moves a `file://` image reference from the old images directory to the new one.
fn rewrite(
    row: &mut Map<String, Value>,
    old_images: &PyPath,
    new_images: &PyPath,
) -> Result<(), Error> {
    for role in ["appIcon", "image"] {
        let Some(Value::String(value)) = row.get(role) else {
            continue;
        };
        if !value.starts_with("file://") {
            continue;
        }
        if let Some(path) = local_image(value)
            && path.parent() == *old_images
        {
            let uri = new_images.join_bytes(path.name()).as_uri()?;
            row.insert(role.into(), Value::String(uri));
        }
    }
    Ok(())
}

fn copy_private(from: &PyPath, to: &PyPath) -> Result<(), Error> {
    let mut source = File::open(from.path())?;
    let mut target = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o666)
        .open(to.path())?;
    io::copy(&mut source, &mut target)?;
    fs::set_permissions(to.path(), fs::Permissions::from_mode(0o600))?;
    Ok(())
}

/// One file of the 4.0 layout: copied (rewritten) unless the target exists,
/// then removed. `Ok(true)` when it was copied.
fn move_row(
    path: &PyPath,
    target: &PyPath,
    old_images: &PyPath,
    new_images: &PyPath,
) -> Result<bool, Error> {
    let mut moved = false;
    if !exists(target) {
        let raw = read_prefix(path, LIMIT + 1)?;
        if raw.len() <= LIMIT
            && let Value::Object(mut row) = pyjson::loads(&raw).map_err(Error::Value)?
        {
            rewrite(&mut row, old_images, new_images)?;
            atomic(target, &line(&Value::Object(row)))?;
            moved = true;
        }
    }
    remove_missing_ok(path)?;
    Ok(moved)
}

/// One-time move of the 4.0 layout Foamy used under omarchy/notifications.
/// Only Foamy's per-file names move; stock files stay.
pub fn migrate(_data: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), Error> {
    private_dirs()?;
    let base = paths::base()?;
    if exists(&base.join(MARKER)) {
        out.extend_from_slice(&line(&json!({"migrated": false})));
        return Ok(());
    }
    let state_home = paths::state_home()?;
    let old = state_home.join("omarchy").join("notifications");
    let (old_images, new_images) = (old.join("images"), images()?);
    let mut moved = 0;
    if is_dir(&old_images) && !is_symlink(&old_images) {
        for entry in fs::read_dir(old_images.path())? {
            let name =
                std::os::unix::ffi::OsStrExt::as_bytes(entry?.file_name().as_os_str()).to_vec();
            let path = old_images.join_bytes(&name);
            let target = new_images.join_bytes(&name);
            // Hashed PNGs and the older unhashed `<key>-<role>` copies (normalized on first read).
            if (image_name(&name).is_some() || legacy_name(&name).is_some())
                && is_file(&path)
                && !is_symlink(&path)
                && !exists(&target)
            {
                copy_private(&path, &target)?;
                remove_missing_ok(&path)?;
            }
        }
    }
    for (source_dir, target_dir) in [(old.clone(), popups()?), (old.join("history"), history()?)] {
        if !is_dir(&source_dir) || is_symlink(&source_dir) {
            continue;
        }
        for (_, path) in notification_files(&source_dir) {
            if is_symlink(&path) || !is_file(&path) {
                continue;
            }
            let target = target_dir.join_bytes(path.name());
            if let Ok(true) = move_row(&path, &target, &old_images, &new_images) {
                moved += 1;
            }
        }
    }
    trim_history()?;
    let state = base.join("state.json");
    if !exists(&state) {
        let mut carried = json!({"version": 5, "dnd": false, "snoozes": {}});
        let legacy = fs::read(state_home.join("omarchy").join("notifications.json").path())
            .map_err(|e| e.to_string())
            .and_then(|raw| pyjson::loads_text(&raw));
        match legacy {
            Ok(Value::Object(legacy)) => {
                carried["dnd"] = json!(legacy.get("dnd") == Some(&json!(true)));
                if let Some(snoozes) = legacy.get("snoozes").filter(|s| s.is_object()) {
                    carried["snoozes"] = snoozes.clone();
                }
            }
            Ok(_) => {}
            Err(_) => {
                if let Ok(quiet) = fs::read(old.join("quiet.json").path())
                    .map_err(|e| e.to_string())
                    .and_then(|raw| pyjson::loads_text(&raw))
                {
                    carried["dnd"] = json!(quiet.get("dnd") == Some(&json!(true)));
                }
            }
        }
        let mut text = pyjson::dumps_indent(&carried, 2).into_bytes();
        text.push(b'\n');
        atomic(&state, &text)?;
    }
    atomic(&base.join(MARKER), b"1\n")?;
    out.extend_from_slice(&line(&json!({"migrated": true, "moved": moved})));
    let _ = out.flush();
    Ok(())
}
