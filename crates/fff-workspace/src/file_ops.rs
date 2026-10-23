use crate::Workspace;
use crate::journal::{Journal, JournalChange};
use serde::Deserialize;
use serde_json::{Value, json};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum FileOperation {
    Create { path: String, content: String },
    Copy { from: String, to: String },
    Move { from: String, to: String },
    Delete { path: String },
}

pub(crate) struct FilePlan {
    changes: Vec<JournalChange>,
    checks: Vec<(String, Option<Vec<u8>>)>,
}

fn snapshot(journal: &Journal, path: &str) -> Result<Option<Vec<u8>>> {
    let resolved = journal.resolve(path)?;
    match fs::metadata(&resolved) {
        Ok(metadata) if metadata.is_file() => Ok(Some(fs::read(resolved)?)),
        Ok(_) => Err("file operations require a file, not a directory".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

impl FilePlan {
    pub(crate) fn preview(workspace: &Workspace, operation: FileOperation) -> Result<Self> {
        let journal = Journal::open(workspace.root())?;
        let mut changes = Vec::new();
        let mut checks = Vec::new();
        let moving = matches!(&operation, FileOperation::Move { .. });
        match operation {
            FileOperation::Create { path, content } => {
                if snapshot(&journal, &path)?.is_some() {
                    return Err("destination already exists; use preview_edit to edit it".into());
                }
                changes.push(JournalChange {
                    path,
                    before: None,
                    after: Some(content.into_bytes()),
                });
            }
            FileOperation::Copy { from, to } | FileOperation::Move { from, to } => {
                let bytes = snapshot(&journal, &from)?.ok_or("source file does not exist")?;
                if snapshot(&journal, &to)?.is_some() {
                    return Err("destination already exists; overwriting is not allowed".into());
                }
                checks.push((from.clone(), Some(bytes.clone())));
                if moving {
                    changes.push(JournalChange {
                        path: from,
                        before: Some(bytes.clone()),
                        after: None,
                    });
                }
                changes.push(JournalChange {
                    path: to,
                    before: None,
                    after: Some(bytes),
                });
            }
            FileOperation::Delete { path } => {
                let bytes = snapshot(&journal, &path)?.ok_or("source file does not exist")?;
                changes.push(JournalChange {
                    path,
                    before: Some(bytes),
                    after: None,
                });
            }
        }
        Ok(Self { changes, checks })
    }

    pub(crate) fn preview_batch(
        workspace: &Workspace,
        operations: Vec<FileOperation>,
    ) -> Result<Self> {
        if operations.is_empty() || operations.len() > 100 {
            return Err("batch must contain 1..=100 operations".into());
        }
        let journal = Journal::open(workspace.root())?;
        let mut combined = Self {
            changes: Vec::new(),
            checks: Vec::new(),
        };
        let mut writes: Vec<PathBuf> = Vec::new();
        let mut reads: Vec<PathBuf> = Vec::new();
        for operation in operations {
            let plan = Self::preview(workspace, operation)?;
            let new_writes = plan
                .changes
                .iter()
                .map(|change| journal.resolve(&change.path))
                .collect::<std::result::Result<Vec<_>, _>>()?;
            let new_reads = plan
                .checks
                .iter()
                .map(|(path, _)| journal.resolve(path))
                .collect::<std::result::Result<Vec<_>, _>>()?;
            // Operations describe independent changes against the same state.
            // Reject aliases, parent/child targets and read/write dependencies
            // rather than silently giving the array sequential script semantics.
            let overlaps = |a: &Path, b: &Path| a.starts_with(b) || b.starts_with(a);
            if new_writes
                .iter()
                .any(|a| writes.iter().chain(&reads).any(|b| overlaps(a, b)))
                || new_reads
                    .iter()
                    .any(|a| writes.iter().any(|b| overlaps(a, b)))
            {
                return Err("batch operations contain conflicting paths".into());
            }
            writes.extend(new_writes);
            reads.extend(new_reads);
            combined.changes.extend(plan.changes);
            combined.checks.extend(plan.checks);
        }
        Ok(combined)
    }

    pub(crate) fn description(&self) -> Value {
        let files = self
            .changes
            .iter()
            .map(|change| {
                let before = change.before.as_deref().unwrap_or_default();
                let after = change.after.as_deref().unwrap_or_default();
                let diff = match (std::str::from_utf8(before), std::str::from_utf8(after)) {
                    (Ok(before), Ok(after)) => Some(
                        similar::TextDiff::from_lines(before, after)
                            .unified_diff()
                            .header(&format!("a/{}", change.path), &format!("b/{}", change.path))
                            .to_string(),
                    ),
                    _ => None,
                };
                json!({
                    "path": change.path,
                    "operation": if change.before.is_none() { "create" } else { "delete" },
                    "before_bytes": before.len(), "after_bytes": after.len(),
                    "unified_diff": diff
                })
            })
            .collect::<Vec<_>>();
        json!({"changed_files": self.changes.len(), "files": files})
    }

    pub(crate) fn apply(&self, workspace: &Workspace) -> Result<Value> {
        let journal = Journal::open(workspace.root())?;
        for (path, expected) in &self.checks {
            if &snapshot(&journal, path)? != expected {
                return Err("source changed since preview".into());
            }
        }
        let transaction =
            journal.commit_changes(Some("resident file operation".into()), &self.changes)?;
        Ok(json!({"transaction": transaction.id, "changed_files": self.changes.len()}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture() -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "ffsake-batch-{nanos}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        root
    }

    fn operations(value: Value) -> Vec<FileOperation> {
        serde_json::from_value(value).unwrap()
    }

    #[test]
    fn creates_share_one_transaction_and_history() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"create","path":"pkg/a.py","content":"one\n"},
                {"op":"create","path":"pkg/b.py","content":"two\n"},
                {"op":"create","path":"pkg/c.py","content":""}
            ])),
        )
        .unwrap();
        assert!(!root.join("pkg").exists());
        let result = plan.apply(&workspace).unwrap();
        let id = result["transaction"].as_str().unwrap();
        let journal = Journal::open(&root).unwrap();
        assert_eq!(journal.load(id).unwrap().files.len(), 3);
        assert_eq!(fs::read(root.join("pkg/a.py")).unwrap(), b"one\n");
        journal.undo(id).unwrap();
        assert!(!root.join("pkg/a.py").exists());
        assert!(!root.join("pkg/b.py").exists());
        assert!(!root.join("pkg/c.py").exists());
        journal.redo(id).unwrap();
        assert_eq!(fs::read(root.join("pkg/a.py")).unwrap(), b"one\n");
        assert_eq!(fs::read(root.join("pkg/b.py")).unwrap(), b"two\n");
        assert_eq!(fs::read(root.join("pkg/c.py")).unwrap(), b"");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_conflicts_without_writing() {
        let root = fixture();
        fs::write(root.join("source"), b"source").unwrap();
        let workspace = Workspace::new(&root).unwrap();
        for value in [
            json!([
                {"op":"create","path":"a","content":"one"},
                {"op":"create","path":"./a","content":"two"}
            ]),
            json!([
                {"op":"create","path":"a","content":"one"},
                {"op":"create","path":"a/child","content":"two"}
            ]),
            json!([
                {"op":"copy","from":"source","to":"a"},
                {"op":"delete","path":"source"}
            ]),
            json!([
                {"op":"delete","path":"source"},
                {"op":"copy","from":"source","to":"a"}
            ]),
            json!([]),
        ] {
            assert!(FilePlan::preview_batch(&workspace, operations(value)).is_err());
            assert!(!root.join("a").exists());
            assert_eq!(fs::read(root.join("source")).unwrap(), b"source");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn failed_write_rolls_back_earlier_files() {
        use std::os::unix::fs::PermissionsExt;

        let root = fixture();
        let protected = root.join("protected");
        fs::create_dir(&protected).unwrap();
        fs::set_permissions(&protected, fs::Permissions::from_mode(0o555)).unwrap();
        // Privileged test runners can bypass directory permissions.
        let probe = protected.join("probe");
        if fs::write(&probe, b"probe").is_ok() {
            fs::set_permissions(&protected, fs::Permissions::from_mode(0o755)).unwrap();
            fs::remove_dir_all(root).unwrap();
            return;
        }
        let workspace = Workspace::new(&root).unwrap();
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"create","path":"first.txt","content":"must roll back"},
                {"op":"create","path":"protected/blocked.txt","content":"cannot write"}
            ])),
        )
        .unwrap();
        let result = plan.apply(&workspace);
        fs::set_permissions(&protected, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(result.is_err());
        assert!(!root.join("first.txt").exists());
        assert!(!protected.join("blocked.txt").exists());
        fs::remove_dir_all(root).unwrap();
    }
}
