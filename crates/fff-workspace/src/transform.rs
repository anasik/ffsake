use crate::coordinates::{TextPosition, to_byte_offset};
use crate::edits::{RangeEdit, apply_range_edits};
use crate::journal::{Journal, JournalChange, JournalError, TransactionManifest};
use crate::{FileSelector, ReplaceMode, Workspace, WorkspaceError};
use regex::Regex;
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TargetedRangeEdit {
    pub path: String,
    pub edit: RangeEdit,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TargetedPositionEdit {
    pub path: String,
    pub start: TextPosition,
    pub end: TextPosition,
    pub replacement: String,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum LineOperation {
    Delete,
    Duplicate,
    Indent { text: String },
    Outdent { width: usize },
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TargetedLineEdit {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub operation: LineOperation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MatchPosition {
    Before,
    After,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Mutation {
    AssertHashes {
        expected: BTreeMap<String, String>,
    },
    Replace {
        mode: ReplaceMode,
        pattern: String,
        replacement: String,
        expected_matches: Option<usize>,
    },
    ReplaceIdentifier {
        from: String,
        to: String,
        expected_matches: Option<usize>,
    },
    ApplyRanges {
        edits: Vec<TargetedRangeEdit>,
    },
    ApplyPositions {
        edits: Vec<TargetedPositionEdit>,
    },
    ApplyLines {
        edits: Vec<TargetedLineEdit>,
    },
    InsertAtMatches {
        mode: ReplaceMode,
        pattern: String,
        text: String,
        position: MatchPosition,
        expected_matches: Option<usize>,
    },
    DeleteMatches {
        mode: ReplaceMode,
        pattern: String,
        expected_matches: Option<usize>,
    },
    DeleteFiles,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Transform {
    pub target: FileSelector,
    pub operations: Vec<Mutation>,
    pub max_files: Option<usize>,
}

#[derive(Debug)]
pub enum TransformError {
    Workspace(WorkspaceError),
    Journal(JournalError),
    ExpectedMatchCount { expected: usize, actual: usize },
    TooManyFiles { max: usize, actual: usize },
    EmptyPattern,
}

impl std::fmt::Display for TransformError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Workspace(error) => write!(f, "{error}"),
            Self::Journal(error) => write!(f, "{error}"),
            Self::ExpectedMatchCount { expected, actual } => {
                write!(f, "expected {expected} matches, found {actual}")
            }
            Self::TooManyFiles { max, actual } => {
                write!(
                    f,
                    "target resolved to {actual} files, exceeding maximum {max}"
                )
            }
            Self::EmptyPattern => write!(f, "replacement pattern must not be empty"),
        }
    }
}

impl std::error::Error for TransformError {}

impl From<WorkspaceError> for TransformError {
    fn from(value: WorkspaceError) -> Self {
        Self::Workspace(value)
    }
}

impl From<JournalError> for TransformError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}

pub type Result<T> = std::result::Result<T, TransformError>;

#[derive(Debug, Clone)]
pub struct TransformResult {
    pub transaction: Option<TransactionManifest>,
    pub selected_files: usize,
    pub changed_files: usize,
}

#[derive(Debug, Clone)]
pub(crate) struct BufferState {
    pub path: PathBuf,
    pub before: Vec<u8>,
    pub current: Option<Vec<u8>>,
}

pub fn execute(
    workspace: &Workspace,
    transform: &Transform,
    label: Option<String>,
) -> Result<TransformResult> {
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
    let mut buffers = BTreeMap::new();

    for path in files {
        let relative = workspace.relative_display(&path);
        let bytes = fs::read(&path).map_err(WorkspaceError::from)?;
        buffers.insert(
            relative,
            BufferState {
                path,
                before: bytes.clone(),
                current: Some(bytes),
            },
        );
    }

    for operation in &transform.operations {
        apply_mutation(&mut buffers, operation)?;
    }

    let changes = collect_changes(&buffers);
    let changed_files = changes.len();

    let transaction = if changes.is_empty() {
        None
    } else {
        Some(Journal::open(workspace.root())?.commit_changes(label, &changes)?)
    };

    Ok(TransformResult {
        transaction,
        selected_files,
        changed_files,
    })
}

pub(crate) fn load_paths(
    workspace: &Workspace,
    paths: &[String],
) -> std::result::Result<BTreeMap<String, BufferState>, WorkspaceError> {
    let mut buffers = BTreeMap::new();
    for relative in paths {
        let result = workspace.read_text(relative, None, None)?;
        let path = workspace.root().join(relative);
        let bytes = fs::read(&path)?;
        buffers.insert(
            result.path,
            BufferState {
                path,
                before: bytes.clone(),
                current: Some(bytes),
            },
        );
    }
    Ok(buffers)
}

pub(crate) fn apply_mutation(
    buffers: &mut BTreeMap<String, BufferState>,
    operation: &Mutation,
) -> Result<()> {
    match operation {
        Mutation::AssertHashes { expected } => {
            for (path, expected_hash) in expected {
                let Some(state) = buffers.get(path) else {
                    return Err(TransformError::Workspace(WorkspaceError::NotFound(
                        PathBuf::from(path),
                    )));
                };
                let actual_hash = blake3::hash(&state.before).to_hex().to_string();
                if &actual_hash != expected_hash {
                    return Err(TransformError::Workspace(WorkspaceError::ContentChanged {
                        expected: expected_hash.clone(),
                        actual: actual_hash,
                    }));
                }
            }
            Ok(())
        }
        Mutation::DeleteFiles => {
            for state in buffers.values_mut() {
                state.current = None;
            }
            Ok(())
        }
        Mutation::ApplyRanges { edits } => {
            let mut by_path: BTreeMap<&str, Vec<RangeEdit>> = BTreeMap::new();
            for targeted in edits {
                by_path
                    .entry(targeted.path.as_str())
                    .or_default()
                    .push(targeted.edit.clone());
            }

            for (path, path_edits) in by_path {
                let Some(state) = buffers.get_mut(path) else {
                    return Err(TransformError::Workspace(WorkspaceError::NotFound(
                        PathBuf::from(path),
                    )));
                };
                let Some(bytes) = &state.current else {
                    continue;
                };
                let text = String::from_utf8(bytes.clone())
                    .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
                state.current = Some(apply_range_edits(&text, &path_edits)?.into_bytes());
            }
            Ok(())
        }
        Mutation::ApplyPositions { edits } => {
            let mut by_path: BTreeMap<&str, Vec<&TargetedPositionEdit>> = BTreeMap::new();
            for targeted in edits {
                by_path
                    .entry(targeted.path.as_str())
                    .or_default()
                    .push(targeted);
            }

            for (path, position_edits) in by_path {
                let Some(state) = buffers.get_mut(path) else {
                    return Err(TransformError::Workspace(WorkspaceError::NotFound(
                        PathBuf::from(path),
                    )));
                };
                let Some(bytes) = &state.current else {
                    continue;
                };
                let text = String::from_utf8(bytes.clone())
                    .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;

                let mut range_edits = Vec::with_capacity(position_edits.len());
                for targeted in position_edits {
                    range_edits.push(RangeEdit {
                        start_byte: to_byte_offset(&text, targeted.start)?,
                        end_byte: to_byte_offset(&text, targeted.end)?,
                        replacement: targeted.replacement.clone(),
                    });
                }

                state.current = Some(apply_range_edits(&text, &range_edits)?.into_bytes());
            }
            Ok(())
        }
        Mutation::ApplyLines { edits } => {
            for targeted in edits {
                let Some(state) = buffers.get_mut(targeted.path.as_str()) else {
                    return Err(TransformError::Workspace(WorkspaceError::NotFound(
                        PathBuf::from(&targeted.path),
                    )));
                };
                let Some(bytes) = &state.current else {
                    continue;
                };
                let text = String::from_utf8(bytes.clone())
                    .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
                state.current = Some(
                    apply_line_operation(
                        &text,
                        targeted.start_line,
                        targeted.end_line,
                        &targeted.operation,
                    )?
                    .into_bytes(),
                );
            }
            Ok(())
        }
        Mutation::InsertAtMatches {
            mode,
            pattern,
            text,
            position,
            expected_matches,
        } => apply_insert_at_matches(buffers, *mode, pattern, text, *position, *expected_matches),
        Mutation::DeleteMatches {
            mode,
            pattern,
            expected_matches,
        } => apply_delete_matches(buffers, *mode, pattern, *expected_matches),
        Mutation::ReplaceIdentifier {
            from,
            to,
            expected_matches,
        } => apply_identifier_replace(buffers, from, to, *expected_matches),
        Mutation::Replace {
            mode,
            pattern,
            replacement,
            expected_matches,
        } => apply_replace(buffers, *mode, pattern, replacement, *expected_matches),
    }
}

pub(crate) fn collect_changes(buffers: &BTreeMap<String, BufferState>) -> Vec<JournalChange> {
    buffers
        .iter()
        .filter_map(|(path, state)| {
            if state.current.as_deref() == Some(state.before.as_slice()) {
                None
            } else {
                Some(JournalChange {
                    path: path.clone(),
                    before: Some(state.before.clone()),
                    after: state.current.clone(),
                })
            }
        })
        .collect()
}

fn apply_line_operation(
    text: &str,
    start_line: usize,
    end_line: usize,
    operation: &LineOperation,
) -> Result<String> {
    if start_line == 0 || end_line < start_line {
        return Err(TransformError::Workspace(WorkspaceError::InvalidPath(
            format!("invalid line range: {start_line}..={end_line}"),
        )));
    }

    let mut lines = if text.is_empty() {
        Vec::new()
    } else {
        text.split_inclusive('\n')
            .map(str::to_string)
            .collect::<Vec<_>>()
    };

    if lines.is_empty() || start_line > lines.len() || end_line > lines.len() {
        return Err(TransformError::Workspace(WorkspaceError::InvalidPath(
            format!(
                "line range {start_line}..={end_line} exceeds {} line(s)",
                lines.len()
            ),
        )));
    }

    let start = start_line - 1;
    let end = end_line;

    match operation {
        LineOperation::Delete => {
            lines.drain(start..end);
        }
        LineOperation::Duplicate => {
            let duplicated = lines[start..end].to_vec();
            lines.splice(end..end, duplicated);
        }
        LineOperation::Indent { text } => {
            for line in &mut lines[start..end] {
                line.insert_str(0, text);
            }
        }
        LineOperation::Outdent { width } => {
            for line in &mut lines[start..end] {
                let remove = line
                    .as_bytes()
                    .iter()
                    .take(*width)
                    .take_while(|byte| **byte == b' ')
                    .count();
                if remove > 0 {
                    line.replace_range(0..remove, "");
                } else if line.starts_with('\t') {
                    line.replace_range(0..1, "");
                }
            }
        }
    }

    Ok(lines.concat())
}

fn collect_match_ranges(
    text: &str,
    mode: ReplaceMode,
    pattern: &str,
) -> Result<Vec<(usize, usize)>> {
    if pattern.is_empty() {
        return Err(TransformError::EmptyPattern);
    }

    match mode {
        ReplaceMode::Literal => Ok(text
            .match_indices(pattern)
            .map(|(start, matched)| (start, start + matched.len()))
            .collect()),
        ReplaceMode::Regex => {
            let regex = Regex::new(pattern).map_err(WorkspaceError::from)?;
            Ok(regex
                .find_iter(text)
                .map(|m| (m.start(), m.end()))
                .collect())
        }
    }
}

fn apply_insert_at_matches(
    buffers: &mut BTreeMap<String, BufferState>,
    mode: ReplaceMode,
    pattern: &str,
    inserted: &str,
    position: MatchPosition,
    expected_matches: Option<usize>,
) -> Result<()> {
    let mut total_matches = 0usize;

    for state in buffers.values_mut() {
        let Some(bytes) = &state.current else {
            continue;
        };
        let text = String::from_utf8(bytes.clone())
            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
        let ranges = collect_match_ranges(&text, mode, pattern)?;
        total_matches += ranges.len();

        if ranges.is_empty() {
            continue;
        }

        let edits = ranges
            .into_iter()
            .map(|(start, end)| {
                let offset = match position {
                    MatchPosition::Before => start,
                    MatchPosition::After => end,
                };
                RangeEdit {
                    start_byte: offset,
                    end_byte: offset,
                    replacement: inserted.to_string(),
                }
            })
            .collect::<Vec<_>>();

        state.current = Some(apply_range_edits(&text, &edits)?.into_bytes());
    }

    if let Some(expected) = expected_matches
        && total_matches != expected
    {
        return Err(TransformError::ExpectedMatchCount {
            expected,
            actual: total_matches,
        });
    }

    Ok(())
}

fn apply_delete_matches(
    buffers: &mut BTreeMap<String, BufferState>,
    mode: ReplaceMode,
    pattern: &str,
    expected_matches: Option<usize>,
) -> Result<()> {
    let mut total_matches = 0usize;

    for state in buffers.values_mut() {
        let Some(bytes) = &state.current else {
            continue;
        };
        let text = String::from_utf8(bytes.clone())
            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
        let ranges = collect_match_ranges(&text, mode, pattern)?;
        total_matches += ranges.len();

        if ranges.is_empty() {
            continue;
        }

        let edits = ranges
            .into_iter()
            .map(|(start, end)| RangeEdit {
                start_byte: start,
                end_byte: end,
                replacement: String::new(),
            })
            .collect::<Vec<_>>();

        state.current = Some(apply_range_edits(&text, &edits)?.into_bytes());
    }

    if let Some(expected) = expected_matches
        && total_matches != expected
    {
        return Err(TransformError::ExpectedMatchCount {
            expected,
            actual: total_matches,
        });
    }

    Ok(())
}

fn is_ascii_identifier_start(byte: u8) -> bool {
    byte == b'_' || byte.is_ascii_alphabetic()
}

fn is_ascii_identifier_continue(byte: u8) -> bool {
    is_ascii_identifier_start(byte) || byte.is_ascii_digit()
}

fn identifier_ranges(text: &str, identifier: &str) -> Vec<(usize, usize)> {
    if identifier.is_empty()
        || !identifier.is_ascii()
        || !is_ascii_identifier_start(identifier.as_bytes()[0])
        || !identifier.bytes().skip(1).all(is_ascii_identifier_continue)
    {
        return Vec::new();
    }

    let bytes = text.as_bytes();
    let needle = identifier.as_bytes();
    let mut out = Vec::new();
    let mut i = 0usize;

    while i + needle.len() <= bytes.len() {
        if &bytes[i..i + needle.len()] == needle {
            let left_ok = i == 0 || !is_ascii_identifier_continue(bytes[i - 1]);
            let right = i + needle.len();
            let right_ok = right == bytes.len() || !is_ascii_identifier_continue(bytes[right]);

            if left_ok && right_ok {
                out.push((i, right));
                i = right;
                continue;
            }
        }
        i += 1;
    }

    out
}

fn apply_identifier_replace(
    buffers: &mut BTreeMap<String, BufferState>,
    from: &str,
    to: &str,
    expected_matches: Option<usize>,
) -> Result<()> {
    if from.is_empty() {
        return Err(TransformError::EmptyPattern);
    }

    let mut total_matches = 0usize;

    for state in buffers.values_mut() {
        let Some(bytes) = &state.current else {
            continue;
        };
        let text = String::from_utf8(bytes.clone())
            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;
        let ranges = identifier_ranges(&text, from);
        total_matches += ranges.len();

        if ranges.is_empty() {
            continue;
        }

        let edits = ranges
            .into_iter()
            .map(|(start, end)| RangeEdit {
                start_byte: start,
                end_byte: end,
                replacement: to.to_string(),
            })
            .collect::<Vec<_>>();

        state.current = Some(apply_range_edits(&text, &edits)?.into_bytes());
    }

    if let Some(expected) = expected_matches
        && total_matches != expected
    {
        return Err(TransformError::ExpectedMatchCount {
            expected,
            actual: total_matches,
        });
    }

    Ok(())
}

fn apply_replace(
    buffers: &mut BTreeMap<String, BufferState>,
    mode: ReplaceMode,
    pattern: &str,
    replacement: &str,
    expected_matches: Option<usize>,
) -> Result<()> {
    if pattern.is_empty() {
        return Err(TransformError::EmptyPattern);
    }

    let regex = match mode {
        ReplaceMode::Literal => None,
        ReplaceMode::Regex => Some(Regex::new(pattern).map_err(WorkspaceError::from)?),
    };

    let mut total_matches = 0usize;

    for state in buffers.values_mut() {
        let Some(bytes) = &state.current else {
            continue;
        };

        let text = String::from_utf8(bytes.clone())
            .map_err(|_| WorkspaceError::NotUtf8(state.path.clone()))?;

        let matches = match (&regex, mode) {
            (Some(regex), ReplaceMode::Regex) => regex.find_iter(&text).count(),
            (None, ReplaceMode::Literal) => text.match_indices(pattern).count(),
            _ => unreachable!(),
        };
        total_matches += matches;

        if matches == 0 {
            continue;
        }

        let updated = match (&regex, mode) {
            (Some(regex), ReplaceMode::Regex) => regex.replace_all(&text, replacement).into_owned(),
            (None, ReplaceMode::Literal) => text.replace(pattern, replacement),
            _ => unreachable!(),
        };

        state.current = Some(updated.into_bytes());
    }

    if let Some(expected) = expected_matches
        && total_matches != expected
    {
        return Err(TransformError::ExpectedMatchCount {
            expected,
            actual: total_matches,
        });
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::PathSelector;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture() -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("ffsake-transform-{id}"));
        fs::create_dir_all(root.join("src")).unwrap();
        fs::write(root.join("src/a.rs"), "User User").unwrap();
        fs::write(root.join("src/b.rs"), "User").unwrap();
        root
    }

    #[test]
    fn transform_is_one_transaction() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let transform = Transform {
            target: FileSelector {
                include: vec![PathSelector::Glob("**/*.rs".into())],
                exclude: vec![],
            },
            operations: vec![
                Mutation::Replace {
                    mode: ReplaceMode::Literal,
                    pattern: "User".into(),
                    replacement: "Customer".into(),
                    expected_matches: Some(3),
                },
                Mutation::Replace {
                    mode: ReplaceMode::Literal,
                    pattern: "Customer".into(),
                    replacement: "Account".into(),
                    expected_matches: Some(3),
                },
            ],
            max_files: Some(10),
        };

        let result = execute(&workspace, &transform, Some("global rename".into())).unwrap();
        let tx = result.transaction.unwrap();

        assert_eq!(result.changed_files, 2);
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "Account Account"
        );

        Journal::open(&root).unwrap().undo(&tx.id).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "User User"
        );
        assert_eq!(fs::read_to_string(root.join("src/b.rs")).unwrap(), "User");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn max_files_is_a_precondition_not_control_flow() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let transform = Transform {
            target: FileSelector::all(),
            operations: vec![Mutation::DeleteFiles],
            max_files: Some(1),
        };

        assert!(matches!(
            execute(&workspace, &transform, None),
            Err(TransformError::TooManyFiles { .. })
        ));
        assert!(root.join("src/a.rs").exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn transform_supports_match_relative_insert_and_delete() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();

        let transform = Transform {
            target: FileSelector::exact("src/a.rs"),
            operations: vec![
                Mutation::InsertAtMatches {
                    mode: ReplaceMode::Literal,
                    pattern: "User".into(),
                    text: "[".into(),
                    position: MatchPosition::Before,
                    expected_matches: Some(2),
                },
                Mutation::InsertAtMatches {
                    mode: ReplaceMode::Literal,
                    pattern: "User".into(),
                    text: "]".into(),
                    position: MatchPosition::After,
                    expected_matches: Some(2),
                },
                Mutation::DeleteMatches {
                    mode: ReplaceMode::Literal,
                    pattern: " ".into(),
                    expected_matches: Some(1),
                },
            ],
            max_files: Some(1),
        };

        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "[User][User]"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn exact_range_edits_are_path_qualified() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();

        let transform = Transform {
            target: FileSelector::all(),
            operations: vec![Mutation::ApplyRanges {
                edits: vec![
                    TargetedRangeEdit {
                        path: "src/a.rs".into(),
                        edit: RangeEdit {
                            start_byte: 0,
                            end_byte: 4,
                            replacement: "Account".into(),
                        },
                    },
                    TargetedRangeEdit {
                        path: "src/b.rs".into(),
                        edit: RangeEdit {
                            start_byte: 0,
                            end_byte: 4,
                            replacement: "Member".into(),
                        },
                    },
                ],
            }],
            max_files: Some(10),
        };

        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "Account User"
        );
        assert_eq!(fs::read_to_string(root.join("src/b.rs")).unwrap(), "Member");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn position_edits_convert_to_exact_ranges() {
        let root = fixture();
        fs::write(root.join("src/a.rs"), "alpha\nbeta\n").unwrap();
        let workspace = Workspace::new(&root).unwrap();

        let transform = Transform {
            target: FileSelector::exact("src/a.rs"),
            operations: vec![Mutation::ApplyPositions {
                edits: vec![TargetedPositionEdit {
                    path: "src/a.rs".into(),
                    start: TextPosition { line: 2, column: 0 },
                    end: TextPosition { line: 2, column: 4 },
                    replacement: "BETA".into(),
                }],
            }],
            max_files: Some(1),
        };

        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "alpha\nBETA\n"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn identifier_replace_respects_ascii_identifier_boundaries() {
        let root = fixture();
        fs::write(
            root.join("src/a.rs"),
            "User SuperUser UserFactory _User User2 Vec<User> foo::User User::new",
        )
        .unwrap();
        let workspace = Workspace::new(&root).unwrap();

        let transform = Transform {
            target: FileSelector::exact("src/a.rs"),
            operations: vec![Mutation::ReplaceIdentifier {
                from: "User".into(),
                to: "Customer".into(),
                expected_matches: Some(4),
            }],
            max_files: Some(1),
        };

        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "Customer SuperUser UserFactory _User User2 Vec<Customer> foo::Customer Customer::new"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn line_edits_are_structural_and_path_qualified() {
        let root = fixture();
        fs::write(root.join("src/a.rs"), "a\nb\nc\n").unwrap();
        let workspace = Workspace::new(&root).unwrap();

        let transform = Transform {
            target: FileSelector::exact("src/a.rs"),
            operations: vec![
                Mutation::ApplyLines {
                    edits: vec![TargetedLineEdit {
                        path: "src/a.rs".into(),
                        start_line: 2,
                        end_line: 2,
                        operation: LineOperation::Duplicate,
                    }],
                },
                Mutation::ApplyLines {
                    edits: vec![TargetedLineEdit {
                        path: "src/a.rs".into(),
                        start_line: 1,
                        end_line: 2,
                        operation: LineOperation::Indent { text: "  ".into() },
                    }],
                },
            ],
            max_files: Some(1),
        };

        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "  a\n  b\nb\nc\n"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn hash_assertion_fails_closed_before_mutation() {
        let root = fixture();
        let workspace = Workspace::new(&root).unwrap();
        let expected = workspace.content_hash("src/a.rs").unwrap();
        fs::write(root.join("src/a.rs"), "changed").unwrap();

        let transform = Transform {
            target: FileSelector::exact("src/a.rs"),
            operations: vec![
                Mutation::AssertHashes {
                    expected: BTreeMap::from([("src/a.rs".into(), expected)]),
                },
                Mutation::Replace {
                    mode: ReplaceMode::Literal,
                    pattern: "changed".into(),
                    replacement: "mutated".into(),
                    expected_matches: Some(1),
                },
            ],
            max_files: Some(1),
        };

        assert!(matches!(
            execute(&workspace, &transform, None),
            Err(TransformError::Workspace(
                WorkspaceError::ContentChanged { .. }
            ))
        ));
        assert_eq!(
            fs::read_to_string(root.join("src/a.rs")).unwrap(),
            "changed"
        );

        fs::remove_dir_all(root).unwrap();
    }
}
