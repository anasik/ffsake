use crate::dsl::{ExecutionPlan, Operation};
use crate::journal::{Journal, JournalChange, JournalError, TransactionManifest};
use crate::{ReplaceMode, Workspace, WorkspaceError};
use regex::Regex;
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

#[derive(Debug)]
pub enum ExecuteError {
    Workspace(WorkspaceError),
    Journal(JournalError),
    InvalidPipeline(String),
}

impl std::fmt::Display for ExecuteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Workspace(error) => write!(f, "{error}"),
            Self::Journal(error) => write!(f, "{error}"),
            Self::InvalidPipeline(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ExecuteError {}

impl From<WorkspaceError> for ExecuteError {
    fn from(value: WorkspaceError) -> Self {
        Self::Workspace(value)
    }
}

impl From<JournalError> for ExecuteError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}

pub type Result<T> = std::result::Result<T, ExecuteError>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadOutput {
    pub path: String,
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct ExecutionResult {
    pub transaction: Option<TransactionManifest>,
    pub reads: Vec<ReadOutput>,
    pub selected_files: usize,
    pub changed_files: usize,
}

#[derive(Debug, Clone)]
struct BufferState {
    path: PathBuf,
    before: Vec<u8>,
    current: Option<Vec<u8>>,
}

pub fn execute(
    workspace: &Workspace,
    plan: &ExecutionPlan,
    label: Option<String>,
) -> Result<ExecutionResult> {
    let selected = workspace.select_files(&plan.selector)?;
    let mut buffers = BTreeMap::new();

    for path in selected {
        let rel = workspace.relative_display(&path);
        let bytes = fs::read(&path).map_err(WorkspaceError::from)?;
        buffers.insert(
            rel,
            BufferState {
                path,
                before: bytes.clone(),
                current: Some(bytes),
            },
        );
    }

    let mut reads = Vec::new();

    for operation in &plan.operations {
        match operation {
            Operation::Read => {
                reads.clear();
                for (relative, state) in &buffers {
                    if let Some(bytes) = &state.current {
                        let text = String::from_utf8(bytes.clone())
                            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
                        reads.push(ReadOutput {
                            path: relative.clone(),
                            text,
                        });
                    }
                }
            }
            Operation::Delete => {
                for state in buffers.values_mut() {
                    state.current = None;
                }
            }
            Operation::Replace {
                mode,
                pattern,
                replacement,
            } => {
                apply_replace(&mut buffers, *mode, pattern, replacement)?;
            }
        }
    }

    let mut changes = Vec::new();
    for (path, state) in &buffers {
        if state.current.as_deref() != Some(state.before.as_slice()) {
            changes.push(JournalChange {
                path: path.clone(),
                before: Some(state.before.clone()),
                after: state.current.clone(),
            });
        }
    }

    let changed_files = changes.len();
    let transaction = if changes.is_empty() {
        None
    } else {
        Some(Journal::open(workspace.root())?.commit_changes(label, &changes)?)
    };

    Ok(ExecutionResult {
        transaction,
        reads,
        selected_files: buffers.len(),
        changed_files,
    })
}

fn apply_replace(
    buffers: &mut BTreeMap<String, BufferState>,
    mode: ReplaceMode,
    pattern: &str,
    replacement: &str,
) -> Result<()> {
    if pattern.is_empty() {
        return Err(ExecuteError::InvalidPipeline(
            "replacement pattern must not be empty".to_string(),
        ));
    }

    let regex = if mode == ReplaceMode::Regex {
        Some(
            Regex::new(pattern)
                .map_err(WorkspaceError::from)
                .map_err(ExecuteError::from)?,
        )
    } else {
        None
    };

    for state in buffers.values_mut() {
        let Some(bytes) = &state.current else {
            continue;
        };
        let text = String::from_utf8(bytes.clone())
            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;

        let updated = match (&regex, mode) {
            (Some(regex), ReplaceMode::Regex) => {
                regex.replace_all(&text, replacement).into_owned()
            }
            (None, ReplaceMode::Literal) => text.replace(pattern, replacement),
            _ => unreachable!(),
        };

        state.current = Some(updated.into_bytes());
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dsl;

    fn fixture() -> PathBuf {
        let root = tempfile::Builder::new()
            .prefix("ffsake-executor-")
            .tempdir()
            .unwrap()
            .keep();
        fs::create_dir_all(root.join("src")).unwrap();
        fs::write(root.join("src/a.rs"), "User User").unwrap();
        fs::write(root.join("src/b.rs"), "User").unwrap();
        root
    }

    #[test]
    fn executes_replace_pipeline_as_one_transaction() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let plan = dsl::parse("glob:**/*.rs | replace literal:User => Customer").unwrap();

        let result = execute(&workspace, &plan, Some("rename".into())).unwrap();
        let transaction = result.transaction.unwrap();

        assert_eq!(result.changed_files, 2);
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "Customer Customer"
        );
        assert_eq!(
            fs::read_to_string(root.join("src/b.rs")).unwrap(),
            "Customer"
        );

        let journal = Journal::open(&root).unwrap();
        journal.undo(&transaction.id).unwrap();
        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "User User");
        assert_eq!(fs::read_to_string(root.join("src/b.rs")).unwrap(), "User");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pipeline_applies_operations_in_memory_before_commit() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let plan = dsl::parse(
            "src/a.rs | replace literal:User => Customer | replace literal:Customer => Account",
        )
        .unwrap();

        let result = execute(&workspace, &plan, None).unwrap();

        assert_eq!(result.changed_files, 1);
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "Account Account"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn read_only_pipeline_creates_no_transaction() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let plan = dsl::parse("src/a.rs | read").unwrap();

        let result = execute(&workspace, &plan, None).unwrap();

        assert!(result.transaction.is_none());
        assert_eq!(result.reads.len(), 1);
        assert_eq!(result.reads[0].text, "User User");

        fs::remove_dir_all(root).unwrap();
    }
}
