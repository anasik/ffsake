use crate::{Result, WorkspaceError};

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TextPosition {
    /// One-based line number.
    pub line: usize,
    /// Zero-based Unicode scalar column within the line.
    pub column: usize,
}

/// LSP position after explicitly negotiating UTF-16. Unlike TextPosition, both
/// coordinates are zero-based and the column counts UTF-16 code units.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Utf16Position {
    pub line: u32,
    pub character: u32,
}

fn invalid_offset(text: &str, offset: usize) -> WorkspaceError {
    WorkspaceError::InvalidEditRange {
        start: offset,
        end: offset,
        len: text.len(),
    }
}

/// Convert an LSP UTF-16 position to original UTF-8 bytes. Reject out-of-line
/// positions rather than clamping them, and never split a surrogate pair.
/// LF and CRLF line endings are supported; a bare CR is ordinary line content.
pub fn from_utf16_position(text: &str, position: Utf16Position) -> Result<usize> {
    let mut start = 0;
    for _ in 0..position.line {
        let newline = text[start..]
            .find('\n')
            .ok_or_else(|| invalid_offset(text, text.len()))?;
        start += newline + 1;
    }
    let rest = &text[start..];
    let end = rest.find('\n').unwrap_or(rest.len());
    let line = if end < rest.len() {
        rest[..end].strip_suffix('\r').unwrap_or(&rest[..end])
    } else {
        rest
    };
    let mut units = 0u32;
    for (offset, character) in line.char_indices() {
        if units == position.character {
            return Ok(start + offset);
        }
        units = units
            .checked_add(character.len_utf16() as u32)
            .ok_or_else(|| invalid_offset(text, start + offset))?;
        if units > position.character {
            return Err(invalid_offset(text, start + offset));
        }
    }
    if units == position.character {
        Ok(start + line.len())
    } else {
        Err(invalid_offset(text, start + line.len()))
    }
}

/// Convert a UTF-8 byte boundary to an LSP UTF-16 position. The position between
/// CR and LF is invalid; use the end of line or start of the following line.
pub fn to_utf16_position(text: &str, offset: usize) -> Result<Utf16Position> {
    if !text.is_char_boundary(offset)
        || (offset > 0
            && text.as_bytes().get(offset) == Some(&b'\n')
            && text.as_bytes()[offset - 1] == b'\r')
    {
        return Err(invalid_offset(text, offset));
    }
    let prefix = &text[..offset];
    let line = prefix.bytes().filter(|&byte| byte == b'\n').count();
    let start = prefix.rfind('\n').map_or(0, |index| index + 1);
    let character = prefix[start..].encode_utf16().count();
    Ok(Utf16Position {
        line: u32::try_from(line).map_err(|_| invalid_offset(text, offset))?,
        character: u32::try_from(character).map_err(|_| invalid_offset(text, offset))?,
    })
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
    fn utf16_round_trips_original_byte_boundaries() {
        for text in ["", "ascii", "é🚀e\u{301}\r\n東京\n", "\n\r\n", "a\rb\r"] {
            for offset in 0..=text.len() {
                let in_crlf = offset > 0
                    && text.as_bytes().get(offset) == Some(&b'\n')
                    && text.as_bytes()[offset - 1] == b'\r';
                if !text.is_char_boundary(offset) || in_crlf {
                    assert!(to_utf16_position(text, offset).is_err());
                } else {
                    let position = to_utf16_position(text, offset).unwrap();
                    assert_eq!(from_utf16_position(text, position).unwrap(), offset);
                }
            }
            assert!(to_utf16_position(text, text.len() + 1).is_err());
            assert!(to_utf16_position(text, usize::MAX).is_err());
        }
    }

    #[test]
    fn utf16_rejects_surrogate_splits_and_out_of_line_positions() {
        let text = "é🚀\r\nx\n";
        for (line, character) in [(0, 2), (0, 4), (1, 2), (2, 1), (3, 0), (u32::MAX, 0)] {
            assert!(from_utf16_position(text, Utf16Position { line, character }).is_err());
        }
        assert_eq!(
            from_utf16_position(
                text,
                Utf16Position {
                    line: 0,
                    character: 3
                }
            )
            .unwrap(),
            6
        );
        assert_eq!(
            from_utf16_position(
                text,
                Utf16Position {
                    line: 1,
                    character: 0
                }
            )
            .unwrap(),
            8
        );
        assert_eq!(
            to_utf16_position(text, 8).unwrap(),
            Utf16Position {
                line: 1,
                character: 0
            }
        );
        for value in [
            r#"{"line":-1,"character":0}"#,
            r#"{"line":0,"character":1.5}"#,
            r#"{"line":0,"character":0,"extra":true}"#,
        ] {
            assert!(serde_json::from_str::<Utf16Position>(value).is_err());
        }
    }

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
