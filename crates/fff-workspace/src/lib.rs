use globset::Glob;
use regex::Regex;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

mod archive;
mod atomic;
pub mod coordinates;
pub mod edits;
mod file_ops;
pub use file_ops::FileOperation;
pub mod journal;
mod journal_parents;
pub mod lsp;
mod lsp_actions;
mod lsp_cpp;
mod lsp_diagnostics;
mod lsp_hover;
mod lsp_project;
mod lsp_rename;
mod lsp_symbols;
mod lsp_transport;
mod manifest_context;
pub mod preview;
pub mod resident;
mod scoped_search;
pub mod search;
pub mod selection;
pub mod semantic;
pub mod session;
pub mod syntax;
pub mod transform;
mod tree_move;

#[derive(Debug)]
pub enum WorkspaceError {
    Io(io::Error),
    InvalidPath(String),
    OutsideWorkspace(PathBuf),
    NotUtf8(PathBuf),
    NotFound(PathBuf),
    ExpectedOccurrenceCount {
        expected: usize,
        actual: usize,
    },
    Regex(regex::Error),
    InvalidGlob(String),
    ContentChanged {
        expected: String,
        actual: String,
    },
    InvalidEditRange {
        start: usize,
        end: usize,
        len: usize,
    },
    OverlappingEdits,
    InternalPath(String),
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
                write!(
                    f,
                    "expected {expected} matching occurrence(s), found {actual}"
                )
            }
            Self::Regex(e) => write!(f, "invalid regex: {e}"),
            Self::InvalidGlob(g) => write!(f, "invalid glob: {g}"),
            Self::ContentChanged { expected, actual } => {
                write!(f, "content changed: expected {expected}, actual {actual}")
            }
            Self::InvalidEditRange { start, end, len } => {
                write!(f, "invalid edit range {start}..{end} for {len}-byte file")
            }
            Self::OverlappingEdits => write!(f, "text edits overlap"),
            Self::InternalPath(path) => write!(f, "path is reserved for ffsake internals: {path}"),
        }
    }
}

impl std::error::Error for WorkspaceError {}

impl From<io::Error> for WorkspaceError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<regex::Error> for WorkspaceError {
    fn from(value: regex::Error) -> Self {
        Self::Regex(value)
    }
}

pub type Result<T> = std::result::Result<T, WorkspaceError>;

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct DirEntry {
    pub path: String,
    pub kind: EntryKind,
    pub size: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum EntryKind {
    File,
    Directory,
    Symlink,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ReadResult {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub total_lines: usize,
    pub text: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ReplaceMode {
    Literal,
    Regex,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum PathSelector {
    /// Select every file in the workspace. "*" is normalized to this at API boundaries.
    All,
    /// Exact workspace-relative file or directory. Directories select their descendants.
    Exact(String),
    /// Glob over workspace-relative paths.
    Glob(String),
    /// Regular expression over workspace-relative paths.
    Regex(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub struct FileSelector {
    /// Union of selectors. Empty means no files; use PathSelector::All for the whole workspace.
    pub include: Vec<PathSelector>,
    /// Applied after the include union.
    pub exclude: Vec<PathSelector>,
}

impl FileSelector {
    pub fn all() -> Self {
        Self {
            include: vec![PathSelector::All],
            exclude: Vec::new(),
        }
    }

    pub fn exact(path: impl Into<String>) -> Self {
        Self {
            include: vec![PathSelector::Exact(path.into())],
            exclude: Vec::new(),
        }
    }

    pub fn from_patterns(patterns: impl IntoIterator<Item = String>) -> Self {
        let include = patterns
            .into_iter()
            .map(|pattern| {
                if pattern == "*" {
                    PathSelector::All
                } else {
                    PathSelector::Regex(pattern)
                }
            })
            .collect();
        Self {
            include,
            exclude: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplaceRequest {
    pub pattern: String,
    pub replacement: String,
    pub mode: ReplaceMode,
    pub selector: FileSelector,
    /// If set, the operation fails unless the total matches across the selected scope equals this value.
    pub expected_matches: Option<usize>,
    /// If true, return what would change without writing files.
    pub dry_run: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileReplaceResult {
    pub path: String,
    pub replacements: usize,
    pub bytes_before: usize,
    pub bytes_after: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplaceBatchResult {
    pub files_scanned: usize,
    pub files_changed: usize,
    pub replacements: usize,
    pub changes: Vec<FileReplaceResult>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextEdit {
    /// UTF-8 byte offset, inclusive.
    pub start_byte: usize,
    /// UTF-8 byte offset, exclusive.
    pub end_byte: usize,
    pub replacement: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditResult {
    pub path: String,
    pub edits: usize,
    pub bytes_before: usize,
    pub bytes_after: usize,
    pub before_hash: String,
    pub after_hash: String,
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

    pub fn read_all_text(&self, relative: impl AsRef<Path>) -> Result<String> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }

        let bytes = fs::read(&path)?;
        String::from_utf8(bytes).map_err(|_| WorkspaceError::NotUtf8(path))
    }

    /// Bounded explicit-file read. Missing paths are data; symlinks are rejected
    /// before probing descendants, including dangling and internal aliases.
    pub fn read_text_bounded(
        &self,
        relative: &Path,
        start: Option<usize>,
        end: Option<usize>,
        max_bytes: usize,
    ) -> Result<Option<ReadResult>> {
        if self.resolve_explicit_file(relative)?.is_none() {
            return Ok(None);
        }
        self.read_text_impl(relative, start, end, Some(max_bytes))
            .map(Some)
    }

    fn resolve_explicit_file(&self, relative: &Path) -> Result<Option<PathBuf>> {
        let clean = self.validate_relative(relative)?;
        let mut candidate = self.root.clone();
        for component in clean.components() {
            candidate.push(component);
            match fs::symlink_metadata(&candidate) {
                Ok(metadata) if metadata.file_type().is_symlink() => {
                    return Err(WorkspaceError::InvalidPath(
                        "symlinks are not allowed in batch reads".into(),
                    ));
                }
                Ok(_) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(error.into()),
            }
        }
        let path = self.resolve_existing(relative)?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(relative.display().to_string()));
        }
        Ok(Some(path))
    }

    pub(crate) fn read_source_bounded(
        &self,
        relative: &Path,
        max_bytes: usize,
    ) -> Result<(String, Vec<u8>)> {
        let path = self
            .resolve_explicit_file(relative)?
            .ok_or_else(|| WorkspaceError::NotFound(relative.to_path_buf()))?;
        Ok((
            self.relative_display(&path),
            read_bounded_bytes(&path, max_bytes)?,
        ))
    }

    pub fn read_text(
        &self,
        relative: impl AsRef<Path>,
        start_line: Option<usize>,
        end_line: Option<usize>,
    ) -> Result<ReadResult> {
        self.read_text_impl(relative.as_ref(), start_line, end_line, None)
    }

    fn read_text_impl(
        &self,
        relative: &Path,
        start_line: Option<usize>,
        end_line: Option<usize>,
        max_bytes: Option<usize>,
    ) -> Result<ReadResult> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }

        let bytes = if let Some(limit) = max_bytes {
            read_bounded_bytes(&path, limit)?
        } else {
            fs::read(&path)?
        };
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
        atomic::replace_file(&path, &tmp, text.as_bytes(), None)?;
        Ok(())
    }

    pub fn content_hash(&self, relative: impl AsRef<Path>) -> Result<String> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }
        let bytes = fs::read(path)?;
        Ok(blake3::hash(&bytes).to_hex().to_string())
    }

    pub fn apply_text_edits(
        &self,
        relative: impl AsRef<Path>,
        edits: &[TextEdit],
        expected_hash: Option<&str>,
    ) -> Result<EditResult> {
        let path = self.resolve_existing(relative.as_ref())?;
        if !path.is_file() {
            return Err(WorkspaceError::InvalidPath(self.relative_display(&path)));
        }

        let bytes = fs::read(&path)?;
        let text = String::from_utf8(bytes).map_err(|_| WorkspaceError::NotUtf8(path.clone()))?;
        let before_hash = blake3::hash(text.as_bytes()).to_hex().to_string();

        if let Some(expected) = expected_hash
            && expected != before_hash
        {
            return Err(WorkspaceError::ContentChanged {
                expected: expected.to_string(),
                actual: before_hash,
            });
        }

        let mut ordered = edits.to_vec();
        ordered.sort_by_key(|edit| (edit.start_byte, edit.end_byte));

        let len = text.len();
        let mut previous_end = 0usize;
        for edit in &ordered {
            if edit.start_byte > edit.end_byte
                || edit.end_byte > len
                || !text.is_char_boundary(edit.start_byte)
                || !text.is_char_boundary(edit.end_byte)
            {
                return Err(WorkspaceError::InvalidEditRange {
                    start: edit.start_byte,
                    end: edit.end_byte,
                    len,
                });
            }
            if edit.start_byte < previous_end {
                return Err(WorkspaceError::OverlappingEdits);
            }
            previous_end = edit.end_byte;
        }

        let mut updated = text.clone();
        for edit in ordered.iter().rev() {
            updated.replace_range(edit.start_byte..edit.end_byte, &edit.replacement);
        }

        let after_hash = blake3::hash(updated.as_bytes()).to_hex().to_string();
        let rel = self.relative_path(&path)?;
        self.write_text(rel, &updated)?;

        Ok(EditResult {
            path: self.relative_display(&path),
            edits: edits.len(),
            bytes_before: text.len(),
            bytes_after: updated.len(),
            before_hash,
            after_hash,
        })
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

    pub fn replace(&self, request: &ReplaceRequest) -> Result<ReplaceBatchResult> {
        if request.pattern.is_empty() {
            return Err(WorkspaceError::InvalidPath(
                "replacement pattern must not be empty".into(),
            ));
        }

        let regex = match request.mode {
            ReplaceMode::Literal => None,
            ReplaceMode::Regex => Some(Regex::new(&request.pattern)?),
        };
        let files = self.select_files(&request.selector)?;

        let mut staged: Vec<(PathBuf, String, FileReplaceResult)> = Vec::new();
        let mut total_matches = 0usize;
        let mut files_scanned = 0usize;

        for path in files {
            let rel = self.relative_display(&path);
            files_scanned += 1;

            let bytes = fs::read(&path)?;
            let Ok(text) = String::from_utf8(bytes) else {
                continue;
            };

            let matches = match (&regex, request.mode) {
                (Some(re), ReplaceMode::Regex) => re.find_iter(&text).count(),
                (None, ReplaceMode::Literal) => text.match_indices(&request.pattern).count(),
                _ => unreachable!(),
            };
            if matches == 0 {
                continue;
            }

            let updated = match (&regex, request.mode) {
                (Some(re), ReplaceMode::Regex) => re
                    .replace_all(&text, request.replacement.as_str())
                    .into_owned(),
                (None, ReplaceMode::Literal) => {
                    text.replace(&request.pattern, &request.replacement)
                }
                _ => unreachable!(),
            };

            total_matches += matches;
            staged.push((
                path,
                updated.clone(),
                FileReplaceResult {
                    path: rel,
                    replacements: matches,
                    bytes_before: text.len(),
                    bytes_after: updated.len(),
                },
            ));
        }

        if let Some(expected) = request.expected_matches
            && total_matches != expected
        {
            return Err(WorkspaceError::ExpectedOccurrenceCount {
                expected,
                actual: total_matches,
            });
        }

        if !request.dry_run {
            for (path, updated, _) in &staged {
                let rel = self.relative_path(path)?;
                self.write_text(rel, updated)?;
            }
        }

        Ok(ReplaceBatchResult {
            files_scanned,
            files_changed: staged.len(),
            replacements: total_matches,
            changes: staged.into_iter().map(|(_, _, change)| change).collect(),
        })
    }

    pub fn replace_literal(
        &self,
        pattern: impl Into<String>,
        replacement: impl Into<String>,
        selector: FileSelector,
        expected_matches: Option<usize>,
        dry_run: bool,
    ) -> Result<ReplaceBatchResult> {
        self.replace(&ReplaceRequest {
            pattern: pattern.into(),
            replacement: replacement.into(),
            mode: ReplaceMode::Literal,
            selector,
            expected_matches,
            dry_run,
        })
    }

    pub fn replace_regex(
        &self,
        pattern: impl Into<String>,
        replacement: impl Into<String>,
        selector: FileSelector,
        expected_matches: Option<usize>,
        dry_run: bool,
    ) -> Result<ReplaceBatchResult> {
        self.replace(&ReplaceRequest {
            pattern: pattern.into(),
            replacement: replacement.into(),
            mode: ReplaceMode::Regex,
            selector,
            expected_matches,
            dry_run,
        })
    }

    pub fn select_files(&self, selector: &FileSelector) -> Result<Vec<PathBuf>> {
        use std::collections::BTreeSet;

        let mut all_files = Vec::new();
        let needs_scan = selector
            .include
            .iter()
            .chain(selector.exclude.iter())
            .any(|s| !matches!(s, PathSelector::Exact(_)));
        if needs_scan {
            self.collect_files(&self.root, &mut all_files)?;
        }

        let mut selected = BTreeSet::new();

        for rule in &selector.include {
            match rule {
                PathSelector::All => {
                    for path in &all_files {
                        selected.insert(path.clone());
                    }
                }
                PathSelector::Exact(raw) => {
                    let path = self.resolve_existing(Path::new(raw))?;
                    if path.is_file() {
                        selected.insert(path);
                    } else if path.is_dir() {
                        let mut nested = Vec::new();
                        self.collect_files(&path, &mut nested)?;
                        selected.extend(nested);
                    } else {
                        return Err(WorkspaceError::InvalidPath(raw.clone()));
                    }
                }
                PathSelector::Glob(pattern) => {
                    let glob = Glob::new(pattern)
                        .map_err(|_| WorkspaceError::InvalidGlob(pattern.clone()))?
                        .compile_matcher();
                    for path in &all_files {
                        if glob.is_match(self.relative_display(path)) {
                            selected.insert(path.clone());
                        }
                    }
                }
                PathSelector::Regex(pattern) => {
                    let re = Regex::new(pattern)?;
                    for path in &all_files {
                        if re.is_match(&self.relative_display(path)) {
                            selected.insert(path.clone());
                        }
                    }
                }
            }
        }

        if !selector.exclude.is_empty() && !selected.is_empty() {
            let mut remove = BTreeSet::new();
            for rule in &selector.exclude {
                match rule {
                    PathSelector::All => {
                        remove.extend(selected.iter().cloned());
                    }
                    PathSelector::Exact(raw) => {
                        let path = self.resolve_existing(Path::new(raw))?;
                        if path.is_file() {
                            remove.insert(path);
                        } else if path.is_dir() {
                            let mut nested = Vec::new();
                            self.collect_files(&path, &mut nested)?;
                            remove.extend(nested);
                        }
                    }
                    PathSelector::Glob(pattern) => {
                        let glob = Glob::new(pattern)
                            .map_err(|_| WorkspaceError::InvalidGlob(pattern.clone()))?
                            .compile_matcher();
                        for path in &selected {
                            if glob.is_match(self.relative_display(path)) {
                                remove.insert(path.clone());
                            }
                        }
                    }
                    PathSelector::Regex(pattern) => {
                        let re = Regex::new(pattern)?;
                        for path in &selected {
                            if re.is_match(&self.relative_display(path)) {
                                remove.insert(path.clone());
                            }
                        }
                    }
                }
            }
            for path in remove {
                selected.remove(&path);
            }
        }

        Ok(selected.into_iter().collect())
    }

    pub fn batch<T, F>(&self, selector: &FileSelector, mut operation: F) -> Result<Vec<T>>
    where
        F: FnMut(&Workspace, &Path) -> Result<T>,
    {
        let files = self.select_files(selector)?;
        files
            .iter()
            .map(|path| operation(self, path.as_path()))
            .collect()
    }

    fn collect_files(&self, dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            let ty = entry.file_type()?;
            if entry.file_name() == ".ffsake" || ty.is_symlink() {
                continue;
            }
            if ty.is_dir() {
                self.ensure_inside(fs::canonicalize(&path)?)?;
                self.collect_files(&path, out)?;
            } else if ty.is_file() {
                self.ensure_inside(fs::canonicalize(&path)?)?;
                out.push(path);
            }
        }
        Ok(())
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
            return Err(WorkspaceError::InvalidPath(
                "workspace root is not a valid mutation target".to_string(),
            ));
        }
        if relative.is_absolute() {
            return Err(WorkspaceError::InvalidPath(relative.display().to_string()));
        }

        let mut clean = PathBuf::new();
        for component in relative.components() {
            match component {
                Component::Normal(part) if part == ".ffsake" => {
                    return Err(WorkspaceError::InternalPath(relative.display().to_string()));
                }
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
        let relative = path
            .strip_prefix(&self.root)
            .map_err(|_| WorkspaceError::OutsideWorkspace(path.clone()))?;
        // Validate the resolved path as well as the caller's spelling: an
        // otherwise innocuous symlink name can point into the reserved journal.
        if relative
            .components()
            .any(|component| matches!(component, Component::Normal(part) if part == ".ffsake"))
        {
            return Err(WorkspaceError::InternalPath(relative.display().to_string()));
        }
        Ok(path)
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

fn read_bounded_bytes(path: &Path, limit: usize) -> Result<Vec<u8>> {
    use std::io::Read;
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take((limit as u64).saturating_add(1))
        .read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(WorkspaceError::InvalidPath(
            "source exceeds byte limit".into(),
        ));
    }
    Ok(bytes)
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
        // Clock resolution can yield the same timestamp to parallel tests.
        // Reserve the directory atomically rather than sharing create_dir_all.
        for suffix in 0u64.. {
            let root = std::env::temp_dir().join(format!("fff-workspace-{id}-{suffix}"));
            match fs::create_dir(&root) {
                Ok(()) => {
                    fs::create_dir(root.join("src")).unwrap();
                    fs::write(root.join("src/lib.rs"), "one\ntwo\nthree\n").unwrap();
                    return root;
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("cannot create test fixture: {error}"),
            }
        }
        unreachable!()
    }

    #[test]
    fn bounded_reads_preserve_ranges_and_reject_oversized_sources() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();
        let path = Path::new("src/lib.rs");
        let read = ws
            .read_text_bounded(path, Some(2), Some(3), 14)
            .unwrap()
            .unwrap();
        assert_eq!(read, ws.read_text(path, Some(2), Some(3)).unwrap());
        assert!(ws.read_text_bounded(path, Some(1), Some(1), 13).is_err());
        assert!(
            ws.read_text_bounded(Path::new("missing/child"), None, None, 14)
                .unwrap()
                .is_none()
        );
        assert!(
            ws.read_text_bounded(Path::new(".ffsake/missing"), None, None, 14)
                .is_err()
        );
        fs::write(root.join("empty"), "").unwrap();
        assert_eq!(
            ws.read_text_bounded(Path::new("empty"), None, None, 0)
                .unwrap()
                .unwrap()
                .text,
            ""
        );
        fs::remove_dir_all(root).unwrap();
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
    #[test]
    fn repo_wide_literal_and_regex_replace_are_scoped() {
        let root = fixture();
        fs::create_dir_all(root.join("tests")).unwrap();
        fs::write(root.join("src/a.rs"), "foo 123\nfoo 456\n").unwrap();
        fs::write(root.join("tests/a.rs"), "foo 999\n").unwrap();
        let ws = Workspace::new(&root).unwrap();

        let dry = ws
            .replace_literal(
                "foo",
                "bar",
                FileSelector {
                    include: vec![PathSelector::Glob("src/**".into())],
                    exclude: vec![],
                },
                Some(2),
                true,
            )
            .unwrap();
        assert_eq!(dry.replacements, 2);
        assert_eq!(dry.files_changed, 1);
        assert!(
            fs::read_to_string(root.join("src/a.rs"))
                .unwrap()
                .contains("foo")
        );

        let changed = ws
            .replace_regex(
                r"(foo) (\d+)",
                "$1 number=$2",
                FileSelector {
                    include: vec![
                        PathSelector::Exact("src".into()),
                        PathSelector::Glob("**/*.rs".into()),
                    ],
                    exclude: vec![PathSelector::Regex(r"^tests/".into())],
                },
                Some(2),
                false,
            )
            .unwrap();
        assert_eq!(changed.replacements, 2);
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "foo number=123\nfoo number=456\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("tests/a.rs")).unwrap(),
            "foo 999\n"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn selector_unions_exact_regex_and_all() {
        let root = fixture();
        fs::create_dir_all(root.join("tests")).unwrap();
        fs::write(root.join("src/a.rs"), "a").unwrap();
        fs::write(root.join("src/b.txt"), "b").unwrap();
        fs::write(root.join("tests/c.rs"), "c").unwrap();
        let ws = Workspace::new(&root).unwrap();

        let selected = ws
            .select_files(&FileSelector {
                include: vec![
                    PathSelector::Exact("src/b.txt".into()),
                    PathSelector::Regex(r"\.rs$".into()),
                ],
                exclude: vec![PathSelector::Regex(r"^tests/".into())],
            })
            .unwrap();
        let rel: Vec<_> = selected.iter().map(|p| ws.relative_display(p)).collect();
        assert_eq!(rel, vec!["src/a.rs", "src/b.txt", "src/lib.rs"]);

        let everything = ws.select_files(&FileSelector::all()).unwrap();
        assert_eq!(everything.len(), 4);

        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn applies_multiple_precise_text_edits_with_hash_precondition() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();
        let hash = ws.content_hash("src/lib.rs").unwrap();

        let result = ws
            .apply_text_edits(
                "src/lib.rs",
                &[
                    TextEdit {
                        start_byte: 0,
                        end_byte: 3,
                        replacement: "ONE".into(),
                    },
                    TextEdit {
                        start_byte: 8,
                        end_byte: 13,
                        replacement: "THREE".into(),
                    },
                ],
                Some(&hash),
            )
            .unwrap();

        assert_eq!(result.edits, 2);
        assert_eq!(
            fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "ONE\ntwo\nTHREE\n"
        );

        assert!(matches!(
            ws.apply_text_edits(
                "src/lib.rs",
                &[TextEdit {
                    start_byte: 0,
                    end_byte: 3,
                    replacement: "X".into(),
                }],
                Some(&hash),
            ),
            Err(WorkspaceError::ContentChanged { .. })
        ));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_overlapping_text_edits() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();

        assert!(matches!(
            ws.apply_text_edits(
                "src/lib.rs",
                &[
                    TextEdit {
                        start_byte: 0,
                        end_byte: 5,
                        replacement: "x".into(),
                    },
                    TextEdit {
                        start_byte: 3,
                        end_byte: 7,
                        replacement: "y".into(),
                    },
                ],
                None,
            ),
            Err(WorkspaceError::OverlappingEdits)
        ));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn internal_ffsake_directory_is_not_targetable() {
        let root = fixture();
        fs::create_dir_all(root.join(".ffsake/journal")).unwrap();
        fs::write(root.join(".ffsake/secret"), "internal").unwrap();
        let ws = Workspace::new(&root).unwrap();

        let all = ws.select_files(&FileSelector::all()).unwrap();
        assert!(
            all.iter()
                .all(|path| !path.starts_with(root.join(".ffsake")))
        );
        assert!(matches!(
            ws.read_text(".ffsake/secret", None, None),
            Err(WorkspaceError::InternalPath(_))
        ));

        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn internal_aliases_are_rejected_but_safe_read_aliases_work() {
        use std::os::unix::fs::symlink;
        let root = fixture();
        fs::create_dir_all(root.join(".ffsake")).unwrap();
        fs::write(root.join(".ffsake/secret"), "internal").unwrap();
        symlink(root.join(".ffsake"), root.join("alias")).unwrap();
        symlink(root.join(".ffsake/secret"), root.join("secret-alias")).unwrap();
        symlink(root.join("src"), root.join("safe-alias")).unwrap();
        let ws = Workspace::new(&root).unwrap();
        for path in ["alias/secret", "secret-alias"] {
            assert!(matches!(
                ws.read_text(path, None, None),
                Err(WorkspaceError::InternalPath(_))
            ));
            assert!(ws.content_hash(path).is_err());
            assert!(ws.select_files(&FileSelector::exact(path)).is_err());
        }
        assert!(ws.list_dir("alias").is_err());
        assert!(ws.select_files(&FileSelector::exact("alias")).is_err());
        assert!(ws.write_text("alias/new/child", "bad").is_err());
        assert!(ws.write_text("alias/secret", "bad").is_err());
        assert!(ws.delete_path("alias/secret").is_err());
        assert!(ws.move_path("alias/secret", "leaked").is_err());
        assert_eq!(
            fs::read_to_string(root.join(".ffsake/secret")).unwrap(),
            "internal"
        );
        assert!(!root.join(".ffsake/new").exists());
        assert_eq!(
            ws.read_text("safe-alias/lib.rs", None, None).unwrap(),
            ws.read_text("src/lib.rs", None, None).unwrap()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn recursive_selection_skips_nested_reserved_directories() {
        let root = fixture();
        fs::create_dir_all(root.join("src/.ffsake")).unwrap();
        fs::write(root.join("src/.ffsake/secret"), "internal").unwrap();
        let ws = Workspace::new(&root).unwrap();
        assert_eq!(
            ws.select_files(&FileSelector::all()).unwrap(),
            vec![ws.root().join("src/lib.rs")]
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn workspace_root_cannot_be_mutated_as_empty_path() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();

        assert!(matches!(
            ws.delete_path(""),
            Err(WorkspaceError::InvalidPath(_))
        ));
        assert!(matches!(
            ws.move_path("", "moved"),
            Err(WorkspaceError::InvalidPath(_))
        ));
        assert!(matches!(
            ws.write_text("", "oops"),
            Err(WorkspaceError::InvalidPath(_))
        ));

        assert!(root.exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn direct_writes_reject_temporary_aliases_and_preserve_permissions() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();
        let path = root.join("src/lib.rs");
        let sentinel = root.join("sentinel");
        fs::write(&sentinel, b"untouched").unwrap();
        let temp = temp_sibling(&path);
        symlink(&sentinel, &temp).unwrap();
        assert!(ws.write_text("src/lib.rs", "bad").is_err());
        assert_eq!(fs::read(&sentinel).unwrap(), b"untouched");
        fs::remove_file(&temp).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        ws.write_text("src/lib.rs", "safe").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"safe");
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert!(!temp.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn direct_writes_preserve_temporary_collisions_and_reject_root() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();
        let path = root.join("src/lib.rs");
        let temp = temp_sibling(&path);
        fs::write(&temp, b"unrelated temporary file").unwrap();
        assert!(ws.write_text("src/lib.rs", "bad").is_err());
        assert_eq!(fs::read(&temp).unwrap(), b"unrelated temporary file");
        assert_eq!(fs::read(&path).unwrap(), b"one\ntwo\nthree\n");
        assert!(ws.write_text(".", "bad").is_err());
        assert!(root.is_dir());
        fs::remove_dir_all(root).unwrap();
    }
}
