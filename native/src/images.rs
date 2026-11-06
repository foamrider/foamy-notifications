//! Sender images: bounded reads and conversion by ImageMagick (`file`
//! picks the coder) into content-addressed private PNGs of at most 720 px.

use std::ffi::OsString;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use foamy_helper::child::Bounded;
use foamy_helper::process::Resource;

use crate::focus::checked;
use crate::paths::{self, PyPath};
use crate::{Error, atomic, is_key, list_dir, mkdir_p, remove_missing_ok, sha};

/// Largest image read or written.
pub const IMAGE_LIMIT: u64 = 5 * 1024 * 1024;
/// Largest converted PNG side.
const MAX_SIDE: u32 = 720;

/// `<key>-<appIcon|image>-<sha256>.png` → `(key, role)`.
pub fn image_name(name: &[u8]) -> Option<(&str, &str)> {
    let name = std::str::from_utf8(name).ok()?;
    let stem = name.strip_suffix(".png")?;
    let (rest, digest) = stem.rsplit_once('-')?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    {
        return None;
    }
    legacy_name(rest.as_bytes())
}

/// `<key>-<appIcon|image>` → `(key, role)` (the older unhashed copies).
pub fn legacy_name(name: &[u8]) -> Option<(&str, &str)> {
    let name = std::str::from_utf8(name).ok()?;
    for role in ["appIcon", "image"] {
        if let Some(key) = name.strip_suffix(role).and_then(|k| k.strip_suffix('-'))
            && is_key(key)
        {
            return Some((key, role));
        }
    }
    None
}

/// `([0-9]+-[0-9]+)-(appIcon|image)(-[a-f0-9]+)?(\.png)?` → the key (what
/// `sweep` may remove once its notification is gone).
pub fn sweepable(name: &[u8]) -> Option<&str> {
    let text = std::str::from_utf8(name).ok()?;
    let candidates = [Some(text), text.strip_suffix(".png")];
    for candidate in candidates.into_iter().flatten() {
        if let Some((key, _)) = legacy_name(candidate.as_bytes()) {
            return Some(key);
        }
        if let Some((rest, hex)) = candidate.rsplit_once('-')
            && !hex.is_empty()
            && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
            && let Some((key, _)) = legacy_name(rest.as_bytes())
        {
            return Some(key);
        }
    }
    None
}

/// Reads a regular file of at most [`IMAGE_LIMIT`] bytes, without following
/// a final symlink or blocking on a FIFO.
pub fn image_bytes(path: &Path) -> Result<Vec<u8>, Error> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    if !file.metadata()?.file_type().is_file() {
        return Err(Error::Value("Image is not a regular file".into()));
    }
    let mut data = Vec::new();
    file.take(IMAGE_LIMIT + 1).read_to_end(&mut data)?;
    if data.len() as u64 > IMAGE_LIMIT {
        return Err(Error::Value("Image exceeds the size limit".into()));
    }
    Ok(data)
}

/// The size of a converted PNG, at most 720 × 720.
pub fn png_dimensions(data: &[u8]) -> Result<(u32, u32), Error> {
    if data.len() < 33 || &data[..16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" {
        return Err(Error::Value("Converter did not produce a PNG".into()));
    }
    let width = u32::from_be_bytes([data[16], data[17], data[18], data[19]]);
    let height = u32::from_be_bytes([data[20], data[21], data[22], data[23]]);
    if !(1..=MAX_SIDE).contains(&width) || !(1..=MAX_SIDE).contains(&height) {
        return Err(Error::Value(
            "Converted image dimensions exceed the limit".into(),
        ));
    }
    Ok((width, height))
}

/// `bin/policy.xml` beside the helper's architecture directory.
fn policy() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    Some(exe.parent()?.parent()?.join("policy.xml"))
}

/// `shutil.which(name)`: an executable file on `PATH`.
fn which(name: &str) -> bool {
    let path = std::env::var_os("PATH").unwrap_or_else(|| OsString::from("/bin:/usr/bin"));
    path.as_bytes().split(|&b| b == b':').any(|dir| {
        let dir = if dir.is_empty() {
            Path::new(".")
        } else {
            Path::new(std::ffi::OsStr::from_bytes(dir))
        };
        let candidate = dir.join(name);
        let Ok(c_path) = std::ffi::CString::new(candidate.as_os_str().as_bytes()) else {
            return false;
        };
        // SAFETY: `c_path` is a valid NUL-terminated string for the call.
        let executable = unsafe { libc::access(c_path.as_ptr(), libc::X_OK) } == 0;
        executable && fs::metadata(&candidate).is_ok_and(|meta| !meta.is_dir())
    })
}

/// A private scratch directory (`.foamy-image-…`, 0700), removed on drop.
struct Scratch(PathBuf);

impl Scratch {
    fn new(parent: &Path) -> Result<Scratch, Error> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.subsec_nanos());
        for _ in 0..100 {
            let n = COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = parent.join(format!(".foamy-image-{}{nanos:x}{n:x}", std::process::id()));
            match DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => return Ok(Scratch(path)),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Error::Os(std::io::Error::other(
            "cannot create a scratch directory",
        )))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn coder(mime: &[u8]) -> Option<&'static str> {
    Some(match mime {
        b"image/png" => "PNG",
        b"image/jpeg" => "JPEG",
        b"image/gif" => "GIF",
        b"image/webp" => "WEBP",
        b"image/bmp" | b"image/x-ms-bmp" => "BMP",
        b"image/vnd.microsoft.icon" | b"image/x-icon" => "ICO",
        b"image/svg+xml" => "RSVG",
        _ => return None,
    })
}

/// Converts `source` into `images/<key>-<role>-<sha256>.png` (or reuses it)
/// and returns that path. Earlier conversions for the same key and role go.
pub fn normalize_image(source: &PyPath, key: &str, role: &str) -> Result<PyPath, Error> {
    let data = image_bytes(source.path())?;
    let images = paths::base()?.join("images");
    mkdir_p(&images)?;
    let digest = sha::hex_digest(&data);
    // Content-addressed private outputs are reusable on text-only updates and restores.
    if source.parent() == images && image_name(source.name()) == Some((key, role)) {
        png_dimensions(&data)?;
        return Ok(source.clone());
    }
    let destination = images.join(&format!("{key}-{role}-{digest}.png"));
    if let Ok(existing) = image_bytes(destination.path())
        && png_dimensions(&existing).is_ok()
    {
        return Ok(destination);
    }
    let policy = policy().filter(|p| fs::metadata(p).is_ok_and(|m| m.is_file()));
    let (Some(policy), true, true) = (policy, which("magick"), which("file")) else {
        return Err(Error::Value(
            "Image conversion requires file and ImageMagick 7".into(),
        ));
    };
    let scratch = Scratch::new(images.path())?;
    let incoming = scratch.0.join("input");
    File::options()
        .write(true)
        .create_new(true)
        .mode(0o666)
        .open(&incoming)?
        .write_all(&data)?;
    let mime = checked(
        Bounded::new("file")
            .arg("-b")
            .arg("--mime-type")
            .arg(&incoming)
            .timeout(Duration::from_secs(2)),
        "file",
        2,
    )?;
    let Some(coder) = coder(mime.stdout.trim_ascii()) else {
        return Err(Error::Value("Unsupported image format".into()));
    };
    let output = scratch.0.join("output.png");
    let empty = scratch.0.join("empty");
    DirBuilder::new().create(&empty)?;
    // SVG receives stdin in an empty directory, without a base path for linked resources.
    let input = if coder == "RSVG" {
        OsString::from("RSVG:-[0]")
    } else {
        let mut spec = format!("{coder}:").into_bytes();
        spec.extend_from_slice(incoming.as_os_str().as_bytes());
        spec.extend_from_slice(b"[0]");
        OsString::from_vec(spec)
    };
    let mut target = b"PNG:".to_vec();
    target.extend_from_slice(output.as_os_str().as_bytes());
    let mut magick = Bounded::new("magick")
        .args(["-background", "none"])
        .arg(input)
        .args(["-auto-orient", "-resize", "720x720>", "-strip"])
        .arg(OsString::from_vec(target))
        .env(
            "MAGICK_CONFIGURE_PATH",
            policy.parent().unwrap_or(Path::new("/")),
        )
        .current_dir(&empty)
        .timeout(Duration::from_secs(5))
        // The decoder alone gets these limits; Qt never owns its allocations.
        .rlimit(Resource::Core, 0)
        .rlimit(Resource::As, 512 * 1024 * 1024)
        .rlimit(Resource::Fsize, IMAGE_LIMIT)
        .rlimit(Resource::Cpu, 5);
    if coder == "RSVG" {
        magick = magick.stdin(data);
    }
    checked(magick, "magick", 5)?;
    let normalized = image_bytes(&output)?;
    png_dimensions(&normalized)?;
    atomic(&destination, &normalized)?;
    // A new URL keeps Qt from reusing the original or a previous replacement's cached image.
    let prefix = format!("{key}-{role}-");
    for (name, path) in list_dir(&images) {
        if name.starts_with(prefix.as_bytes())
            && name.ends_with(b".png")
            && path != destination
            && image_name(&name).is_some()
        {
            remove_missing_ok(&path)?;
        }
    }
    Ok(destination)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        let hash = "a".repeat(64);
        assert_eq!(
            image_name(format!("1-2-image-{hash}.png").as_bytes()),
            Some(("1-2", "image"))
        );
        assert_eq!(
            image_name(format!("1-2-appIcon-{hash}.png").as_bytes()),
            Some(("1-2", "appIcon"))
        );
        assert_eq!(image_name(format!("1-2-icon-{hash}.png").as_bytes()), None);
        assert_eq!(
            image_name(format!("1-2-image-{}.png", "A".repeat(64)).as_bytes()),
            None
        );
        assert_eq!(legacy_name(b"9-9-image"), Some(("9-9", "image")));
        assert_eq!(legacy_name(b"9-9-x-image"), None);
        assert_eq!(sweepable(b"5-5-image-ab.png"), Some("5-5"));
        assert_eq!(sweepable(b"5-5-appIcon.png"), Some("5-5"));
        assert_eq!(sweepable(b"5-5-appIcon-"), None);
        assert_eq!(sweepable(b"5-5-extra"), None);
    }

    #[test]
    fn png_limits() {
        let png = |w: u32, h: u32| {
            let mut data = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR".to_vec();
            data.extend_from_slice(&w.to_be_bytes());
            data.extend_from_slice(&h.to_be_bytes());
            data.resize(33, 0);
            data
        };
        assert_eq!(png_dimensions(&png(720, 1)).unwrap(), (720, 1));
        assert!(png_dimensions(&png(721, 1)).is_err());
        assert!(png_dimensions(&png(0, 1)).is_err());
        assert!(png_dimensions(&png(1, 1)[..32]).is_err());
    }
}
