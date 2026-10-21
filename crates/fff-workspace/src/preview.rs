use crate::transform::{Transform, TransformError, apply_mutation, collect_changes, load_paths};
use crate::{Workspace, WorkspaceError};
use similar::TextDiff;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct FilePreview {
    pub path: String,
    pub unified_diff: String,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TransformPreview {
    pub selected_files: usize,
    pub changed_files: usize,
    pub files: Vec<FilePreview>,
}

pub fn preview(
    workspace: &Workspace,
    transform: &Transform,
) -> Result<TransformPreview, TransformError> {
    let files = workspace.select_files(&transform.target)?;

    if let Some(max) = transform.max_files
        && files.len() > max
    {
        return Err(TransformError::TooManyFiles {
            max,
            actual: files.len(),
        });
    }

    let selected_files = files.len();
    let relative = files
        .iter()
        .map(|path| workspace.relative_display(path))
        .collect::<Vec<_>>();
    let mut buffers = load_paths(workspace, &relative)?;

    for operation in &transform.operations {
        apply_mutation(&mut buffers, operation)?;
    }

    let changes = collect_changes(&buffers);
    let changed_files = changes.len();
    let mut previews = Vec::with_capacity(changed_files);

    for change in changes {
        let before = change.before.unwrap_or_default();
        let after = change.after.unwrap_or_default();
        let before_text = String::from_utf8(before).map_err(|_| {
            TransformError::Workspace(WorkspaceError::NotUtf8(workspace.root().join(&change.path)))
        })?;
        let after_text = String::from_utf8(after).map_err(|_| {
            TransformError::Workspace(WorkspaceError::NotUtf8(workspace.root().join(&change.path)))
        })?;

        let diff = TextDiff::from_lines(&before_text, &after_text)
            .unified_diff()
            .header(&format!("a/{}", change.path), &format!("b/{}", change.path))
            .to_string();

        previews.push(FilePreview {
            path: change.path,
            unified_diff: diff,
        });
    }

    Ok(TransformPreview {
        selected_files,
        changed_files,
        files: previews,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transform::Mutation;
    use crate::{FileSelector, ReplaceMode};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn preview_does_not_mutate_workspace() {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("ffsake-preview-{id}"));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("a.txt"), "hello\n").unwrap();

        let workspace = Workspace::new(&root).unwrap();
        let transform = Transform {
            target: FileSelector::exact("a.txt"),
            operations: vec![Mutation::Replace {
                mode: ReplaceMode::Literal,
                pattern: "hello".into(),
                replacement: "world".into(),
                expected_matches: Some(1),
            }],
            max_files: Some(1),
        };

        let result = preview(&workspace, &transform).unwrap();
        assert_eq!(result.changed_files, 1);
        assert!(result.files[0].unified_diff.contains("-hello"));
        assert!(result.files[0].unified_diff.contains("+world"));
        assert_eq!(fs::read_to_string(root.join("a.txt")).unwrap(), "hello\n");

        fs::remove_dir_all(root).unwrap();
    }
}
