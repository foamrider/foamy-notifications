//! Paths as Python's `pathlib` spelled them (`str(Path(x))`, `.parent`,
//! `.name`, `.as_uri()`), the helper's state directories, and the
//! `file://` parsing of `local_image`.

use std::ffi::{OsStr, OsString};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};

/// A path normalized like `pathlib.PurePosixPath`: repeated slashes and `.`
/// components dropped, `..` kept, exactly two leading slashes preserved.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PyPath(Vec<u8>);

impl PyPath {
    pub fn new(raw: &[u8]) -> PyPath {
        let root: &[u8] = if raw.starts_with(b"//") && !raw.starts_with(b"///") {
            b"//"
        } else if raw.starts_with(b"/") {
            b"/"
        } else {
            b""
        };
        let parts: Vec<&[u8]> = raw
            .split(|&b| b == b'/')
            .filter(|part| !part.is_empty() && *part != b".")
            .collect();
        let mut out = root.to_vec();
        out.extend_from_slice(&parts.join(&b'/'));
        if out.is_empty() {
            out.push(b'.');
        }
        PyPath(out)
    }

    pub fn from_os(raw: &OsStr) -> PyPath {
        PyPath::new(raw.as_bytes())
    }

    pub fn bytes(&self) -> &[u8] {
        &self.0
    }

    pub fn path(&self) -> &Path {
        Path::new(OsStr::from_bytes(&self.0))
    }

    pub fn to_path_buf(&self) -> PathBuf {
        PathBuf::from(OsString::from_vec(self.0.clone()))
    }

    /// `str(path)` where it is valid UTF-8.
    pub fn text(&self) -> Option<&str> {
        std::str::from_utf8(&self.0).ok()
    }

    fn root_len(&self) -> usize {
        self.0.iter().take_while(|&&b| b == b'/').count()
    }

    pub fn is_absolute(&self) -> bool {
        self.root_len() > 0
    }

    /// `path / name` (`name` has no slash).
    pub fn join(&self, name: &str) -> PyPath {
        if self.0 == b"." {
            return PyPath::new(name.as_bytes());
        }
        let mut raw = self.0.clone();
        raw.push(b'/');
        raw.extend_from_slice(name.as_bytes());
        PyPath::new(&raw)
    }

    pub fn join_bytes(&self, name: &[u8]) -> PyPath {
        if self.0 == b"." {
            return PyPath::new(name);
        }
        let mut raw = self.0.clone();
        raw.push(b'/');
        raw.extend_from_slice(name);
        PyPath::new(&raw)
    }

    /// `path.name` (empty for a root or `.`).
    pub fn name(&self) -> &[u8] {
        let root = self.root_len();
        let tail = &self.0[root..];
        if tail == b"." {
            return b"";
        }
        tail.rsplit(|&b| b == b'/').next().unwrap_or(b"")
    }

    /// `path.parent`.
    pub fn parent(&self) -> PyPath {
        let root = self.root_len();
        let tail = &self.0[root..];
        if tail.is_empty() || tail == b"." {
            return self.clone();
        }
        match tail.iter().rposition(|&b| b == b'/') {
            Some(index) => PyPath(self.0[..root + index].to_vec()),
            None if root > 0 => PyPath(self.0[..root].to_vec()),
            None => PyPath(b".".to_vec()),
        }
    }

    /// `path.as_uri()`: `file://` and the path percent-encoded except
    /// `A-Za-z0-9_.-~/`; a relative path has none.
    pub fn as_uri(&self) -> Result<String, crate::Error> {
        if !self.is_absolute() {
            return Err(crate::Error::Value(
                "relative paths can't be expressed as file URIs".into(),
            ));
        }
        let mut uri = String::from("file://");
        for &byte in &self.0 {
            if byte.is_ascii_alphanumeric() || b"_.-~/".contains(&byte) {
                uri.push(byte as char);
            } else {
                uri.push_str(&format!("%{byte:02X}"));
            }
        }
        Ok(uri)
    }
}

/// `os.path.expanduser('~')`: `$HOME` (even empty), else the passwd entry,
/// trailing slashes removed, `/` when nothing is left.
pub fn home() -> Result<PyPath, crate::Error> {
    let raw = match std::env::var_os("HOME") {
        Some(home) => home.into_vec(),
        None => passwd_home()
            .ok_or_else(|| crate::Error::Value("Could not determine home directory.".into()))?,
    };
    let end = raw.iter().rposition(|&b| b != b'/').map_or(0, |i| i + 1);
    Ok(PyPath::new(if end == 0 { b"/" } else { &raw[..end] }))
}

fn passwd_home() -> Option<Vec<u8>> {
    // SAFETY: zeroed `passwd` is a valid value of a plain C struct.
    let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
    let mut buffer = vec![0 as libc::c_char; 16384];
    let mut result: *mut libc::passwd = std::ptr::null_mut();
    // SAFETY: all pointers are valid for the call and `buffer` outlives it.
    let rc = unsafe {
        libc::getpwuid_r(
            libc::getuid(),
            &mut entry,
            buffer.as_mut_ptr(),
            buffer.len(),
            &mut result,
        )
    };
    if rc != 0 || result.is_null() || entry.pw_dir.is_null() {
        return None;
    }
    // SAFETY: on success `pw_dir` is a NUL-terminated string in `buffer`.
    Some(
        unsafe { std::ffi::CStr::from_ptr(entry.pw_dir) }
            .to_bytes()
            .to_vec(),
    )
}

/// `$XDG_STATE_HOME` (used as is when set) or `~/.local/state`.
pub fn state_home() -> Result<PyPath, crate::Error> {
    match std::env::var_os("XDG_STATE_HOME") {
        Some(dir) => Ok(PyPath::from_os(&dir)),
        None => Ok(home()?.join(".local").join("state")),
    }
}

/// Foamy's own state directory.
pub fn base() -> Result<PyPath, crate::Error> {
    Ok(state_home()?.join("foamy-notifications"))
}

/// Percent-decoding like `urllib.parse.unquote` (UTF-8, invalid sequences
/// replaced; a `%` without two hex digits stays).
fn unquote(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = |b: u8| (b as char).to_digit(16);
            if let (Some(Some(high)), Some(Some(low))) = (
                bytes.get(i + 1).map(|&b| hex(b)),
                bytes.get(i + 2).map(|&b| hex(b)),
            ) {
                out.push((high * 16 + low) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `local_image`: an absolute path, or a `file://` URI without host, query
/// or fragment (percent-decoded); anything else is no local image.
pub fn local_image(value: &str) -> Option<PyPath> {
    let decoded;
    let path = if value.starts_with("file://") {
        // urlsplit drops tabs and newlines anywhere in the URL.
        let url: String = value
            .chars()
            .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
            .collect();
        let rest = url.strip_prefix("file:").unwrap_or(&url);
        let rest = rest.strip_prefix("//")?;
        let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        if end > 0 {
            return None;
        }
        let mut rest = &rest[end..];
        if let Some((before, fragment)) = rest.split_once('#') {
            if !fragment.is_empty() {
                return None;
            }
            rest = before;
        }
        if let Some((before, query)) = rest.split_once('?') {
            if !query.is_empty() {
                return None;
            }
            rest = before;
        }
        decoded = unquote(rest);
        decoded.as_str()
    } else {
        value
    };
    path.starts_with('/').then(|| PyPath::new(path.as_bytes()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_normalize_like_pathlib() {
        assert_eq!(PyPath::new(b"//a//./b/../c/").bytes(), b"//a/b/../c");
        assert_eq!(PyPath::new(b"///a").bytes(), b"/a");
        assert_eq!(PyPath::new(b"").bytes(), b".");
        assert_eq!(PyPath::new(b"/a/b").parent().bytes(), b"/a");
        assert_eq!(PyPath::new(b"/a").parent().bytes(), b"/");
        assert_eq!(PyPath::new(b"//a").parent().bytes(), b"//");
        assert_eq!(PyPath::new(b"a").parent().bytes(), b".");
        assert_eq!(PyPath::new(b"/a/b.png").name(), b"b.png");
        assert_eq!(PyPath::new(b".").join("x").bytes(), b"x");
        assert_eq!(
            PyPath::new("/a b/ü~%:".as_bytes()).as_uri().unwrap(),
            "file:///a%20b/%C3%BC~%25%3A"
        );
        assert_eq!(PyPath::new(b"//a/b").as_uri().unwrap(), "file:////a/b");
        assert!(PyPath::new(b"a").as_uri().is_err());
    }

    #[test]
    fn local_images() {
        let path = |v: &str| local_image(v).map(|p| String::from_utf8(p.bytes().to_vec()).unwrap());
        assert_eq!(
            path("file:///a%20b/c%zz%C3"),
            Some("/a b/c%zz\u{fffd}".into())
        );
        assert_eq!(path("file:///a\tb?"), Some("/ab".into()));
        assert_eq!(path("file:///a#"), Some("/a".into()));
        assert_eq!(path("file://host/a"), None);
        assert_eq!(path("file:///a?x"), None);
        assert_eq!(path("file:///a#x"), None);
        assert_eq!(path("/x/./y"), Some("/x/y".into()));
        assert_eq!(path("x/y"), None);
        assert_eq!(path("image://x"), None);
    }
}
