pub use crate::tree_move::TreeMove as TransactionTreeMove;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const INTERNAL_DIR: &str = ".ffsake";
const JOURNAL_DIR: &str = "journal";

#[derive(Debug)]
pub enum JournalError {
    Io(io::Error),
    Json(serde_json::Error),
    InvalidPath(String),
    MissingTransaction(String),
    RollbackFailed {
        original: String,
        failures: Vec<String>,
    },
    StateConflict {
        path: String,
        expected: String,
        actual: String,
    },
}

impl std::fmt::Display for JournalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(f, "{error}"),
            Self::Json(error) => write!(f, "{error}"),
            Self::InvalidPath(path) => write!(f, "invalid journal path: {path}"),
            Self::MissingTransaction(id) => write!(f, "transaction not found: {id}"),
            Self::RollbackFailed { original, failures } => write!(
                f,
                "{original}; rollback incomplete: {}",
                failures.join("; ")
            ),
            Self::StateConflict {
                path,
                expected,
                actual,
            } => write!(
                f,
                "cannot apply transaction for {path}: expected {expected}, found {actual}"
            ),
        }
    }
}

impl std::error::Error for JournalError {}

impl From<io::Error> for JournalError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<serde_json::Error> for JournalError {
    fn from(value: serde_json::Error) -> Self {
        Self::Json(value)
    }
}

pub type Result<T> = std::result::Result<T, JournalError>;

#[derive(Debug, Clone)]
pub struct Journal {
    workspace_root: PathBuf,
    journal_root: PathBuf,
}

#[derive(Debug, Clone)]
pub struct JournalChange {
    pub path: String,
    pub before: Option<Vec<u8>>,
    pub after: Option<Vec<u8>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum TransactionState {
    Prepared,
    Applied,
    Undone,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TransactionFile {
    pub path: String,
    pub snapshot_key: String,
    pub before_hash: Option<String>,
    /// Ordinary Unix rwx bits for recreating the before-side file. Older
    /// journals and record-after-the-fact callers have no original metadata.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_unix_mode: Option<u32>,
    pub after_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub after_unix_mode: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TransactionDirectory {
    pub path: String,
    pub before_exists: bool,
    pub after_exists: bool,
    pub unix_mode: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TransactionTreeArchive {
    pub path: String,
    #[serde(default)]
    pub restore_when_applied: bool,
    pub entries: BTreeMap<String, crate::tree_move::TreeEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TransactionManifest {
    pub id: String,
    pub state: TransactionState,
    pub label: Option<String>,
    pub files: Vec<TransactionFile>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub directory: Option<TransactionDirectory>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tree_move: Option<TransactionTreeMove>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tree_archive: Option<TransactionTreeArchive>,
}

impl Journal {
    pub fn open(workspace_root: impl AsRef<Path>) -> Result<Self> {
        let workspace_root = fs::canonicalize(workspace_root.as_ref())?;
        let journal_root = workspace_root.join(INTERNAL_DIR).join(JOURNAL_DIR);
        for path in [workspace_root.join(INTERNAL_DIR), journal_root.clone()] {
            if fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
                return Err(JournalError::InvalidPath(path.display().to_string()));
            }
        }
        fs::create_dir_all(&journal_root)?;
        Ok(Self {
            workspace_root,
            journal_root,
        })
    }

    pub fn commit(
        &self,
        label: Option<String>,
        planned: &[(String, Option<Vec<u8>>)],
    ) -> Result<TransactionManifest> {
        let mut changes = Vec::with_capacity(planned.len());

        for (path, after) in planned {
            let resolved = self.resolve(path)?;
            let before = if resolved.exists() {
                Some(fs::read(&resolved)?)
            } else {
                None
            };
            changes.push(JournalChange {
                path: path.clone(),
                before,
                after: after.clone(),
            });
        }

        self.commit_changes(label, &changes)
    }

    pub fn commit_changes(
        &self,
        label: Option<String>,
        changes: &[JournalChange],
    ) -> Result<TransactionManifest> {
        self.commit_changes_with_modes(label, changes, &BTreeMap::new())
    }

    pub(crate) fn commit_changes_with_modes(
        &self,
        label: Option<String>,
        changes: &[JournalChange],
        destination_modes: &BTreeMap<String, u32>,
    ) -> Result<TransactionManifest> {
        let mut manifest = self.prepare(label, changes, destination_modes)?;
        self.verify_side(&manifest, SnapshotSide::Before)?;

        let mut applied = Vec::new();
        for index in 0..manifest.files.len() {
            let file = manifest.files[index].clone();
            match self.apply_one(&manifest, &file, SnapshotSide::After) {
                Ok(mode) => manifest.files[index].after_unix_mode = mode,
                Err(error) => {
                    return Err(self.rollback(&manifest, &applied, SnapshotSide::Before, error));
                }
            }
            applied.push(file);
        }

        manifest.state = TransactionState::Applied;
        if let Err(error) = self.write_manifest(&manifest) {
            return Err(self.rollback(&manifest, &applied, SnapshotSide::Before, error));
        }
        Ok(manifest)
    }

    /// Journal one empty directory. Parents must already exist; no recursive removal.
    pub fn commit_directory(&self, path: &str, create: bool) -> Result<TransactionManifest> {
        self.commit_directory_preview(self.preview_directory(path, create)?)
    }

    pub(crate) fn preview_directory(
        &self,
        path: &str,
        create: bool,
    ) -> Result<TransactionDirectory> {
        let resolved = self.resolve(path)?;
        let directory = TransactionDirectory {
            path: path.into(),
            before_exists: !create,
            after_exists: create,
            unix_mode: if create { None } else { unix_mode(&resolved)? },
        };
        self.verify_directory(&directory, SnapshotSide::Before)?;
        Ok(directory)
    }

    pub(crate) fn commit_directory_preview(
        &self,
        directory: TransactionDirectory,
    ) -> Result<TransactionManifest> {
        self.verify_directory(&directory, SnapshotSide::Before)?;
        let manifest = TransactionManifest {
            id: transaction_id(),
            state: TransactionState::Prepared,
            label: Some("empty directory operation".into()),
            files: vec![],
            directory: Some(directory),
            tree_move: None,
            tree_archive: None,
        };
        self.write_manifest(&manifest)?;
        self.transition_directory(manifest, SnapshotSide::After, TransactionState::Applied)
    }

    fn verify_directory(&self, directory: &TransactionDirectory, side: SnapshotSide) -> Result<()> {
        if directory.before_exists == directory.after_exists
            || directory.unix_mode.is_some_and(|mode| mode > 0o777)
        {
            return Err(JournalError::InvalidPath(
                "invalid directory transaction".into(),
            ));
        }
        let path = self.resolve(&directory.path)?;
        let exists = match side {
            SnapshotSide::Before => directory.before_exists,
            SnapshotSide::After => directory.after_exists,
        };
        match fs::symlink_metadata(&path) {
            Ok(metadata) if exists && metadata.is_dir() => {
                if fs::read_dir(&path)?.next().transpose()?.is_some() {
                    return Err(JournalError::StateConflict {
                        path: directory.path.clone(),
                        expected: "empty directory".into(),
                        actual: "nonempty directory".into(),
                    });
                }
                if directory.unix_mode.is_some() && unix_mode(&path)? != directory.unix_mode {
                    return Err(JournalError::StateConflict {
                        path: directory.path.clone(),
                        expected: "original directory permissions".into(),
                        actual: "changed permissions".into(),
                    });
                }
            }
            Err(error) if !exists && error.kind() == io::ErrorKind::NotFound => {
                if !path.parent().is_some_and(Path::is_dir) {
                    return Err(JournalError::InvalidPath(
                        "directory parent must already exist".into(),
                    ));
                }
            }
            Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error.into()),
            _ => {
                return Err(JournalError::StateConflict {
                    path: directory.path.clone(),
                    expected: if exists {
                        "empty directory"
                    } else {
                        "absent directory"
                    }
                    .into(),
                    actual: "unexpected filesystem state".into(),
                });
            }
        }
        Ok(())
    }

    fn apply_directory(
        &self,
        directory: &TransactionDirectory,
        side: SnapshotSide,
    ) -> Result<Option<u32>> {
        self.verify_directory(directory, side.opposite())?;
        let path = self.resolve(&directory.path)?;
        let exists = match side {
            SnapshotSide::Before => directory.before_exists,
            SnapshotSide::After => directory.after_exists,
        };
        if exists {
            fs::create_dir(&path)?;
            #[cfg(unix)]
            if let Some(mode) = directory.unix_mode {
                use std::os::unix::fs::PermissionsExt;
                if let Err(error) = fs::set_permissions(&path, fs::Permissions::from_mode(mode)) {
                    return match fs::remove_dir(&path) {
                        Ok(()) => Err(error.into()),
                        Err(rollback) => Err(JournalError::RollbackFailed {
                            original: error.to_string(),
                            failures: vec![format!("{}: {rollback}", directory.path)],
                        }),
                    };
                }
            }
            unix_mode(&path)
        } else {
            // remove_dir is deliberately nonrecursive, including during rollback.
            fs::remove_dir(path)?;
            Ok(directory.unix_mode)
        }
    }

    fn transition_directory(
        &self,
        mut manifest: TransactionManifest,
        side: SnapshotSide,
        state: TransactionState,
    ) -> Result<TransactionManifest> {
        if !manifest.files.is_empty() {
            return Err(JournalError::InvalidPath(
                "mixed directory/file transaction".into(),
            ));
        }
        let mut directory = manifest
            .directory
            .clone()
            .ok_or_else(|| JournalError::InvalidPath("missing directory transaction".into()))?;
        directory.unix_mode = self.apply_directory(&directory, side)?;
        manifest.directory = Some(directory.clone());
        manifest.state = state;
        if let Err(error) = self.write_manifest(&manifest) {
            return match self.apply_directory(&directory, side.opposite()) {
                Ok(_) => Err(error),
                Err(rollback) => Err(JournalError::RollbackFailed {
                    original: error.to_string(),
                    failures: vec![format!("{}: {rollback}", directory.path)],
                }),
            };
        }
        Ok(manifest)
    }

    pub(crate) fn commit_tree_move(
        &self,
        movement: TransactionTreeMove,
    ) -> Result<TransactionManifest> {
        movement.verify(self, false)?;
        let manifest = TransactionManifest {
            id: transaction_id(),
            state: TransactionState::Prepared,
            label: Some("directory move".into()),
            files: vec![],
            directory: None,
            tree_move: Some(movement),
            tree_archive: None,
        };
        self.write_manifest(&manifest)?;
        self.transition_tree_move(manifest, SnapshotSide::After, TransactionState::Applied)
    }

    fn transition_tree_move(
        &self,
        mut manifest: TransactionManifest,
        side: SnapshotSide,
        state: TransactionState,
    ) -> Result<TransactionManifest> {
        if !manifest.files.is_empty() || manifest.directory.is_some() {
            return Err(JournalError::InvalidPath("mixed tree transaction".into()));
        }
        let movement = manifest
            .tree_move
            .as_ref()
            .ok_or_else(|| JournalError::InvalidPath("missing tree move".into()))?;
        let after = matches!(side, SnapshotSide::After);
        movement.apply(self, after)?;
        manifest.state = state;
        if let Err(error) = self.write_manifest(&manifest) {
            return match movement.apply(self, !after) {
                Ok(()) => Err(error),
                Err(rollback) => Err(JournalError::RollbackFailed {
                    original: error.to_string(),
                    failures: vec![rollback.to_string()],
                }),
            };
        }
        Ok(manifest)
    }

    pub(crate) fn preview_tree_archive(&self, path: &str) -> Result<TransactionTreeArchive> {
        if path.len() > 4096 {
            return Err(JournalError::InvalidPath(
                "tree path exceeds 4096 bytes".into(),
            ));
        }
        Ok(TransactionTreeArchive {
            path: path.into(),
            restore_when_applied: false,
            entries: crate::tree_move::inventory(self, path)?,
        })
    }

    pub(crate) fn commit_tree_archive(
        &self,
        archive: TransactionTreeArchive,
    ) -> Result<TransactionManifest> {
        let manifest = TransactionManifest {
            id: transaction_id(),
            state: TransactionState::Prepared,
            label: Some("directory archive".into()),
            files: vec![],
            directory: None,
            tree_move: None,
            tree_archive: Some(archive),
        };
        // The transaction parent is created only after the source preview was
        // validated; the protected archive destination is never caller-supplied.
        self.write_manifest(&manifest)?;
        self.transition_tree_archive(manifest, SnapshotSide::After, TransactionState::Applied)
    }

    pub(crate) fn commit_tree_copy(
        &self,
        copy: &crate::tree_move::TreeCopy,
    ) -> Result<TransactionManifest> {
        copy.movement.verify(self, false)?;
        let manifest = TransactionManifest {
            id: transaction_id(),
            state: TransactionState::Prepared,
            label: Some("directory copy".into()),
            files: vec![],
            directory: None,
            tree_move: None,
            tree_archive: Some(TransactionTreeArchive {
                path: copy.movement.to.clone(),
                restore_when_applied: true,
                entries: copy.movement.entries.clone(),
            }),
        };
        self.write_manifest(&manifest)?;
        let (_, stored) = self.tree_archive_paths(&manifest)?;
        // Failures leave only a private prepared transaction; no destination is
        // published until the complete copied inventory and source recheck pass.
        copy.materialize(&stored)?;
        copy.movement.verify(self, false)?;
        self.transition_tree_archive(manifest, SnapshotSide::After, TransactionState::Applied)
    }

    fn tree_archive_paths(&self, manifest: &TransactionManifest) -> Result<(PathBuf, PathBuf)> {
        validate_transaction_id(&manifest.id)?;
        if !manifest.files.is_empty()
            || manifest.directory.is_some()
            || manifest.tree_move.is_some()
        {
            return Err(JournalError::InvalidPath(
                "mixed tree archive transaction".into(),
            ));
        }
        let archive = manifest
            .tree_archive
            .as_ref()
            .ok_or_else(|| JournalError::InvalidPath("missing tree archive".into()))?;
        let source = self.resolve(&archive.path)?;
        let stored = self.journal_root.join(&manifest.id).join("tree-data");
        self.check_internal_path(&stored)?;
        Ok((source, stored))
    }

    fn verify_tree_archive(&self, manifest: &TransactionManifest, after: bool) -> Result<()> {
        let (source, stored) = self.tree_archive_paths(manifest)?;
        let archived = after != manifest.tree_archive.as_ref().unwrap().restore_when_applied;
        let (present, absent) = if archived {
            (stored, source)
        } else {
            (source, stored)
        };
        if absent.try_exists()? || !absent.parent().is_some_and(Path::is_dir) {
            return Err(JournalError::InvalidPath(
                "archive destination must be absent with an existing parent".into(),
            ));
        }
        let archive = manifest.tree_archive.as_ref().unwrap();
        if crate::tree_move::inventory_path(&present)? != archive.entries {
            return Err(JournalError::StateConflict {
                path: archive.path.clone(),
                expected: "original directory inventory".into(),
                actual: "changed archive or source contents/permissions".into(),
            });
        }
        Ok(())
    }

    fn apply_tree_archive(&self, manifest: &TransactionManifest, after: bool) -> Result<()> {
        self.verify_tree_archive(manifest, !after)?;
        let (source, stored) = self.tree_archive_paths(manifest)?;
        let archived = after != manifest.tree_archive.as_ref().unwrap().restore_when_applied;
        if archived {
            fs::rename(source, stored)?;
        } else {
            fs::rename(stored, source)?;
        }
        Ok(())
    }

    fn transition_tree_archive(
        &self,
        mut manifest: TransactionManifest,
        side: SnapshotSide,
        state: TransactionState,
    ) -> Result<TransactionManifest> {
        let after = matches!(side, SnapshotSide::After);
        self.apply_tree_archive(&manifest, after)?;
        manifest.state = state;
        if let Err(error) = self.write_manifest(&manifest) {
            return match self.apply_tree_archive(&manifest, !after) {
                Ok(()) => Err(error),
                Err(rollback) => Err(JournalError::RollbackFailed {
                    original: error.to_string(),
                    failures: vec![rollback.to_string()],
                }),
            };
        }
        Ok(manifest)
    }

    pub fn record(
        &self,
        label: Option<String>,
        changes: &[JournalChange],
    ) -> Result<TransactionManifest> {
        let id = transaction_id();
        let tx_root = self.journal_root.join(&id);
        let before_root = tx_root.join("before");
        let after_root = tx_root.join("after");
        fs::create_dir_all(&before_root)?;
        fs::create_dir_all(&after_root)?;

        let mut files = Vec::with_capacity(changes.len());

        for change in changes {
            validate_relative(Path::new(&change.path))?;
            let snapshot_key = blake3::hash(change.path.as_bytes()).to_hex().to_string();

            if let Some(before) = &change.before {
                fs::write(before_root.join(&snapshot_key), before)?;
            }
            if let Some(after) = &change.after {
                fs::write(after_root.join(&snapshot_key), after)?;
            }

            files.push(TransactionFile {
                path: change.path.clone(),
                snapshot_key,
                before_hash: change.before.as_deref().map(hash_bytes),
                before_unix_mode: None,
                after_hash: change.after.as_deref().map(hash_bytes),
                after_unix_mode: if change.after.is_some() {
                    unix_mode(&self.resolve(&change.path)?)?
                } else {
                    None
                },
            });
        }

        let manifest = TransactionManifest {
            id,
            state: TransactionState::Applied,
            label,
            files,
            directory: None,
            tree_move: None,
            tree_archive: None,
        };
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    fn prepare(
        &self,
        label: Option<String>,
        changes: &[JournalChange],
        destination_modes: &BTreeMap<String, u32>,
    ) -> Result<TransactionManifest> {
        let id = transaction_id();
        let tx_root = self.journal_root.join(&id);
        let before_root = tx_root.join("before");
        let after_root = tx_root.join("after");
        fs::create_dir_all(&before_root)?;
        fs::create_dir_all(&after_root)?;

        let mut files = Vec::with_capacity(changes.len());

        for change in changes {
            validate_relative(Path::new(&change.path))?;
            let snapshot_key = blake3::hash(change.path.as_bytes()).to_hex().to_string();

            if let Some(before) = &change.before {
                fs::write(before_root.join(&snapshot_key), before)?;
            }
            if let Some(after) = &change.after {
                fs::write(after_root.join(&snapshot_key), after)?;
            }

            files.push(TransactionFile {
                path: change.path.clone(),
                snapshot_key,
                before_hash: change.before.as_deref().map(hash_bytes),
                before_unix_mode: if change.before.is_some() {
                    unix_mode(&self.resolve(&change.path)?)?
                } else {
                    None
                },
                after_hash: change.after.as_deref().map(hash_bytes),
                after_unix_mode: destination_modes.get(&change.path).copied(),
            });
        }

        let manifest = TransactionManifest {
            id,
            state: TransactionState::Prepared,
            label,
            files,
            directory: None,
            tree_move: None,
            tree_archive: None,
        };
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    pub fn load(&self, id: &str) -> Result<TransactionManifest> {
        validate_transaction_id(id)?;
        let path = self.journal_root.join(id).join("manifest.json");
        self.check_internal_path(&path)?;
        if !path.is_file() {
            return Err(JournalError::MissingTransaction(id.to_string()));
        }
        let manifest: TransactionManifest = serde_json::from_slice(&fs::read(path)?)?;
        if manifest.id != id {
            return Err(JournalError::InvalidPath(
                "manifest transaction id mismatch".into(),
            ));
        }
        Ok(manifest)
    }

    pub fn undo(&self, id: &str) -> Result<TransactionManifest> {
        let manifest = self.load(id)?;
        if manifest.state == TransactionState::Undone {
            return Ok(manifest);
        }
        if manifest.state == TransactionState::Prepared {
            return Err(JournalError::StateConflict {
                path: manifest.id.clone(),
                expected: "applied transaction".to_string(),
                actual: "prepared transaction".to_string(),
            });
        }

        self.verify_side(&manifest, SnapshotSide::After)?;
        self.transition(manifest, SnapshotSide::Before, TransactionState::Undone)
    }

    pub fn redo(&self, id: &str) -> Result<TransactionManifest> {
        let manifest = self.load(id)?;
        if manifest.state == TransactionState::Applied {
            return Ok(manifest);
        }
        if manifest.state == TransactionState::Prepared {
            return Err(JournalError::StateConflict {
                path: manifest.id.clone(),
                expected: "undone transaction".to_string(),
                actual: "prepared transaction".to_string(),
            });
        }

        self.verify_side(&manifest, SnapshotSide::Before)?;
        self.transition(manifest, SnapshotSide::After, TransactionState::Applied)
    }

    fn verify_side(&self, manifest: &TransactionManifest, side: SnapshotSide) -> Result<()> {
        if manifest.tree_archive.is_some() {
            return self.verify_tree_archive(manifest, matches!(side, SnapshotSide::After));
        }
        if let Some(movement) = &manifest.tree_move {
            if !manifest.files.is_empty() || manifest.directory.is_some() {
                return Err(JournalError::InvalidPath("mixed tree transaction".into()));
            }
            return movement.verify(self, matches!(side, SnapshotSide::After));
        }
        if let Some(directory) = &manifest.directory {
            if !manifest.files.is_empty() || directory.before_exists == directory.after_exists {
                return Err(JournalError::InvalidPath(
                    "invalid directory transaction".into(),
                ));
            }
            return self.verify_directory(directory, side);
        }
        for file in &manifest.files {
            let path = self.resolve(&file.path)?;
            let expected = side.hash(file);

            match (expected, path.exists()) {
                (None, false) => {}
                (None, true) => {
                    return Err(JournalError::StateConflict {
                        path: file.path.clone(),
                        expected: "<absent>".to_string(),
                        actual: hash_path(&path)?,
                    });
                }
                (Some(expected), false) => {
                    return Err(JournalError::StateConflict {
                        path: file.path.clone(),
                        expected: expected.clone(),
                        actual: "<absent>".to_string(),
                    });
                }
                (Some(expected), true) => {
                    let actual = hash_path(&path)?;
                    if &actual != expected {
                        return Err(JournalError::StateConflict {
                            path: file.path.clone(),
                            expected: expected.clone(),
                            actual,
                        });
                    }
                }
            }
        }
        Ok(())
    }

    fn transition(
        &self,
        mut manifest: TransactionManifest,
        side: SnapshotSide,
        state: TransactionState,
    ) -> Result<TransactionManifest> {
        if manifest.tree_archive.is_some() {
            return self.transition_tree_archive(manifest, side, state);
        }
        if manifest.tree_move.is_some() {
            return self.transition_tree_move(manifest, side, state);
        }
        if manifest.directory.is_some() {
            return self.transition_directory(manifest, side, state);
        }
        // Both sides must be intact before writes: the opposite side is needed
        // for recovery if a later member fails. apply_one rechecks before use.
        self.verify_snapshots(&manifest)?;
        let restore = side.opposite();
        // History intentionally permits chmod on existing files. If rollback
        // recreates one of those files, retain its permissions at operation start.
        let mut rollback_files = manifest.files.clone();
        for file in &mut rollback_files {
            if restore.hash(file).is_some() {
                let mode = unix_mode(&self.resolve(&file.path)?)?;
                match restore {
                    SnapshotSide::Before => file.before_unix_mode = mode,
                    SnapshotSide::After => file.after_unix_mode = mode,
                }
            }
        }
        let mut applied = Vec::new();
        for (file, rollback_file) in manifest.files.iter().zip(rollback_files) {
            if let Err(error) = self.apply_one(&manifest, file, side) {
                return Err(self.rollback(&manifest, &applied, restore, error));
            }
            applied.push(rollback_file);
        }
        manifest.state = state;
        if let Err(error) = self.write_manifest(&manifest) {
            return Err(self.rollback(&manifest, &applied, restore, error));
        }
        Ok(manifest)
    }

    fn rollback(
        &self,
        manifest: &TransactionManifest,
        applied: &[TransactionFile],
        side: SnapshotSide,
        original: JournalError,
    ) -> JournalError {
        let failures = applied
            .iter()
            .rev()
            .filter_map(|file| {
                self.apply_one(manifest, file, side)
                    .err()
                    .map(|error| format!("{}: {error}", file.path))
            })
            .collect::<Vec<_>>();
        if failures.is_empty() {
            original
        } else {
            JournalError::RollbackFailed {
                original: original.to_string(),
                failures,
            }
        }
    }

    fn apply_one(
        &self,
        manifest: &TransactionManifest,
        file: &TransactionFile,
        side: SnapshotSide,
    ) -> Result<Option<u32>> {
        let path = self.resolve(&file.path)?;

        if side.hash(file).is_some() {
            let bytes = self.read_snapshot(manifest, file, side)?;
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            let recreate_mode = match side {
                SnapshotSide::Before => file.before_unix_mode,
                SnapshotSide::After => file.after_unix_mode,
            };
            return atomic_write_with_mode(&path, &bytes, recreate_mode);
        } else if path.is_file() {
            fs::remove_file(path)?;
        } else if path.exists() {
            return Err(JournalError::StateConflict {
                path: file.path.clone(),
                expected: "file or absent path".into(),
                actual: "non-file appeared during file transaction".into(),
            });
        }

        Ok(None)
    }

    fn verify_snapshots(&self, manifest: &TransactionManifest) -> Result<()> {
        for file in &manifest.files {
            for side in [SnapshotSide::Before, SnapshotSide::After] {
                if side.hash(file).is_some() {
                    self.read_snapshot(manifest, file, side)?;
                }
            }
        }
        Ok(())
    }

    fn read_snapshot(
        &self,
        manifest: &TransactionManifest,
        file: &TransactionFile,
        side: SnapshotSide,
    ) -> Result<Vec<u8>> {
        validate_transaction_id(&manifest.id)?;
        validate_relative(Path::new(&file.path))?;
        if file.snapshot_key != blake3::hash(file.path.as_bytes()).to_hex().as_str() {
            return Err(JournalError::InvalidPath("invalid snapshot key".into()));
        }
        let path = self
            .journal_root
            .join(&manifest.id)
            .join(side.directory_name())
            .join(&file.snapshot_key);
        self.check_internal_path(&path)?;
        if !fs::metadata(&path)?.is_file() {
            return Err(JournalError::InvalidPath(
                "snapshot must be a regular file".into(),
            ));
        }
        let bytes = fs::read(&path)?;
        let actual = hash_bytes(&bytes);
        if side.hash(file).as_ref() != Some(&actual) {
            return Err(JournalError::StateConflict {
                path: format!(
                    "journal snapshot {}/{} for {}",
                    manifest.id,
                    side.directory_name(),
                    file.path
                ),
                expected: side.hash(file).clone().unwrap_or_else(|| "<absent>".into()),
                actual,
            });
        }
        Ok(bytes)
    }

    fn check_internal_path(&self, path: &Path) -> Result<()> {
        let relative = path
            .strip_prefix(&self.workspace_root)
            .map_err(|_| JournalError::InvalidPath(path.display().to_string()))?;
        let mut current = self.workspace_root.clone();
        for part in relative.components() {
            if !matches!(part, Component::Normal(_)) {
                return Err(JournalError::InvalidPath(path.display().to_string()));
            }
            current.push(part);
            match fs::symlink_metadata(&current) {
                Ok(metadata) if metadata.file_type().is_symlink() => {
                    return Err(JournalError::InvalidPath(current.display().to_string()));
                }
                Ok(_) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }

    pub(crate) fn resolve(&self, relative: &str) -> Result<PathBuf> {
        let relative = Path::new(relative);
        validate_relative(relative)?;
        let mut path = self.workspace_root.clone();
        for component in relative.components() {
            if let Component::Normal(part) = component {
                path.push(part);
                match fs::symlink_metadata(&path) {
                    Ok(metadata) if metadata.file_type().is_symlink() => {
                        return Err(JournalError::InvalidPath(relative.display().to_string()));
                    }
                    Ok(_) => {}
                    Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                    Err(error) => return Err(error.into()),
                }
            }
        }
        Ok(path)
    }

    fn write_manifest(&self, manifest: &TransactionManifest) -> Result<()> {
        validate_transaction_id(&manifest.id)?;
        let tx_root = self.journal_root.join(&manifest.id);
        self.check_internal_path(&tx_root.join("manifest.json"))?;
        fs::create_dir_all(&tx_root)?;
        let data = serde_json::to_vec_pretty(manifest)?;
        atomic_write(&tx_root.join("manifest.json"), &data)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy)]
enum SnapshotSide {
    Before,
    After,
}

impl SnapshotSide {
    fn opposite(self) -> Self {
        match self {
            Self::Before => Self::After,
            Self::After => Self::Before,
        }
    }

    fn hash<'a>(&self, file: &'a TransactionFile) -> &'a Option<String> {
        match self {
            Self::Before => &file.before_hash,
            Self::After => &file.after_hash,
        }
    }

    fn directory_name(&self) -> &'static str {
        match self {
            Self::Before => "before",
            Self::After => "after",
        }
    }
}

fn validate_transaction_id(id: &str) -> Result<()> {
    if id.is_empty()
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err(JournalError::InvalidPath("invalid transaction id".into()));
    }
    Ok(())
}

fn validate_relative(path: &Path) -> Result<()> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || !path
            .components()
            .any(|part| matches!(part, Component::Normal(_)))
    {
        return Err(JournalError::InvalidPath(path.display().to_string()));
    }

    for component in path.components() {
        match component {
            Component::Normal(part) if part == INTERNAL_DIR => {
                return Err(JournalError::InvalidPath(path.display().to_string()));
            }
            Component::Normal(_) | Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(JournalError::InvalidPath(path.display().to_string()));
            }
        }
    }

    Ok(())
}

fn hash_bytes(bytes: &[u8]) -> String {
    blake3::hash(bytes).to_hex().to_string()
}

fn hash_path(path: &Path) -> Result<String> {
    Ok(hash_bytes(&fs::read(path)?))
}

fn transaction_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("tx-{nanos}-{}", std::process::id())
}

pub(crate) fn unix_mode(path: &Path) -> Result<Option<u32>> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        match fs::metadata(path) {
            Ok(metadata) => Ok(Some(metadata.permissions().mode() & 0o777)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.into()),
        }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(None)
    }
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    atomic_write_with_mode(path, bytes, None).map(|_| ())
}

fn atomic_write_with_mode(
    path: &Path,
    bytes: &[u8],
    recreate_mode: Option<u32>,
) -> Result<Option<u32>> {
    let parent = path
        .parent()
        .ok_or_else(|| JournalError::InvalidPath(path.display().to_string()))?;
    fs::create_dir_all(parent)?;

    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("snapshot");
    let temp = parent.join(format!(".{name}.ffsake-journal-tmp-{}", std::process::id()));
    Ok(crate::atomic::replace_file(
        path,
        &temp,
        bytes,
        recreate_mode,
    )?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_at(base: &Path) -> PathBuf {
        // A timestamp is a candidate name, not a reservation. Never share a
        // directory with another parallel test, even if their clocks coincide.
        for suffix in 0u64.. {
            let root = base.with_extension(suffix.to_string());
            match fs::create_dir(&root) {
                Ok(()) => {
                    fs::create_dir(root.join("src")).unwrap();
                    fs::write(root.join("src/lib.rs"), b"before").unwrap();
                    return root;
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                Err(error) => panic!("cannot allocate journal fixture: {error}"),
            }
        }
        unreachable!("fixture suffix space exhausted")
    }

    fn fixture() -> PathBuf {
        let id = transaction_id();
        fixture_at(&std::env::temp_dir().join(format!("ffsake-journal-{id}")))
    }

    #[test]
    fn fixture_name_collisions_do_not_share_directories() {
        let root = fixture();
        let base = root.join("collision");
        let first = fixture_at(&base);
        fs::write(first.join("src/lib.rs"), b"first fixture").unwrap();
        let second = fixture_at(&base);
        assert_ne!(first, second);
        assert_eq!(
            fs::read(first.join("src/lib.rs")).unwrap(),
            b"first fixture"
        );
        assert_eq!(fs::read(second.join("src/lib.rs")).unwrap(), b"before");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn records_undoes_and_redoes() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();

        fs::write(root.join("src/lib.rs"), b"after").unwrap();
        let manifest = journal
            .record(
                Some("rename".into()),
                &[JournalChange {
                    path: "src/lib.rs".into(),
                    before: Some(b"before".to_vec()),
                    after: Some(b"after".to_vec()),
                }],
            )
            .unwrap();

        journal.undo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"before");

        journal.redo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"after");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_stale_undo() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();

        fs::write(root.join("src/lib.rs"), b"after").unwrap();
        let manifest = journal
            .record(
                None,
                &[JournalChange {
                    path: "src/lib.rs".into(),
                    before: Some(b"before".to_vec()),
                    after: Some(b"after".to_vec()),
                }],
            )
            .unwrap();

        fs::write(root.join("src/lib.rs"), b"someone else changed this").unwrap();

        assert!(matches!(
            journal.undo(&manifest.id),
            Err(JournalError::StateConflict { .. })
        ));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn handles_created_files() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();

        fs::write(root.join("new.txt"), b"created").unwrap();
        let manifest = journal
            .record(
                None,
                &[JournalChange {
                    path: "new.txt".into(),
                    before: None,
                    after: Some(b"created".to_vec()),
                }],
            )
            .unwrap();

        journal.undo(&manifest.id).unwrap();
        assert!(!root.join("new.txt").exists());

        journal.redo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("new.txt")).unwrap(), b"created");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn commit_owns_the_write_and_can_undo_it() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();

        let manifest = journal
            .commit(
                Some("edit".into()),
                &[("src/lib.rs".into(), Some(b"committed".to_vec()))],
            )
            .unwrap();

        assert_eq!(manifest.state, TransactionState::Applied);
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"committed");

        journal.undo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"before");

        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn preserves_executable_permissions_across_commit_undo_and_redo() {
        use std::os::unix::fs::PermissionsExt;

        let root = fixture();
        let script = root.join("src/run.sh");
        fs::write(&script, b"#!/bin/sh\necho before\n").unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        let journal = Journal::open(&root).unwrap();
        let mode = || fs::metadata(&script).unwrap().permissions().mode() & 0o777;

        let manifest = journal
            .commit(
                None,
                &[(
                    "src/run.sh".into(),
                    Some(b"#!/bin/sh\necho after\n".to_vec()),
                )],
            )
            .unwrap();
        assert_eq!(fs::read(&script).unwrap(), b"#!/bin/sh\necho after\n");
        assert_eq!(mode(), 0o755);

        journal.undo(&manifest.id).unwrap();
        assert_eq!(fs::read(&script).unwrap(), b"#!/bin/sh\necho before\n");
        assert_eq!(mode(), 0o755);

        journal.redo(&manifest.id).unwrap();
        assert_eq!(fs::read(&script).unwrap(), b"#!/bin/sh\necho after\n");
        assert_eq!(mode(), 0o755);

        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn deleted_file_permissions_survive_reopened_history() {
        use std::os::unix::fs::PermissionsExt;
        let root = fixture();
        let path = root.join("src/lib.rs");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o751)).unwrap();
        let manifest = Journal::open(&root)
            .unwrap()
            .commit(None, &[("src/lib.rs".into(), None)])
            .unwrap();
        assert_eq!(manifest.files[0].before_unix_mode, Some(0o751));
        assert!(!path.exists());
        let journal = Journal::open(&root).unwrap();
        for _ in 0..2 {
            journal.undo(&manifest.id).unwrap();
            assert_eq!(fs::read(&path).unwrap(), b"before");
            assert_eq!(
                fs::metadata(&path).unwrap().permissions().mode() & 0o7777,
                0o751
            );
            journal.redo(&manifest.id).unwrap();
            assert!(!path.exists());
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn history_preserves_current_permissions_when_file_still_exists() {
        use std::os::unix::fs::PermissionsExt;
        let root = fixture();
        let path = root.join("src/lib.rs");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(None, &[("src/lib.rs".into(), Some(b"after".to_vec()))])
            .unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        journal.undo(&manifest.id).unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o700
        );
        journal.redo(&manifest.id).unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o700
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn legacy_manifest_without_permissions_remains_usable() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(None, &[("src/lib.rs".into(), None)])
            .unwrap();
        let mut legacy = serde_json::to_value(&manifest).unwrap();
        legacy["files"][0]
            .as_object_mut()
            .unwrap()
            .remove("before_unix_mode");
        let manifest_path = root
            .join(".ffsake/journal")
            .join(&manifest.id)
            .join("manifest.json");
        fs::write(manifest_path, serde_json::to_vec(&legacy).unwrap()).unwrap();
        assert_eq!(
            journal.load(&manifest.id).unwrap().files[0].before_unix_mode,
            None
        );
        journal.undo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"before");
        journal.redo(&manifest.id).unwrap();
        assert!(!root.join("src/lib.rs").exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn failed_history_write_rolls_back_prior_files_and_can_retry() {
        let root = fixture();
        fs::write(root.join("second.txt"), b"second before").unwrap();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(
                None,
                &[
                    ("src/lib.rs".into(), Some(b"first after".to_vec())),
                    ("second.txt".into(), Some(b"second after".to_vec())),
                ],
            )
            .unwrap();
        let obstruction = root.join(format!(
            ".second.txt.ffsake-journal-tmp-{}",
            std::process::id()
        ));
        fs::create_dir(&obstruction).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"first after");
        assert_eq!(fs::read(root.join("second.txt")).unwrap(), b"second after");
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Applied
        );
        fs::remove_dir(&obstruction).unwrap();
        journal.undo(&manifest.id).unwrap();
        fs::create_dir(&obstruction).unwrap();
        assert!(journal.redo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"before");
        assert_eq!(fs::read(root.join("second.txt")).unwrap(), b"second before");
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Undone
        );
        fs::remove_dir(&obstruction).unwrap();
        journal.redo(&manifest.id).unwrap();
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"first after");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn history_manifest_failure_restores_files_and_previous_state() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(None, &[("src/lib.rs".into(), Some(b"after".to_vec()))])
            .unwrap();
        let obstruction = root
            .join(".ffsake/journal")
            .join(&manifest.id)
            .join(format!(
                ".manifest.json.ffsake-journal-tmp-{}",
                std::process::id()
            ));
        fs::create_dir(&obstruction).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"after");
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Applied
        );
        fs::remove_dir(&obstruction).unwrap();
        journal.undo(&manifest.id).unwrap();
        fs::create_dir(&obstruction).unwrap();
        assert!(journal.redo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"before");
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Undone
        );
        fs::remove_dir(&obstruction).unwrap();
        journal.redo(&manifest.id).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn undo_rollback_recreates_current_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let root = fixture();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(
                None,
                &[
                    ("created.sh".into(), Some(b"created".to_vec())),
                    ("src/lib.rs".into(), Some(b"after".to_vec())),
                ],
            )
            .unwrap();
        fs::set_permissions(root.join("created.sh"), fs::Permissions::from_mode(0o700)).unwrap();
        let obstruction = root
            .join("src")
            .join(format!(".lib.rs.ffsake-journal-tmp-{}", std::process::id()));
        fs::create_dir(&obstruction).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("created.sh")).unwrap(), b"created");
        assert_eq!(
            fs::metadata(root.join("created.sh"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn incomplete_history_rollback_is_reported_explicitly() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(
                None,
                &[
                    ("created.txt".into(), Some(b"created".to_vec())),
                    ("src/lib.rs".into(), Some(b"after".to_vec())),
                ],
            )
            .unwrap();
        // Undo removes the created file, then fails on the second write. Its
        // recovery snapshot has been lost, so rollback must not claim success.
        fs::remove_file(
            root.join(".ffsake/journal")
                .join(&manifest.id)
                .join("after")
                .join(&manifest.files[0].snapshot_key),
        )
        .unwrap();
        let obstruction = root
            .join("src")
            .join(format!(".lib.rs.ffsake-journal-tmp-{}", std::process::id()));
        fs::create_dir(&obstruction).unwrap();
        journal
            .apply_one(&manifest, &manifest.files[0], SnapshotSide::Before)
            .unwrap();
        let original = journal
            .apply_one(&manifest, &manifest.files[1], SnapshotSide::Before)
            .unwrap_err();
        let error = journal.rollback(
            &manifest,
            &manifest.files[..1],
            SnapshotSide::After,
            original,
        );
        assert!(
            matches!(&error, JournalError::RollbackFailed { failures, .. } if failures.len() == 1 && failures[0].contains("created.txt"))
        );
        assert!(error.to_string().contains("rollback incomplete"));
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Applied
        );
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn corrupt_snapshot_is_rejected_before_any_history_write() {
        let root = fixture();
        fs::write(root.join("second.txt"), b"second before").unwrap();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(
                None,
                &[
                    ("src/lib.rs".into(), Some(b"first after".to_vec())),
                    ("second.txt".into(), Some(b"second after".to_vec())),
                ],
            )
            .unwrap();
        let snapshot = root
            .join(".ffsake/journal")
            .join(&manifest.id)
            .join("before")
            .join(&manifest.files[1].snapshot_key);
        fs::write(snapshot, b"corrupted snapshot").unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"first after");
        assert_eq!(fs::read(root.join("second.txt")).unwrap(), b"second after");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn missing_or_corrupt_snapshots_in_either_direction_are_fail_closed() {
        for redo in [false, true] {
            for side in ["before", "after"] {
                for missing in [false, true] {
                    let root = fixture();
                    let journal = Journal::open(&root).unwrap();
                    let manifest = journal
                        .commit(None, &[("src/lib.rs".into(), Some(b"after".to_vec()))])
                        .unwrap();
                    if redo {
                        journal.undo(&manifest.id).unwrap();
                    }
                    let snapshot = root
                        .join(".ffsake/journal")
                        .join(&manifest.id)
                        .join(side)
                        .join(&manifest.files[0].snapshot_key);
                    let original = fs::read(&snapshot).unwrap();
                    if missing {
                        fs::remove_file(&snapshot).unwrap();
                    } else {
                        fs::write(&snapshot, b"corrupt").unwrap();
                    }
                    let result = if redo {
                        journal.redo(&manifest.id)
                    } else {
                        journal.undo(&manifest.id)
                    };
                    assert!(result.is_err());
                    assert_eq!(
                        fs::read(root.join("src/lib.rs")).unwrap(),
                        if redo {
                            b"before".as_slice()
                        } else {
                            b"after".as_slice()
                        }
                    );
                    fs::write(&snapshot, original).unwrap();
                    if redo {
                        journal.redo(&manifest.id).unwrap();
                    } else {
                        journal.undo(&manifest.id).unwrap();
                    }
                    fs::remove_dir_all(root).unwrap();
                }
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn journal_snapshot_and_manifest_symlinks_are_rejected() {
        use std::os::unix::fs::symlink;
        let root = fixture();
        let other = fixture();
        let journal = Journal::open(&root).unwrap();
        let manifest = journal
            .commit(None, &[("src/lib.rs".into(), Some(b"after".to_vec()))])
            .unwrap();
        let tx = root.join(".ffsake/journal").join(&manifest.id);
        for path in [
            tx.join("before").join(&manifest.files[0].snapshot_key),
            tx.join("manifest.json"),
        ] {
            let bytes = fs::read(&path).unwrap();
            let external = other.join("external");
            fs::write(&external, &bytes).unwrap();
            fs::remove_file(&path).unwrap();
            symlink(&external, &path).unwrap();
            assert!(journal.undo(&manifest.id).is_err());
            assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"after");
            assert_eq!(fs::read(&external).unwrap(), bytes);
            fs::remove_file(&path).unwrap();
            fs::write(&path, bytes).unwrap();
        }
        let external_tx = other.join("tx");
        fs::rename(&tx, &external_tx).unwrap();
        symlink(&external_tx, &tx).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"after");
        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(other).unwrap();
    }

    #[test]
    fn journal_rejects_invalid_ids_and_snapshot_keys() {
        let root = fixture();
        let journal = Journal::open(&root).unwrap();
        for id in ["", "..", "../elsewhere", "/tmp/elsewhere"] {
            assert!(journal.load(id).is_err());
        }
        let manifest = journal
            .commit(None, &[("src/lib.rs".into(), Some(b"after".to_vec()))])
            .unwrap();
        let path = root
            .join(".ffsake/journal")
            .join(&manifest.id)
            .join("manifest.json");
        let mut altered = manifest.clone();
        altered.id = "different-id".into();
        fs::write(&path, serde_json::to_vec(&altered).unwrap()).unwrap();
        assert!(journal.load(&manifest.id).is_err());
        altered = manifest.clone();
        altered.files[0].snapshot_key = "../../outside".into();
        fs::write(&path, serde_json::to_vec(&altered).unwrap()).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.join("src/lib.rs")).unwrap(), b"after");
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn preexisting_temporary_symlink_cannot_redirect_atomic_write() {
        use std::os::unix::fs::symlink;
        let root = fixture();
        let other = fixture();
        let path = root.join("src/lib.rs");
        let temp = root
            .join("src")
            .join(format!(".lib.rs.ffsake-journal-tmp-{}", std::process::id()));
        let sentinel = other.join("sentinel");
        fs::write(&sentinel, b"external sentinel").unwrap();
        symlink(&sentinel, &temp).unwrap();
        assert!(atomic_write(&path, b"must not escape").is_err());
        assert_eq!(fs::read(&sentinel).unwrap(), b"external sentinel");
        assert_eq!(fs::read(&path).unwrap(), b"before");
        assert!(
            fs::symlink_metadata(&temp)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(other).unwrap();
    }
}

#[cfg(test)]
mod directory_tests {
    use super::*;

    #[test]
    fn empty_directory_history_survives_reopening_and_rejects_added_contents() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let created = journal.commit_directory("empty", true).unwrap();
        assert!(root.path().join("empty").is_dir());
        let journal = Journal::open(root.path()).unwrap();
        fs::write(root.path().join("empty/keep"), b"keep").unwrap();
        assert!(journal.undo(&created.id).is_err());
        assert_eq!(fs::read(root.path().join("empty/keep")).unwrap(), b"keep");
        fs::remove_file(root.path().join("empty/keep")).unwrap();
        journal.undo(&created.id).unwrap();
        assert!(!root.path().join("empty").exists());
        journal.redo(&created.id).unwrap();
        let removed = journal.commit_directory("empty", false).unwrap();
        assert!(!root.path().join("empty").exists());
        journal.undo(&removed.id).unwrap();
        journal.redo(&removed.id).unwrap();
        assert!(!root.path().join("empty").exists());
    }

    #[test]
    fn file_replay_never_recursively_removes_an_unexpected_directory() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::write(root.path().join("entry"), b"original").unwrap();
        let removed = journal.commit(None, &[("entry".into(), None)]).unwrap();
        fs::create_dir(root.path().join("entry")).unwrap();
        fs::write(root.path().join("entry/keep"), b"preserve").unwrap();
        // Exercise the write-time check directly, simulating a directory
        // appearing after the transaction-wide preflight has already passed.
        assert!(
            journal
                .apply_one(&removed, &removed.files[0], SnapshotSide::After)
                .is_err()
        );
        assert_eq!(
            fs::read(root.path().join("entry/keep")).unwrap(),
            b"preserve"
        );
    }

    #[test]
    fn directory_boundaries_and_parent_preconditions() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        for path in [
            "",
            ".",
            "..",
            "../outside",
            ".ffsake/hidden",
            "missing/child",
        ] {
            assert!(journal.commit_directory(path, true).is_err(), "{path}");
        }
        fs::write(root.path().join("file"), b"data").unwrap();
        assert!(journal.commit_directory("file", false).is_err());
        assert!(journal.commit_directory("file", true).is_err());
        assert!(journal.commit_directory("absent", false).is_err());
        assert_eq!(fs::read(root.path().join("file")).unwrap(), b"data");
    }

    #[test]
    fn copied_tree_redo_is_independent_of_original_source() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir_all(root.path().join("source/empty")).unwrap();
        fs::write(root.path().join("source/data"), b"original\0\xff").unwrap();
        let copy = crate::tree_move::TreeCopy::preview(&journal, "source", "copied").unwrap();
        let transaction = journal.commit_tree_copy(&copy).unwrap();
        assert!(root.path().join("source/empty").is_dir());
        assert!(root.path().join("copied/empty").is_dir());
        assert_eq!(
            fs::read(root.path().join("copied/data")).unwrap(),
            b"original\0\xff"
        );
        let journal = Journal::open(root.path()).unwrap();
        journal.undo(&transaction.id).unwrap();
        assert!(!root.path().join("copied").exists());
        fs::write(root.path().join("source/data"), b"later source").unwrap();
        journal.redo(&transaction.id).unwrap();
        assert_eq!(
            fs::read(root.path().join("source/data")).unwrap(),
            b"later source"
        );
        assert_eq!(
            fs::read(root.path().join("copied/data")).unwrap(),
            b"original\0\xff"
        );
        fs::write(root.path().join("copied/data"), b"later copy").unwrap();
        assert!(journal.undo(&transaction.id).is_err());
        assert_eq!(
            fs::read(root.path().join("copied/data")).unwrap(),
            b"later copy"
        );
    }

    #[test]
    fn copied_tree_stale_source_and_persistence_failure_preserve_both_sides() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/data"), b"original").unwrap();
        let copy = crate::tree_move::TreeCopy::preview(&journal, "source", "copied").unwrap();
        fs::write(root.path().join("source/data"), b"changed").unwrap();
        assert!(journal.commit_tree_copy(&copy).is_err());
        assert!(!root.path().join("copied").exists());
        let copy = crate::tree_move::TreeCopy::preview(&journal, "source", "copied").unwrap();
        let transaction = journal.commit_tree_copy(&copy).unwrap();
        let path = journal
            .journal_root
            .join(&transaction.id)
            .join("manifest.json");
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(
            journal
                .transition_tree_archive(
                    transaction,
                    SnapshotSide::Before,
                    TransactionState::Undone
                )
                .is_err()
        );
        assert_eq!(
            fs::read(root.path().join("source/data")).unwrap(),
            b"changed"
        );
        assert_eq!(
            fs::read(root.path().join("copied/data")).unwrap(),
            b"changed"
        );
    }

    #[cfg(unix)]
    #[test]
    fn tree_copy_preserves_directory_and_file_modes() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir_all(root.path().join("source/empty")).unwrap();
        fs::write(root.path().join("source/run"), b"script").unwrap();
        fs::set_permissions(
            root.path().join("source/run"),
            fs::Permissions::from_mode(0o551),
        )
        .unwrap();
        fs::set_permissions(
            root.path().join("source/empty"),
            fs::Permissions::from_mode(0o550),
        )
        .unwrap();
        fs::set_permissions(
            root.path().join("source"),
            fs::Permissions::from_mode(0o750),
        )
        .unwrap();
        let copy = crate::tree_move::TreeCopy::preview(&journal, "source", "copied").unwrap();
        let transaction = journal.commit_tree_copy(&copy).unwrap();
        journal.undo(&transaction.id).unwrap();
        journal.redo(&transaction.id).unwrap();
        for (path, mode) in [
            ("copied", 0o750),
            ("copied/empty", 0o550),
            ("copied/run", 0o551),
        ] {
            assert_eq!(unix_mode(&root.path().join(path)).unwrap(), Some(mode));
        }
        // Restore fixture parent write access for temporary-directory cleanup.
        fs::set_permissions(
            root.path().join("source"),
            fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        fs::set_permissions(
            root.path().join("copied"),
            fs::Permissions::from_mode(0o755),
        )
        .unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn readonly_copy_root_publish_failure_preserves_source() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/data"), b"original").unwrap();
        fs::set_permissions(
            root.path().join("source"),
            fs::Permissions::from_mode(0o555),
        )
        .unwrap();
        let copy = crate::tree_move::TreeCopy::preview(&journal, "source", "copied").unwrap();
        // macOS requires directory write permission for a cross-parent rename.
        // Preserve the source's permissions and propagate the OS refusal.
        assert!(
            matches!(journal.commit_tree_copy(&copy), Err(JournalError::Io(error)) if error.kind() == io::ErrorKind::PermissionDenied)
        );
        assert!(!root.path().join("copied").exists());
        assert_eq!(
            fs::read(root.path().join("source/data")).unwrap(),
            b"original"
        );
        assert_eq!(unix_mode(&root.path().join("source")).unwrap(), Some(0o555));
        fs::set_permissions(
            root.path().join("source"),
            fs::Permissions::from_mode(0o755),
        )
        .unwrap();
    }

    #[test]
    fn archived_tree_history_checks_stale_source_and_snapshot_corruption() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir_all(root.path().join("source/empty")).unwrap();
        fs::write(root.path().join("source/data"), b"original").unwrap();
        let preview = journal.preview_tree_archive("source").unwrap();
        fs::write(root.path().join("source/extra"), b"keep").unwrap();
        assert!(journal.commit_tree_archive(preview).is_err());
        assert_eq!(fs::read(root.path().join("source/extra")).unwrap(), b"keep");
        fs::remove_file(root.path().join("source/extra")).unwrap();
        let preview = journal.preview_tree_archive("source").unwrap();
        let transaction = journal.commit_tree_archive(preview).unwrap();
        assert!(!root.path().join("source").exists());
        let stored = journal.journal_root.join(&transaction.id).join("tree-data");
        fs::write(stored.join("data"), b"corrupt").unwrap();
        assert!(journal.undo(&transaction.id).is_err());
        assert!(!root.path().join("source").exists());
        fs::write(stored.join("data"), b"original").unwrap();
        let journal = Journal::open(root.path()).unwrap();
        journal.undo(&transaction.id).unwrap();
        assert!(root.path().join("source/empty").is_dir());
        assert_eq!(
            fs::read(root.path().join("source/data")).unwrap(),
            b"original"
        );
        journal.redo(&transaction.id).unwrap();
        assert!(!root.path().join("source").exists());
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/keep"), b"keep").unwrap();
        assert!(journal.undo(&transaction.id).is_err());
        assert_eq!(fs::read(root.path().join("source/keep")).unwrap(), b"keep");
    }

    #[test]
    fn archive_manifest_failure_restores_archive() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/keep"), b"keep").unwrap();
        let transaction = journal
            .commit_tree_archive(journal.preview_tree_archive("source").unwrap())
            .unwrap();
        let path = journal
            .journal_root
            .join(&transaction.id)
            .join("manifest.json");
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        let stored = journal.journal_root.join(&transaction.id).join("tree-data");
        assert!(
            journal
                .transition_tree_archive(
                    transaction,
                    SnapshotSide::Before,
                    TransactionState::Undone
                )
                .is_err()
        );
        assert!(!root.path().join("source").exists());
        assert_eq!(fs::read(stored.join("keep")).unwrap(), b"keep");
    }

    #[cfg(unix)]
    #[test]
    fn archive_symlinks_cannot_redirect_history() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("sentinel"), b"keep").unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let transaction = journal
            .commit_tree_archive(journal.preview_tree_archive("source").unwrap())
            .unwrap();
        let stored = journal.journal_root.join(&transaction.id).join("tree-data");
        fs::remove_dir(&stored).unwrap();
        symlink(outside.path(), &stored).unwrap();
        assert!(journal.undo(&transaction.id).is_err());
        assert!(!root.path().join("source").exists());
        assert_eq!(fs::read(outside.path().join("sentinel")).unwrap(), b"keep");
    }

    #[test]
    fn tree_move_manifest_failure_restores_tree() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/keep"), b"preserve").unwrap();
        let movement = TransactionTreeMove::preview(&journal, "source", "destination").unwrap();
        let transaction = journal.commit_tree_move(movement).unwrap();
        let path = journal
            .journal_root
            .join(&transaction.id)
            .join("manifest.json");
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(
            journal
                .transition_tree_move(transaction, SnapshotSide::Before, TransactionState::Undone)
                .is_err()
        );
        assert!(!root.path().join("source").exists());
        assert_eq!(
            fs::read(root.path().join("destination/keep")).unwrap(),
            b"preserve"
        );
    }

    #[test]
    fn directory_manifest_failure_restores_previous_state() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let created = journal.commit_directory("empty", true).unwrap();
        let manifest_path = journal.journal_root.join(&created.id).join("manifest.json");
        fs::remove_file(&manifest_path).unwrap();
        fs::create_dir(&manifest_path).unwrap();
        assert!(
            journal
                .transition_directory(created, SnapshotSide::Before, TransactionState::Undone)
                .is_err()
        );
        assert!(root.path().join("empty").is_dir());
        assert!(
            fs::read_dir(root.path().join("empty"))
                .unwrap()
                .next()
                .is_none()
        );
    }

    #[cfg(unix)]
    #[test]
    fn directory_modes_and_symlink_guards() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        fs::create_dir(root.path().join("empty")).unwrap();
        fs::set_permissions(root.path().join("empty"), fs::Permissions::from_mode(0o750)).unwrap();
        symlink(root.path().join("empty"), root.path().join("alias")).unwrap();
        assert!(journal.commit_directory("alias", false).is_err());
        assert!(journal.commit_directory("alias/child", true).is_err());
        let removed = journal.commit_directory("empty", false).unwrap();
        journal.undo(&removed.id).unwrap();
        assert_eq!(unix_mode(&root.path().join("empty")).unwrap(), Some(0o750));
        fs::set_permissions(root.path().join("empty"), fs::Permissions::from_mode(0o700)).unwrap();
        assert!(journal.redo(&removed.id).is_err());
        assert!(root.path().join("empty").is_dir());
    }
}
