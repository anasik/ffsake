use crate::edits::RangeEdit;
use crate::transform::{LineOperation, TargetedLineEdit, TargetedRangeEdit};
use crate::{Result, WorkspaceError};

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
}
