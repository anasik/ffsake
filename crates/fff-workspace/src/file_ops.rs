use crate::Workspace;
use crate::journal::{
    Journal, JournalChange, TransactionDirectory, TransactionTreeArchive, TransactionTreeMove,
    unix_mode,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum FileOperation {
    CopyDirectory {
        from: String,
        to: String,
    },
    DeleteDirectory {
        path: String,
    },
    MoveDirectory {
        from: String,
        to: String,
    },
    CreateDirectory {
        path: String,
    },
    RemoveDirectory {
        path: String,
    },
    Create {
        path: String,
        content: String,
    },
    Copy {
        from: String,
        to: String,
    },
    Move {
        from: String,
        to: String,
    },
    Delete {
        path: String,
    },
    Edit {
        path: String,
        old_text: String,
        new_text: String,
        expected_matches: usize,
    },
}

pub(crate) struct FilePlan {
    directory: Option<TransactionDirectory>,
    tree_move: Option<TransactionTreeMove>,
    tree_archive: Option<TransactionTreeArchive>,
    tree_copy: Option<crate::tree_move::TreeCopy>,
    changes: Vec<JournalChange>,
    checks: Vec<(String, Option<Vec<u8>>)>,
    mode_checks: BTreeMap<String, u32>,
    destination_modes: BTreeMap<String, u32>,
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
        let mut mode_checks = BTreeMap::new();
        let mut destination_modes = BTreeMap::new();
        let creating_directory = matches!(&operation, FileOperation::CreateDirectory { .. });
        let moving = matches!(&operation, FileOperation::Move { .. });
        match operation {
            FileOperation::CopyDirectory { from, to } => {
                let copy = crate::tree_move::TreeCopy::preview(&journal, &from, &to)?;
                return Ok(Self {
                    directory: None,
                    tree_move: None,
                    tree_archive: None,
                    tree_copy: Some(copy),
                    changes,
                    checks,
                    mode_checks,
                    destination_modes,
                });
            }
            FileOperation::DeleteDirectory { path } => {
                let archive = journal.preview_tree_archive(&path)?;
                return Ok(Self {
                    directory: None,
                    tree_move: None,
                    tree_archive: Some(archive),
                    tree_copy: None,
                    changes,
                    checks,
                    mode_checks,
                    destination_modes,
                });
            }
            FileOperation::MoveDirectory { from, to } => {
                let movement = TransactionTreeMove::preview(&journal, &from, &to)?;
                return Ok(Self {
                    directory: None,
                    tree_move: Some(movement),
                    tree_archive: None,
                    tree_copy: None,
                    changes,
                    checks,
                    mode_checks,
                    destination_modes,
                });
            }
            FileOperation::CreateDirectory { path } | FileOperation::RemoveDirectory { path } => {
                let directory = journal.preview_directory(&path, creating_directory)?;
                return Ok(Self {
                    directory: Some(directory),
                    tree_move: None,
                    tree_archive: None,
                    tree_copy: None,
                    changes,
                    checks,
                    mode_checks,
                    destination_modes,
                });
            }
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
                if let Some(mode) = unix_mode(&journal.resolve(&from)?)? {
                    mode_checks.insert(from.clone(), mode);
                    destination_modes.insert(to.clone(), mode);
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
            FileOperation::Edit {
                path,
                old_text,
                new_text,
                expected_matches,
            } => {
                if old_text.is_empty() || expected_matches == 0 {
                    return Err("old_text must be nonempty and expected_matches positive".into());
                }
                let before = snapshot(&journal, &path)?.ok_or("source file does not exist")?;
                let text = std::str::from_utf8(&before)?;
                let actual = text.match_indices(old_text.as_str()).count();
                if actual != expected_matches {
                    return Err(
                        format!("expected {expected_matches} matches, found {actual}").into(),
                    );
                }
                let after = text.replace(old_text.as_str(), &new_text).into_bytes();
                if before == after {
                    return Err("edit produces no changes".into());
                }
                changes.push(JournalChange {
                    path,
                    before: Some(before),
                    after: Some(after),
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
        Ok(Self {
            directory: None,
            tree_move: None,
            tree_archive: None,
            tree_copy: None,
            changes,
            checks,
            mode_checks,
            destination_modes,
        })
    }

    pub(crate) fn preview_batch(
        workspace: &Workspace,
        operations: Vec<FileOperation>,
    ) -> Result<Self> {
        if operations.is_empty() || operations.len() > 100 {
            return Err("batch must contain 1..=100 operations".into());
        }
        if operations.len() == 1 {
            return Self::preview(workspace, operations.into_iter().next().unwrap());
        }
        let journal = Journal::open(workspace.root())?;
        let mut combined = Self {
            directory: None,
            tree_move: None,
            tree_archive: None,
            tree_copy: None,
            changes: Vec::new(),
            checks: Vec::new(),
            mode_checks: BTreeMap::new(),
            destination_modes: BTreeMap::new(),
        };
        let mut writes: Vec<PathBuf> = Vec::new();
        let mut reads: Vec<PathBuf> = Vec::new();
        for operation in operations {
            let plan = Self::preview(workspace, operation)?;
            if plan.directory.is_some()
                || plan.tree_move.is_some()
                || plan.tree_archive.is_some()
                || plan.tree_copy.is_some()
            {
                return Err("directory operations require a single-operation batch".into());
            }
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
            combined.mode_checks.extend(plan.mode_checks);
            combined.destination_modes.extend(plan.destination_modes);
        }
        Ok(combined)
    }

    pub(crate) fn description(&self) -> Value {
        if let Some(copy) = &self.tree_copy {
            return json!({"operation":"copy_directory","from":copy.movement.from,"to":copy.movement.to,
                "entries":copy.movement.entries,"changed_files":copy.files.len(),
                "changed_directories":copy.movement.entries.len() - copy.files.len()});
        }
        if let Some(archive) = &self.tree_archive {
            return json!({"operation":"delete_directory","path":archive.path,"entries":archive.entries,
                "changed_files":archive.entries.values().filter(|entry| !entry.directory).count(),
                "changed_directories":archive.entries.values().filter(|entry| entry.directory).count(),"retained_in_journal":true});
        }
        if let Some(movement) = &self.tree_move {
            return json!({"operation":"move_directory","from":movement.from,"to":movement.to,
                "entries":movement.entries,"changed_files":movement.entries.values().filter(|entry| !entry.directory).count(),
                "changed_directories":movement.entries.values().filter(|entry| entry.directory).count()});
        }
        if let Some(directory) = &self.directory {
            return json!({"changed_files": 0, "changed_directories": 1, "directory": directory,
                "operation": if directory.after_exists { "create_directory" } else { "remove_directory" }});
        }
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
                    "operation": if change.before.is_none() { "create" } else if change.after.is_none() { "delete" } else { "edit" },
                    "before_bytes": before.len(), "after_bytes": after.len(),
                    "unified_diff": diff,
                    "after_unix_mode": self.destination_modes.get(&change.path)
                })
            })
            .collect::<Vec<_>>();
        json!({"changed_files": self.changes.len(), "files": files})
    }

    pub(crate) fn apply(&self, workspace: &Workspace) -> Result<Value> {
        let journal = Journal::open(workspace.root())?;
        if let Some(copy) = &self.tree_copy {
            let transaction = journal.commit_tree_copy(copy)?;
            return Ok(
                json!({"transaction":transaction.id,"copied_entries":copy.movement.entries.len()}),
            );
        }
        if let Some(archive) = &self.tree_archive {
            let transaction = journal.commit_tree_archive(archive.clone())?;
            return Ok(
                json!({"transaction":transaction.id,"archived_entries":archive.entries.len()}),
            );
        }
        if let Some(movement) = &self.tree_move {
            let transaction = journal.commit_tree_move(movement.clone())?;
            return Ok(
                json!({"transaction":transaction.id,"moved_entries":movement.entries.len()}),
            );
        }
        if let Some(directory) = &self.directory {
            let transaction = journal.commit_directory_preview(directory.clone())?;
            return Ok(
                json!({"transaction":transaction.id,"changed_files":0,"changed_directories":1}),
            );
        }
        for (path, expected) in &self.checks {
            if &snapshot(&journal, path)? != expected {
                return Err("source changed since preview".into());
            }
        }
        for (path, expected) in &self.mode_checks {
            if unix_mode(&journal.resolve(path)?)? != Some(*expected) {
                return Err("source permissions changed since preview".into());
            }
        }
        let transaction = journal.commit_changes_with_modes(
            Some("resident file operation".into()),
            &self.changes,
            &self.destination_modes,
        )?;
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

    #[test]
    fn edits_and_creation_share_one_transaction() {
        let root = fixture();
        fs::write(root.join("a.py"), "α\r\nold\r\n").unwrap();
        fs::write(root.join("b.py"), "old old\n").unwrap();
        let workspace = Workspace::new(&root).unwrap();
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"edit","path":"a.py","old_text":"α\r\nold","new_text":"β\r\nnew$1","expected_matches":1},
                {"op":"edit","path":"b.py","old_text":"old","new_text":"new$2","expected_matches":2},
                {"op":"create","path":"new.py","content":"created"}
            ])),
        )
        .unwrap();
        assert_eq!(
            fs::read_to_string(root.join("a.py")).unwrap(),
            "α\r\nold\r\n"
        );
        assert!(!root.join("new.py").exists());
        assert_eq!(plan.description()["files"][0]["operation"], "edit");
        let result = plan.apply(&workspace).unwrap();
        let id = result["transaction"].as_str().unwrap();
        let journal = Journal::open(&root).unwrap();
        assert_eq!(journal.load(id).unwrap().files.len(), 3);
        assert_eq!(
            fs::read_to_string(root.join("a.py")).unwrap(),
            "β\r\nnew$1\r\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("b.py")).unwrap(),
            "new$2 new$2\n"
        );
        journal.undo(id).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("a.py")).unwrap(),
            "α\r\nold\r\n"
        );
        assert_eq!(fs::read_to_string(root.join("b.py")).unwrap(), "old old\n");
        assert!(!root.join("new.py").exists());
        journal.redo(id).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("a.py")).unwrap(),
            "β\r\nnew$1\r\n"
        );
        assert_eq!(fs::read_to_string(root.join("new.py")).unwrap(), "created");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_or_stale_edit_never_partially_applies_batch() {
        let root = fixture();
        fs::write(root.join("a.py"), "old old").unwrap();
        fs::write(root.join("binary"), [255]).unwrap();
        let workspace = Workspace::new(&root).unwrap();
        for edit in [
            json!({"op":"edit","path":"a.py","old_text":"","new_text":"new","expected_matches":1}),
            json!({"op":"edit","path":"a.py","old_text":"old","new_text":"new","expected_matches":0}),
            json!({"op":"edit","path":"a.py","old_text":"old","new_text":"new","expected_matches":1}),
            json!({"op":"edit","path":"a.py","old_text":"old","new_text":"old","expected_matches":2}),
            json!({"op":"edit","path":"missing","old_text":"old","new_text":"new","expected_matches":1}),
            json!({"op":"edit","path":"binary","old_text":"old","new_text":"new","expected_matches":1}),
        ] {
            let plan = FilePlan::preview_batch(
                &workspace,
                operations(json!([
                    {"op":"create","path":"new.py","content":"must not exist"},
                    edit
                ])),
            );
            assert!(plan.is_err());
            assert!(!root.join("new.py").exists());
            assert_eq!(fs::read_to_string(root.join("a.py")).unwrap(), "old old");
        }
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"create","path":"new.py","content":"must not exist"},
                {"op":"edit","path":"a.py","old_text":"old","new_text":"new","expected_matches":2}
            ])),
        )
        .unwrap();
        fs::write(root.join("a.py"), "external").unwrap();
        assert!(plan.apply(&workspace).is_err());
        assert!(!root.join("new.py").exists());
        assert_eq!(fs::read_to_string(root.join("a.py")).unwrap(), "external");
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
        fs::write(root.join("existing.txt"), b"old").unwrap();
        let workspace = Workspace::new(&root).unwrap();
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"edit","path":"existing.txt","old_text":"old","new_text":"new","expected_matches":1},
                {"op":"create","path":"first.txt","content":"must roll back"},
                {"op":"create","path":"protected/blocked.txt","content":"cannot write"}
            ])),
        )
        .unwrap();
        let result = plan.apply(&workspace);
        fs::set_permissions(&protected, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(result.is_err());
        assert!(!root.join("first.txt").exists());
        assert_eq!(fs::read(root.join("existing.txt")).unwrap(), b"old");
        assert!(!protected.join("blocked.txt").exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn copy_and_move_preserve_permissions_through_reopened_history() {
        use std::os::unix::fs::PermissionsExt;
        for operation in ["copy", "move"] {
            let root = fixture();
            let source = root.join("script.sh");
            let destination = root.join("nested/script.sh");
            fs::write(&source, b"#!/bin/sh\nexit 0\n").unwrap();
            fs::set_permissions(&source, fs::Permissions::from_mode(0o751)).unwrap();
            let workspace = Workspace::new(&root).unwrap();
            let plan = FilePlan::preview_batch(
                &workspace,
                operations(json!([
                    {"op":operation,"from":"script.sh","to":"nested/script.sh"},
                    {"op":"create","path":"new.txt","content":"created"}
                ])),
            )
            .unwrap();
            let result = plan.apply(&workspace).unwrap();
            let id = result["transaction"].as_str().unwrap();
            let journal = Journal::open(&root).unwrap();
            let manifest = journal.load(id).unwrap();
            assert_eq!(
                manifest
                    .files
                    .iter()
                    .find(|f| f.path == "nested/script.sh")
                    .unwrap()
                    .after_unix_mode,
                Some(0o751)
            );
            let created_mode = fs::metadata(root.join("new.txt"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(
                manifest
                    .files
                    .iter()
                    .find(|f| f.path == "new.txt")
                    .unwrap()
                    .after_unix_mode,
                Some(created_mode)
            );
            for _ in 0..2 {
                assert_eq!(fs::read(&destination).unwrap(), b"#!/bin/sh\nexit 0\n");
                assert_eq!(
                    fs::metadata(&destination).unwrap().permissions().mode() & 0o7777,
                    0o751
                );
                assert_eq!(source.exists(), operation == "copy");
                journal.undo(id).unwrap();
                assert!(!destination.exists());
                assert!(!root.join("new.txt").exists());
                assert_eq!(
                    fs::metadata(&source).unwrap().permissions().mode() & 0o7777,
                    0o751
                );
                journal.redo(id).unwrap();
                assert_eq!(
                    fs::metadata(root.join("new.txt"))
                        .unwrap()
                        .permissions()
                        .mode()
                        & 0o777,
                    created_mode
                );
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[cfg(unix)]
    #[test]
    fn stale_source_permissions_reject_the_entire_batch() {
        use std::os::unix::fs::PermissionsExt;
        let root = fixture();
        fs::write(root.join("source"), b"unchanged bytes").unwrap();
        fs::set_permissions(root.join("source"), fs::Permissions::from_mode(0o755)).unwrap();
        let workspace = Workspace::new(&root).unwrap();
        let plan = FilePlan::preview_batch(
            &workspace,
            operations(json!([
                {"op":"create","path":"first","content":"must not be created"},
                {"op":"copy","from":"source","to":"copy"}
            ])),
        )
        .unwrap();
        fs::set_permissions(root.join("source"), fs::Permissions::from_mode(0o700)).unwrap();
        assert!(
            plan.apply(&workspace)
                .unwrap_err()
                .to_string()
                .contains("permissions changed")
        );
        assert!(!root.join("first").exists());
        assert!(!root.join("copy").exists());
        assert_eq!(
            fs::metadata(root.join("source"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        fs::remove_dir_all(root).unwrap();
    }
}
