use crate::{Result, WorkspaceError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TextPosition {
    /// One-based line number.
    pub line: usize,
    /// Zero-based Unicode scalar column within the line.
    pub column: usize,
}

pub fn to_byte_offset(text: &str, position: TextPosition) -> Result<usize> {
    if position.line == 0 {
        return Err(WorkspaceError::InvalidEditRange {
            start: 0,
            end: 0,
            len: text.len(),
        });
    }

    let mut line_start = 0usize;
    let mut current_line = 1usize;

    while current_line < position.line {
        let Some(relative_newline) = text[line_start..].find('\n') else {
            return Err(WorkspaceError::InvalidEditRange {
                start: text.len(),
                end: text.len(),
                len: text.len(),
            });
        };
        line_start += relative_newline + 1;
        current_line += 1;
    }

    let line_end = text[line_start..]
        .find('\n')
        .map(|offset| line_start + offset)
        .unwrap_or(text.len());
    let line_text = &text[line_start..line_end];

    if position.column == 0 {
        return Ok(line_start);
    }

    let mut chars_seen = 0usize;
    for (byte_offset, _) in line_text.char_indices() {
        if chars_seen == position.column {
            return Ok(line_start + byte_offset);
        }
        chars_seen += 1;
    }

    if chars_seen == position.column {
        Ok(line_end)
    } else {
        Err(WorkspaceError::InvalidEditRange {
            start: line_end,
            end: line_end,
            len: text.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_ascii_coordinates() {
        let text = "abc\ndef\n";
        assert_eq!(
            to_byte_offset(text, TextPosition { line: 2, column: 1 }).unwrap(),
            5
        );
    }

    #[test]
    fn columns_are_unicode_scalars_not_bytes() {
        let text = "aéz";
        assert_eq!(
            to_byte_offset(text, TextPosition { line: 1, column: 2 }).unwrap(),
            3
        );
    }

    #[test]
    fn allows_end_of_line_position() {
        assert_eq!(
            to_byte_offset("abc", TextPosition { line: 1, column: 3 }).unwrap(),
            3
        );
    }
}
