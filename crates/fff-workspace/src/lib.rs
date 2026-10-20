use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

#[derive(Debug)]
pub enum WorkspaceError {
    Io(io::Error),
    InvalidPath(String),
    OutsideWorkspace(PathBuf),
    NotUtf8(PathBuf),
    NotFound(PathBuf),
    ExpectedOccurrenceCount { expected: usize, actual: usize },
}

impl std::fmt::Display for WorkspaceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "{e}"),
            Self::InvalidPath(p) => write!(f, "invalid workspace-relative path: {p}"),
            Self::OutsideWorkspace(p) => write!(f, "path escapes workspace: {}", p.display()),
            Self::NotUtf8(p) => write!(f, "file is not valid UTF-8: {}", p.display()),
            Self::NotFound(p) => write!(f, "path not found: {}", p.display()),
            Self::ExpectedOccurrenceCount { expected, actual } => {
                write!(f, "expected {expected} matching occurrence(s), found {actual}")
            }
        }
    }
}

impl std::error::Error for WorkspaceError {}

impl From<io::Error> for WorkspaceError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

pub type Result<T> = std::result::Result<T, WorkspaceError>;

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DirEntry {
    pub path: String,
    pub kind: EntryKind,
    pub size: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryKind {
    File,
    Directory,
    Symlink,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadResult {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub total_lines: usize,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplaceResult {
    pub path: String,
    pub replacements: usize,
    pub bytes_before: usize,
    pub bytes_after: usize,
}

impl Workspace {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = fs::canonicalize(root.as_ref())?;
        if !root.is_dir() {
            return Err(WorkspaceError::InvalidPath(root.display().to_string()));
        }
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn read_text(
        &self,
        relative: impl AsRef<Path>,
        start_line: Option<usize>,
        end_line: Option<usize>,
    ) -> Result<ReadResult> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }

        let bytes = fs::read(&path)?;
        let text = String::from_utf8(bytes).map_err(|_| WorkspaceError::NotUtf8(path.clone()))?;
        let lines: Vec<&str> = text.lines().collect();
        let total = lines.len();

        let start = start_line.unwrap_or(1).max(1);
        let end = end_line.unwrap_or(total).min(total);
        if total == 0 || start > end || start > total {
            return Ok(ReadResult {
                path: self.relative_display(&path),
                start_line: start,
                end_line: end,
                total_lines: total,
                text: String::new(),
            });
        }

        Ok(ReadResult {
            path: self.relative_display(&path),
            start_line: start,
            end_line: end,
            total_lines: total,
            text: lines[start - 1..end].join("\n"),
        })
    }

    pub fn list_dir(&self, relative: impl AsRef<Path>) -> Result<Vec<DirEntry>> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_dir() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }

        let mut out = Vec::new();
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            let file_type = entry.file_type()?;
            let metadata = fs::symlink_metadata(entry.path())?;
            let kind = if file_type.is_file() {
                EntryKind::File
            } else if file_type.is_dir() {
                EntryKind::Directory
            } else if file_type.is_symlink() {
                EntryKind::Symlink
            } else {
                EntryKind::Other
            };
            out.push(DirEntry {
                path: self.relative_display(&entry.path()),
                kind,
                size: metadata.len(),
            });
        }
        out.sort_by(|a, b| a.path.cmp(&b.path));
        Ok(out)
    }

    pub fn write_text(&self, relative: impl AsRef<Path>, text: &str) -> Result<()> {
        let path = self.resolve_for_write(relative.as_ref())?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        let tmp = temp_sibling(&path);
        fs::write(&tmp, text.as_bytes())?;
        fs::rename(&tmp, &path)?;
        Ok(())
    }

    pub fn replace_exact(
        &self,
        relative: impl AsRef<Path>,
        old: &str,
        new: &str,
        expected_occurrences: usize,
    ) -> Result<ReplaceResult> {
        if old.is_empty() {
            return Err(WorkspaceError::InvalidPath(
                "old text must not be empty".to_string(),
            ));
        }

        let path = self.resolve_existing(relative.as_ref())?;
        let bytes = fs::read(&path)?;
        let text = String::from_utf8(bytes).map_err(|_| WorkspaceError::NotUtf8(path.clone()))?;
        let actual = text.match_indices(old).count();

        if actual != expected_occurrences {
            return Err(WorkspaceError::ExpectedOccurrenceCount {
                expected: expected_occurrences,
                actual,
            });
        }

        let updated = text.replace(old, new);
        let before = text.len();
        let after = updated.len();
        self.write_text(self.relative_path(&path)?, &updated)?;

        Ok(ReplaceResult {
            path: self.relative_display(&path),
            replacements: actual,
            bytes_before: before,
            bytes_after: after,
        })
    }

    pub fn move_path(&self, from: impl AsRef<Path>, to: impl AsRef<Path>) -> Result<()> {
        let from = self.resolve_entry(from.as_ref())?;
        let to = self.resolve_for_write(to.as_ref())?;
        if let Some(parent) = to.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::rename(from, to)?;
        Ok(())
    }

    pub fn delete_path(&self, relative: impl AsRef<Path>) -> Result<()> {
        let path = self.resolve_entry(relative.as_ref())?;
        let metadata = fs::symlink_metadata(&path)?;

        if metadata.file_type().is_symlink() || metadata.is_file() {
            fs::remove_file(path)?;
        } else if metadata.is_dir() {
            fs::remove_dir_all(path)?;
        } else {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }
        Ok(())
    }

    fn validate_relative(&self, relative: &Path) -> Result<PathBuf> {
        if relative.as_os_str().is_empty() {
            return Ok(PathBuf::new());
        }
        if relative.is_absolute() {
            return Err(WorkspaceError::InvalidPath(relative.display().to_string()));
        }

        let mut clean = PathBuf::new();
        for component in relative.components() {
            match component {
                Component::Normal(part) => clean.push(part),
                Component::CurDir => {}
                Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                    return Err(WorkspaceError::InvalidPath(relative.display().to_string()));
                }
            }
        }
        Ok(clean)
    }

    fn resolve_existing(&self, relative: &Path) -> Result<PathBuf> {
        let clean = self.validate_relative(relative)?;
        let candidate = self.root.join(clean);
        let canonical = fs::canonicalize(&candidate).map_err(|e| {
            if e.kind() == io::ErrorKind::NotFound {
                WorkspaceError::NotFound(candidate.clone())
            } else {
                WorkspaceError::Io(e)
            }
        })?;
        self.ensure_inside(canonical)
    }

    fn resolve_entry(&self, relative: &Path) -> Result<PathBuf> {
        let clean = self.validate_relative(relative)?;
        let candidate = self.root.join(clean);
        let metadata = fs::symlink_metadata(&candidate).map_err(|e| {
            if e.kind() == io::ErrorKind::NotFound {
                WorkspaceError::NotFound(candidate.clone())
            } else {
                WorkspaceError::Io(e)
            }
        })?;

        let parent = candidate.parent().unwrap_or(&self.root);
        let canonical_parent = fs::canonicalize(parent)?;
        self.ensure_inside(canonical_parent)?;

        if !metadata.file_type().is_symlink() {
            self.ensure_inside(fs::canonicalize(&candidate)?)?;
        }
        Ok(candidate)
    }

    fn resolve_for_write(&self, relative: &Path) -> Result<PathBuf> {
        let clean = self.validate_relative(relative)?;
        let candidate = self.root.join(clean);

        if candidate.exists() {
            let metadata = fs::symlink_metadata(&candidate)?;
            if metadata.file_type().is_symlink() {
                return Err(WorkspaceError::InvalidPath(format!(
                    "refusing to write through symlink: {}",
                    relative.display()
                )));
            }
            return self.resolve_existing(relative);
        }

        let mut ancestor = candidate.as_path();
        while !ancestor.exists() {
            ancestor = ancestor
                .parent()
                .ok_or_else(|| WorkspaceError::InvalidPath(relative.display().to_string()))?;
        }
        self.ensure_inside(fs::canonicalize(ancestor)?)?;
        Ok(candidate)
    }

    fn ensure_inside(&self, path: PathBuf) -> Result<PathBuf> {
        if path == self.root || path.starts_with(&self.root) {
            Ok(path)
        } else {
            Err(WorkspaceError::OutsideWorkspace(path))
        }
    }

    fn relative_path(&self, path: &Path) -> Result<PathBuf> {
        path.strip_prefix(&self.root)
            .map(Path::to_path_buf)
            .map_err(|_| WorkspaceError::OutsideWorkspace(path.to_path_buf()))
    }

    fn relative_display(&self, path: &Path) -> String {
        path.strip_prefix(&self.root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/")
    }
}

fn temp_sibling(path: &Path) -> PathBuf {
    let mut tmp = path.to_path_buf();
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("fff-edit");
    tmp.set_file_name(format!(".{name}.fff-tmp-{}", std::process::id()));
    tmp
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture() -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("fff-workspace-{id}"));
        fs::create_dir_all(root.join("src")).unwrap();
        fs::write(root.join("src/lib.rs"), "one\ntwo\nthree\n").unwrap();
        root
    }

    #[test]
    fn reads_ranges_and_lists() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();

        let read = ws.read_text("src/lib.rs", Some(2), Some(3)).unwrap();
        assert_eq!(read.text, "two\nthree");
        assert_eq!(read.total_lines, 3);

        let entries = ws.list_dir("src").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].path, "src/lib.rs");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn writes_and_replaces_deterministically() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();

        ws.write_text("src/new.rs", "hello\n").unwrap();
        let changed = ws.replace_exact("src/new.rs", "hello", "world", 1).unwrap();

        assert_eq!(changed.replacements, 1);
        assert_eq!(
            fs::read_to_string(root.join("src/new.rs")).unwrap(),
            "world\n"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_parent_traversal() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();

        assert!(matches!(
            ws.read_text("../outside", None, None),
            Err(WorkspaceError::InvalidPath(_))
        ));

        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_escape_for_reads_and_writes() {
        use std::os::unix::fs::symlink;

        let root = fixture();
        let outside = std::env::temp_dir().join(format!("fff-outside-{}", std::process::id()));
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("secret.txt"), "secret").unwrap();
        symlink(&outside, root.join("escape")).unwrap();

        let ws = Workspace::new(&root).unwrap();

        assert!(matches!(
            ws.read_text("escape/secret.txt", None, None),
            Err(WorkspaceError::OutsideWorkspace(_))
        ));
        assert!(matches!(
            ws.write_text("escape/new.txt", "nope"),
            Err(WorkspaceError::OutsideWorkspace(_))
        ));

        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(outside).unwrap();
    }
}
