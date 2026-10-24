use crate::edits::RangeEdit;
use crate::transform::{LineOperation, Mutation, TargetedLineEdit, TargetedRangeEdit, Transform};
use crate::{FileSelector, PathSelector, Result, Workspace, WorkspaceError};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextSelection {
    pub path: String,
    pub start_byte: usize,
    pub end_byte: usize,
}

fn validate(text: &str, selection: &TextSelection) -> Result<()> {
    if selection.start_byte > selection.end_byte
        || selection.end_byte > text.len()
        || !text.is_char_boundary(selection.start_byte)
        || !text.is_char_boundary(selection.end_byte)
    {
        return Err(WorkspaceError::InvalidEditRange {
            start: selection.start_byte,
            end: selection.end_byte,
            len: text.len(),
        });
    }
    Ok(())
}

fn line_span(text: &str, selection: &TextSelection) -> Result<(usize, usize)> {
    validate(text, selection)?;
    let effective_end = if selection.end_byte > selection.start_byte {
        selection.end_byte - 1
    } else {
        selection.end_byte
    };
    let start_line = text[..selection.start_byte]
        .bytes()
        .filter(|byte| *byte == b'\n')
        .count()
        + 1;
    let end_line = text[..effective_end]
        .bytes()
        .filter(|byte| *byte == b'\n')
        .count()
        + 1;
    Ok((start_line, end_line))
}

pub fn clear_selection(text: &str, selection: &TextSelection) -> Result<TargetedRangeEdit> {
    validate(text, selection)?;
    Ok(TargetedRangeEdit {
        path: selection.path.clone(),
        edit: RangeEdit {
            start_byte: selection.start_byte,
            end_byte: selection.end_byte,
            replacement: String::new(),
        },
    })
}

pub fn duplicate_selection(text: &str, selection: &TextSelection) -> Result<TargetedRangeEdit> {
    validate(text, selection)?;
    Ok(TargetedRangeEdit {
        path: selection.path.clone(),
        edit: RangeEdit {
            start_byte: selection.end_byte,
            end_byte: selection.end_byte,
            replacement: text[selection.start_byte..selection.end_byte].to_string(),
        },
    })
}

pub fn indent_selection(
    text: &str,
    selection: &TextSelection,
    indentation: impl Into<String>,
) -> Result<TargetedLineEdit> {
    let (start_line, end_line) = line_span(text, selection)?;
    Ok(TargetedLineEdit {
        path: selection.path.clone(),
        start_line,
        end_line,
        operation: LineOperation::Indent {
            text: indentation.into(),
        },
    })
}

pub fn outdent_selection(
    text: &str,
    selection: &TextSelection,
    width: usize,
) -> Result<TargetedLineEdit> {
    let (start_line, end_line) = line_span(text, selection)?;
    Ok(TargetedLineEdit {
        path: selection.path.clone(),
        start_line,
        end_line,
        operation: LineOperation::Outdent { width },
    })
}

pub fn copy_selection(
    source_text: &str,
    source: &TextSelection,
    destination_path: impl Into<String>,
    destination_byte: usize,
) -> Result<TargetedRangeEdit> {
    validate(source_text, source)?;
    Ok(TargetedRangeEdit {
        path: destination_path.into(),
        edit: RangeEdit {
            start_byte: destination_byte,
            end_byte: destination_byte,
            replacement: source_text[source.start_byte..source.end_byte].to_string(),
        },
    })
}

pub fn move_selection(
    source_text: &str,
    source: &TextSelection,
    destination_path: impl Into<String>,
    destination_byte: usize,
) -> Result<Vec<TargetedRangeEdit>> {
    validate(source_text, source)?;
    let destination_path = destination_path.into();

    if destination_path == source.path
        && destination_byte >= source.start_byte
        && destination_byte <= source.end_byte
    {
        return Err(WorkspaceError::InvalidEditRange {
            start: source.start_byte,
            end: source.end_byte,
            len: source_text.len(),
        });
    }

    Ok(vec![
        TargetedRangeEdit {
            path: source.path.clone(),
            edit: RangeEdit {
                start_byte: source.start_byte,
                end_byte: source.end_byte,
                replacement: String::new(),
            },
        },
        TargetedRangeEdit {
            path: destination_path,
            edit: RangeEdit {
                start_byte: destination_byte,
                end_byte: destination_byte,
                replacement: source_text[source.start_byte..source.end_byte].to_string(),
            },
        },
    ])
}

fn guarded_transform(
    workspace: &Workspace,
    paths: impl IntoIterator<Item = String>,
    mutations: Vec<Mutation>,
) -> Result<Transform> {
    let paths = paths.into_iter().collect::<BTreeSet<_>>();
    let mut expected = BTreeMap::new();

    for path in &paths {
        expected.insert(path.clone(), workspace.content_hash(path)?);
    }

    let max_files = expected.len();
    let mut operations = vec![Mutation::AssertHashes { expected }];
    operations.extend(mutations);

    Ok(Transform {
        target: FileSelector {
            include: paths.into_iter().map(PathSelector::Exact).collect(),
            exclude: Vec::new(),
        },
        operations,
        max_files: Some(max_files),
    })
}

pub fn clear_selection_transform(
    workspace: &Workspace,
    selection: &TextSelection,
) -> Result<Transform> {
    let text = workspace.read_all_text(&selection.path)?;
    let edit = clear_selection(&text, selection)?;
    guarded_transform(
        workspace,
        [selection.path.clone()],
        vec![Mutation::ApplyRanges { edits: vec![edit] }],
    )
}

pub fn duplicate_selection_transform(
    workspace: &Workspace,
    selection: &TextSelection,
) -> Result<Transform> {
    let text = workspace.read_all_text(&selection.path)?;
    let edit = duplicate_selection(&text, selection)?;
    guarded_transform(
        workspace,
        [selection.path.clone()],
        vec![Mutation::ApplyRanges { edits: vec![edit] }],
    )
}

pub fn indent_selection_transform(
    workspace: &Workspace,
    selection: &TextSelection,
    indentation: impl Into<String>,
) -> Result<Transform> {
    let text = workspace.read_all_text(&selection.path)?;
    let edit = indent_selection(&text, selection, indentation)?;
    guarded_transform(
        workspace,
        [selection.path.clone()],
        vec![Mutation::ApplyLines { edits: vec![edit] }],
    )
}

pub fn outdent_selection_transform(
    workspace: &Workspace,
    selection: &TextSelection,
    width: usize,
) -> Result<Transform> {
    let text = workspace.read_all_text(&selection.path)?;
    let edit = outdent_selection(&text, selection, width)?;
    guarded_transform(
        workspace,
        [selection.path.clone()],
        vec![Mutation::ApplyLines { edits: vec![edit] }],
    )
}

fn validate_destination(text: &str, destination_byte: usize) -> Result<()> {
    if destination_byte > text.len() || !text.is_char_boundary(destination_byte) {
        return Err(WorkspaceError::InvalidEditRange {
            start: destination_byte,
            end: destination_byte,
            len: text.len(),
        });
    }
    Ok(())
}

pub fn copy_selection_transform(
    workspace: &Workspace,
    source: &TextSelection,
    destination_path: impl Into<String>,
    destination_byte: usize,
) -> Result<Transform> {
    let destination_path = destination_path.into();
    let source_text = workspace.read_all_text(&source.path)?;
    let destination_text = workspace.read_all_text(&destination_path)?;
    validate_destination(&destination_text, destination_byte)?;

    let edit = copy_selection(
        &source_text,
        source,
        destination_path.clone(),
        destination_byte,
    )?;

    guarded_transform(
        workspace,
        [source.path.clone(), destination_path],
        vec![Mutation::ApplyRanges { edits: vec![edit] }],
    )
}

pub fn move_selection_transform(
    workspace: &Workspace,
    source: &TextSelection,
    destination_path: impl Into<String>,
    destination_byte: usize,
) -> Result<Transform> {
    let destination_path = destination_path.into();
    let source_text = workspace.read_all_text(&source.path)?;
    let destination_text = if destination_path == source.path {
        source_text.clone()
    } else {
        workspace.read_all_text(&destination_path)?
    };
    validate_destination(&destination_text, destination_byte)?;

    let edits = move_selection(
        &source_text,
        source,
        destination_path.clone(),
        destination_byte,
    )?;

    guarded_transform(
        workspace,
        [source.path.clone(), destination_path],
        vec![Mutation::ApplyRanges { edits }],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_selection_ops_preserve_character_boundaries() {
        let selection = TextSelection {
            path: "a.rs".into(),
            start_byte: 1,
            end_byte: 3,
        };
        assert_eq!(
            duplicate_selection("abcdef", &selection).unwrap().edit,
            RangeEdit {
                start_byte: 3,
                end_byte: 3,
                replacement: "bc".into(),
            }
        );
    }

    #[test]
    fn indentation_expands_selection_to_touched_lines() {
        let selection = TextSelection {
            path: "a.rs".into(),
            start_byte: 2,
            end_byte: 7,
        };
        let edit = indent_selection("one\ntwo\nthree\n", &selection, "  ").unwrap();
        assert_eq!(edit.start_line, 1);
        assert_eq!(edit.end_line, 2);
    }

    #[test]
    fn move_compiles_to_source_and_destination_edits() {
        let selection = TextSelection {
            path: "a.rs".into(),
            start_byte: 1,
            end_byte: 3,
        };
        let edits = move_selection("abcdef", &selection, "a.rs", 6).unwrap();
        assert_eq!(edits.len(), 2);
        assert_eq!(edits[1].edit.replacement, "bc");
    }

    #[test]
    fn same_file_move_offsets_apply_correctly() {
        let selection = TextSelection {
            path: "a.rs".into(),
            start_byte: 1,
            end_byte: 3,
        };
        let edits = move_selection("abcdef", &selection, "a.rs", 6).unwrap();
        let range_edits = edits.into_iter().map(|edit| edit.edit).collect::<Vec<_>>();
        let moved = crate::edits::apply_range_edits("abcdef", &range_edits).unwrap();
        assert_eq!(moved, "adefbc");
    }

    #[test]
    fn guarded_selection_transform_rejects_stale_source() {
        use crate::transform::execute;
        use std::fs;

        let root = tempfile::Builder::new()
            .prefix("ffsake-selection-guard-")
            .tempdir()
            .unwrap()
            .keep();
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("a.txt"), "abcdef").unwrap();

        let workspace = Workspace::new(&root).unwrap();
        let selection = TextSelection {
            path: "a.txt".into(),
            start_byte: 1,
            end_byte: 3,
        };
        let transform = duplicate_selection_transform(&workspace, &selection).unwrap();

        fs::write(root.join("a.txt"), "changed").unwrap();

        assert!(matches!(
            execute(&workspace, &transform, None),
            Err(crate::transform::TransformError::Workspace(
                WorkspaceError::ContentChanged { .. }
            ))
        ));
        assert_eq!(fs::read_to_string(root.join("a.txt")).unwrap(), "changed");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn guarded_cross_file_move_commits_atomically() {
        use crate::transform::execute;
        use std::fs;

        let root = tempfile::Builder::new()
            .prefix("ffsake-selection-move-")
            .tempdir()
            .unwrap()
            .keep();
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("a.txt"), "abcdef").unwrap();
        fs::write(root.join("b.txt"), "XYZ").unwrap();

        let workspace = Workspace::new(&root).unwrap();
        let selection = TextSelection {
            path: "a.txt".into(),
            start_byte: 1,
            end_byte: 3,
        };
        let transform = move_selection_transform(&workspace, &selection, "b.txt", 1).unwrap();

        let result = execute(&workspace, &transform, Some("move selection".into())).unwrap();
        assert_eq!(result.changed_files, 2);
        assert_eq!(fs::read_to_string(root.join("a.txt")).unwrap(), "adef");
        assert_eq!(fs::read_to_string(root.join("b.txt")).unwrap(), "XbcYZ");

        let tx = result.transaction.unwrap();
        crate::journal::Journal::open(&root)
            .unwrap()
            .undo(&tx.id)
            .unwrap();
        assert_eq!(fs::read_to_string(root.join("a.txt")).unwrap(), "abcdef");
        assert_eq!(fs::read_to_string(root.join("b.txt")).unwrap(), "XYZ");

        fs::remove_dir_all(root).unwrap();
    }
}
