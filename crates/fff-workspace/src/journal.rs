use serde::{Deserialize, Serialize};
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
    pub after_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TransactionManifest {
    pub id: String,
    pub state: TransactionState,
    pub label: Option<String>,
    pub files: Vec<TransactionFile>,
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
        let mut manifest = self.prepare(label, changes)?;
        self.verify_side(&manifest, SnapshotSide::Before)?;

        let mut applied = Vec::new();
        for file in &manifest.files {
            let result = self.apply_one(&manifest, file, SnapshotSide::After);
            if let Err(error) = result {
                for applied_file in applied.iter().rev() {
                    let _ = self.apply_one(&manifest, applied_file, SnapshotSide::Before);
                }
                return Err(error);
            }
            applied.push(file.clone());
        }

        manifest.state = TransactionState::Applied;
        self.write_manifest(&manifest)?;
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
                after_hash: change.after.as_deref().map(hash_bytes),
            });
        }

        let manifest = TransactionManifest {
            id,
            state: TransactionState::Applied,
            label,
            files,
        };
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    fn prepare(
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
                after_hash: change.after.as_deref().map(hash_bytes),
            });
        }

        let manifest = TransactionManifest {
            id,
            state: TransactionState::Prepared,
            label,
            files,
        };
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    pub fn load(&self, id: &str) -> Result<TransactionManifest> {
        let path = self.journal_root.join(id).join("manifest.json");
        if !path.is_file() {
            return Err(JournalError::MissingTransaction(id.to_string()));
        }
        Ok(serde_json::from_slice(&fs::read(path)?)?)
    }

    pub fn undo(&self, id: &str) -> Result<TransactionManifest> {
        let mut manifest = self.load(id)?;
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
        self.apply_side(&manifest, SnapshotSide::Before)?;
        manifest.state = TransactionState::Undone;
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    pub fn redo(&self, id: &str) -> Result<TransactionManifest> {
        let mut manifest = self.load(id)?;
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
        self.apply_side(&manifest, SnapshotSide::After)?;
        manifest.state = TransactionState::Applied;
        self.write_manifest(&manifest)?;
        Ok(manifest)
    }

    fn verify_side(&self, manifest: &TransactionManifest, side: SnapshotSide) -> Result<()> {
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

    fn apply_side(&self, manifest: &TransactionManifest, side: SnapshotSide) -> Result<()> {
        for file in &manifest.files {
            self.apply_one(manifest, file, side)?;
        }
        Ok(())
    }

    fn apply_one(
        &self,
        manifest: &TransactionManifest,
        file: &TransactionFile,
        side: SnapshotSide,
    ) -> Result<()> {
        let snapshot_root = self
            .journal_root
            .join(&manifest.id)
            .join(side.directory_name());
        let path = self.resolve(&file.path)?;

        if side.hash(file).is_some() {
            let bytes = fs::read(snapshot_root.join(&file.snapshot_key))?;
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            atomic_write(&path, &bytes)?;
        } else if path.is_file() {
            fs::remove_file(path)?;
        } else if path.is_dir() {
            fs::remove_dir_all(path)?;
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
        let tx_root = self.journal_root.join(&manifest.id);
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

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| JournalError::InvalidPath(path.display().to_string()))?;
    fs::create_dir_all(parent)?;

    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("snapshot");
    let temp = parent.join(format!(".{name}.ffsake-journal-tmp-{}", std::process::id()));
    fs::write(&temp, bytes)?;
    fs::rename(temp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        let id = transaction_id();
        let root = std::env::temp_dir().join(format!("ffsake-journal-{id}"));
        fs::create_dir_all(root.join("src")).unwrap();
        fs::write(root.join("src/lib.rs"), b"before").unwrap();
        root
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
}
