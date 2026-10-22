use crate::Workspace;
use crate::journal::{Journal, JournalChange};
use serde_json::{Value, json};
use std::fs;
use std::io;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

pub(crate) enum FileOperation {
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
