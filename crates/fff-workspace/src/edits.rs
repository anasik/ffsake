use crate::{Result, WorkspaceError};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RangeEdit {
    /// UTF-8 byte offset, inclusive.
    pub start_byte: usize,
    /// UTF-8 byte offset, exclusive.
    pub end_byte: usize,
    pub replacement: String,
}

pub fn apply_range_edits(text: &str, edits: &[RangeEdit]) -> Result<String> {
    let mut ordered = edits.to_vec();
    ordered.sort_by_key(|edit| (edit.start_byte, edit.end_byte));

    let len = text.len();
    let mut previous_end = 0usize;

    for edit in &ordered {
        if edit.start_byte > edit.end_byte
            || edit.end_byte > len
            || !text.is_char_boundary(edit.start_byte)
            || !text.is_char_boundary(edit.end_byte)
        {
            return Err(WorkspaceError::InvalidEditRange {
                start: edit.start_byte,
                end: edit.end_byte,
                len,
            });
        }

        if edit.start_byte < previous_end {
            return Err(WorkspaceError::OverlappingEdits);
        }

        previous_end = edit.end_byte;
    }

    // Preserve caller order for multiple zero-width inserts at the same byte:
    // reversing the stable sorted list makes the first requested insert appear first.
    let mut updated = text.to_string();
    for edit in ordered.iter().rev() {
        updated.replace_range(edit.start_byte..edit.end_byte, &edit.replacement);
    }

    Ok(updated)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn applies_multiple_non_overlapping_ranges() {
        let updated = apply_range_edits(
            "one two three",
            &[
                RangeEdit {
                    start_byte: 0,
                    end_byte: 3,
                    replacement: "ONE".into(),
                },
                RangeEdit {
                    start_byte: 8,
                    end_byte: 13,
                    replacement: "THREE".into(),
                },
            ],
        )
        .unwrap();

        assert_eq!(updated, "ONE two THREE");
    }

    #[test]
    fn rejects_overlap() {
        assert!(matches!(
            apply_range_edits(
                "abcdef",
                &[
                    RangeEdit {
                        start_byte: 1,
                        end_byte: 4,
                        replacement: "x".into(),
                    },
                    RangeEdit {
                        start_byte: 3,
                        end_byte: 5,
                        replacement: "y".into(),
                    },
                ],
            ),
            Err(WorkspaceError::OverlappingEdits)
        ));
    }

    #[test]
    fn supports_multi_insert_same_position_deterministically() {
        let updated = apply_range_edits(
            "ab",
            &[
                RangeEdit {
                    start_byte: 1,
                    end_byte: 1,
                    replacement: "X".into(),
                },
                RangeEdit {
                    start_byte: 1,
                    end_byte: 1,
                    replacement: "Y".into(),
                },
            ],
        )
        .unwrap();

        assert_eq!(updated, "aXYb");
    }
}
