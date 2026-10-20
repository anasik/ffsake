use crate::journal::{Journal, JournalError, TransactionManifest};
use crate::transform::{
    MatchPosition, Mutation, TargetedPositionEdit, TargetedRangeEdit, TransformError,
    apply_mutation, collect_changes, load_paths,
};
use crate::{FileSelector, ReplaceMode, Workspace, WorkspaceError};

#[derive(Debug)]
pub enum SessionError {
    Workspace(WorkspaceError),
    Journal(JournalError),
    Transform(TransformError),
    NoActiveTarget,
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Workspace(error) => write!(f, "{error}"),
            Self::Journal(error) => write!(f, "{error}"),
            Self::Transform(error) => write!(f, "{error}"),
            Self::NoActiveTarget => write!(f, "no active target"),
        }
    }
}

impl std::error::Error for SessionError {}

impl From<WorkspaceError> for SessionError {
    fn from(value: WorkspaceError) -> Self {
        Self::Workspace(value)
    }
}

impl From<JournalError> for SessionError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}

impl From<TransformError> for SessionError {
    fn from(value: TransformError) -> Self {
        Self::Transform(value)
    }
}

pub type Result<T> = std::result::Result<T, SessionError>;

#[derive(Debug, Clone)]
pub struct ActiveTarget {
    pub selector: FileSelector,
    pub files: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct EditResult {
    pub transaction: Option<TransactionManifest>,
    pub active_files: usize,
    pub changed_files: usize,
}

#[derive(Debug, Clone)]
pub struct EditorSession {
    workspace: Workspace,
    active: Option<ActiveTarget>,
}

impl EditorSession {
    pub fn new(workspace: Workspace) -> Self {
        Self {
            workspace,
            active: None,
        }
    }

    pub fn workspace(&self) -> &Workspace {
        &self.workspace
    }

    pub fn active_target(&self) -> Option<&ActiveTarget> {
        self.active.as_ref()
    }

    pub fn set_target(&mut self, selector: FileSelector) -> Result<&ActiveTarget> {
        let files = self
            .workspace
            .select_files(&selector)?
            .into_iter()
            .map(|path| self.workspace.relative_display(&path))
            .collect();

        self.active = Some(ActiveTarget { selector, files });
        Ok(self.active.as_ref().expect("active target was just set"))
    }

    pub fn refresh_target(&mut self) -> Result<&ActiveTarget> {
        let selector = self
            .active
            .as_ref()
            .ok_or(SessionError::NoActiveTarget)?
            .selector
            .clone();
        self.set_target(selector)
    }

    pub fn clear_target(&mut self) {
        self.active = None;
    }

    pub fn replace(
        &self,
        mode: ReplaceMode,
        pattern: impl Into<String>,
        replacement: impl Into<String>,
        expected_matches: Option<usize>,
    ) -> Result<EditResult> {
        self.apply(
            Mutation::Replace {
                mode,
                pattern: pattern.into(),
                replacement: replacement.into(),
                expected_matches,
            },
            Some("editor replace".into()),
        )
    }

    pub fn delete_files(&self) -> Result<EditResult> {
        self.apply(Mutation::DeleteFiles, Some("editor delete".into()))
    }

    pub fn apply_ranges(&self, edits: Vec<TargetedRangeEdit>) -> Result<EditResult> {
        self.apply(
            Mutation::ApplyRanges { edits },
            Some("editor multi-range edit".into()),
        )
    }

    pub fn apply_positions(&self, edits: Vec<TargetedPositionEdit>) -> Result<EditResult> {
        self.apply(
            Mutation::ApplyPositions { edits },
            Some("editor position edit".into()),
        )
    }

    pub fn insert_at_matches(
        &self,
        mode: ReplaceMode,
        pattern: impl Into<String>,
        text: impl Into<String>,
        position: MatchPosition,
        expected_matches: Option<usize>,
    ) -> Result<EditResult> {
        self.apply(
            Mutation::InsertAtMatches {
                mode,
                pattern: pattern.into(),
                text: text.into(),
                position,
                expected_matches,
            },
            Some("editor insert at matches".into()),
        )
    }

    pub fn delete_matches(
        &self,
        mode: ReplaceMode,
        pattern: impl Into<String>,
        expected_matches: Option<usize>,
    ) -> Result<EditResult> {
        self.apply(
            Mutation::DeleteMatches {
                mode,
                pattern: pattern.into(),
                expected_matches,
            },
            Some("editor delete matches".into()),
        )
    }

    pub fn apply(&self, mutation: Mutation, label: Option<String>) -> Result<EditResult> {
        let active = self.active.as_ref().ok_or(SessionError::NoActiveTarget)?;
        let mut buffers = load_paths(&self.workspace, &active.files)?;
        apply_mutation(&mut buffers, &mutation)?;
        let changes = collect_changes(&buffers);
        let changed_files = changes.len();

        let transaction = if changes.is_empty() {
            None
        } else {
            Some(Journal::open(self.workspace.root())?.commit_changes(label, &changes)?)
        };

        Ok(EditResult {
            transaction,
            active_files: active.files.len(),
            changed_files,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PathSelector, ReplaceMode};
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture() -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("ffsake-session-{id}"));
        fs::create_dir_all(root.join("src")).unwrap();
        fs::write(root.join("src/a.rs"), "A").unwrap();
        fs::write(root.join("src/b.rs"), "A").unwrap();
        root
    }

    #[test]
    fn target_is_sticky_but_each_edit_is_separately_undoable() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let mut session = EditorSession::new(workspace);
        session
            .set_target(FileSelector {
                include: vec![PathSelector::Glob("**/*.rs".into())],
                exclude: vec![],
            })
            .unwrap();

        let first = session
            .replace(ReplaceMode::Literal, "A", "B", Some(2))
            .unwrap()
            .transaction
            .unwrap();

        let second = session
            .replace(ReplaceMode::Literal, "B", "C", Some(2))
            .unwrap()
            .transaction
            .unwrap();

        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "C");

        let journal = Journal::open(&root).unwrap();
        journal.undo(&second.id).unwrap();
        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "B");

        journal.undo(&first.id).unwrap();
        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "A");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn active_regex_target_is_resolved_until_refreshed() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let mut session = EditorSession::new(workspace);
        session
            .set_target(FileSelector {
                include: vec![PathSelector::Regex(r"^src/.*\.rs$".into())],
                exclude: vec![],
            })
            .unwrap();

        fs::write(root.join("src/c.rs"), "A").unwrap();
        assert_eq!(session.active_target().unwrap().files.len(), 2);

        session.refresh_target().unwrap();
        assert_eq!(session.active_target().unwrap().files.len(), 3);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn multi_range_edit_is_one_editor_undo_entry() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let mut session = EditorSession::new(workspace);
        session.set_target(FileSelector::exact("src/a.rs")).unwrap();

        let result = session
            .apply_ranges(vec![
                TargetedRangeEdit {
                    path: "src/a.rs".into(),
                    edit: crate::edits::RangeEdit {
                        start_byte: 0,
                        end_byte: 1,
                        replacement: "X".into(),
                    },
                },
                TargetedRangeEdit {
                    path: "src/a.rs".into(),
                    edit: crate::edits::RangeEdit {
                        start_byte: 1,
                        end_byte: 1,
                        replacement: "Y".into(),
                    },
                },
            ])
            .unwrap();

        let tx = result.transaction.unwrap();
        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "XY");

        Journal::open(&root).unwrap().undo(&tx.id).unwrap();
        assert_eq!(fs::read_to_string(root.join("src/a.rs")).unwrap(), "A");

        fs::remove_dir_all(root).unwrap();
    }
}
