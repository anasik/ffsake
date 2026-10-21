use crate::edits::RangeEdit;
use crate::transform::{Mutation, TargetedRangeEdit, Transform};
use crate::{FileSelector, Workspace, WorkspaceError};
use fff::{
    FFFMode, FilePicker, FilePickerOptions, GrepMode, GrepSearchOptions, SharedFilePicker,
    SharedFrecency, parse_grep_query,
};
use std::collections::{BTreeMap, BTreeSet};
use std::time::Duration;

#[derive(Debug)]
pub enum SearchError {
    Fff(String),
    Workspace(WorkspaceError),
    IndexTimeout,
    Unavailable,
    StaleMatchSet {
        path: String,
        expected: String,
        actual: String,
    },
}

impl std::fmt::Display for SearchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Fff(error) => write!(f, "fff search error: {error}"),
            Self::Workspace(error) => write!(f, "{error}"),
            Self::IndexTimeout => write!(f, "fff indexing did not finish before timeout"),
            Self::Unavailable => write!(f, "fff picker is unavailable"),
            Self::StaleMatchSet {
                path,
                expected,
                actual,
            } => write!(
                f,
                "match set is stale for {path}: expected {expected}, actual {actual}"
            ),
        }
    }
}

impl std::error::Error for SearchError {}

impl From<WorkspaceError> for SearchError {
    fn from(value: WorkspaceError) -> Self {
        Self::Workspace(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum SearchMode {
    Literal,
    Regex,
}

impl From<SearchMode> for GrepMode {
    fn from(value: SearchMode) -> Self {
        match value {
            SearchMode::Literal => GrepMode::PlainText,
            SearchMode::Regex => GrepMode::Regex,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub struct SearchRange {
    pub path: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub line: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MatchSet {
    pub pattern: String,
    pub mode: SearchMode,
    pub ranges: Vec<SearchRange>,
    pub file_hashes: BTreeMap<String, String>,
    pub next_file_offset: usize,
}

impl MatchSet {
    pub fn validate(&self, workspace: &Workspace) -> Result<(), SearchError> {
        for (path, expected) in &self.file_hashes {
            let actual = workspace.content_hash(path)?;
            if &actual != expected {
                return Err(SearchError::StaleMatchSet {
                    path: path.clone(),
                    expected: expected.clone(),
                    actual,
                });
            }
        }
        Ok(())
    }

    pub fn replace_edits(&self, replacement: impl Into<String>) -> Vec<TargetedRangeEdit> {
        let replacement = replacement.into();
        self.ranges
            .iter()
            .map(|range| TargetedRangeEdit {
                path: range.path.clone(),
                edit: RangeEdit {
                    start_byte: range.start_byte,
                    end_byte: range.end_byte,
                    replacement: replacement.clone(),
                },
            })
            .collect()
    }

    pub fn insert_before_edits(&self, text: impl Into<String>) -> Vec<TargetedRangeEdit> {
        let text = text.into();
        self.ranges
            .iter()
            .map(|range| TargetedRangeEdit {
                path: range.path.clone(),
                edit: RangeEdit {
                    start_byte: range.start_byte,
                    end_byte: range.start_byte,
                    replacement: text.clone(),
                },
            })
            .collect()
    }

    pub fn insert_after_edits(&self, text: impl Into<String>) -> Vec<TargetedRangeEdit> {
        let text = text.into();
        self.ranges
            .iter()
            .map(|range| TargetedRangeEdit {
                path: range.path.clone(),
                edit: RangeEdit {
                    start_byte: range.end_byte,
                    end_byte: range.end_byte,
                    replacement: text.clone(),
                },
            })
            .collect()
    }

    pub fn target_selector(&self) -> FileSelector {
        FileSelector {
            include: self
                .file_hashes
                .keys()
                .cloned()
                .map(crate::PathSelector::Exact)
                .collect(),
            exclude: Vec::new(),
        }
    }

    fn transform_from_edits(
        &self,
        workspace: &Workspace,
        edits: Vec<TargetedRangeEdit>,
    ) -> Result<Transform, SearchError> {
        self.validate(workspace)?;
        Ok(Transform {
            target: self.target_selector(),
            operations: vec![
                Mutation::AssertHashes {
                    expected: self.file_hashes.clone(),
                },
                Mutation::ApplyRanges { edits },
            ],
            max_files: Some(self.file_hashes.len()),
        })
    }

    pub fn replace_transform(
        &self,
        workspace: &Workspace,
        replacement: impl Into<String>,
    ) -> Result<Transform, SearchError> {
        self.transform_from_edits(workspace, self.replace_edits(replacement))
    }

    pub fn insert_before_transform(
        &self,
        workspace: &Workspace,
        text: impl Into<String>,
    ) -> Result<Transform, SearchError> {
        self.transform_from_edits(workspace, self.insert_before_edits(text))
    }

    pub fn insert_after_transform(
        &self,
        workspace: &Workspace,
        text: impl Into<String>,
    ) -> Result<Transform, SearchError> {
        self.transform_from_edits(workspace, self.insert_after_edits(text))
    }
}

pub struct FffSearchIndex {
    picker: SharedFilePicker,
    frecency: SharedFrecency,
}

impl std::fmt::Debug for FffSearchIndex {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FffSearchIndex").finish_non_exhaustive()
    }
}

impl FffSearchIndex {
    pub fn open(workspace: &Workspace) -> Result<Self, SearchError> {
        let picker = SharedFilePicker::default();
        let frecency = SharedFrecency::default();

        FilePicker::new_with_shared_state(
            picker.clone(),
            frecency.clone(),
            FilePickerOptions {
                base_path: workspace.root().to_string_lossy().into_owned(),
                mode: FFFMode::Ai,
                enable_content_indexing: true,
                watch: true,
                ..Default::default()
            },
        )
        .map_err(|error| SearchError::Fff(error.to_string()))?;

        if !picker.wait_for_indexing_complete(Duration::from_secs(30)) {
            picker.cancel();
            return Err(SearchError::IndexTimeout);
        }

        Ok(Self { picker, frecency })
    }

    pub fn refresh(&self) -> Result<(), SearchError> {
        self.picker
            .trigger_full_rescan_async(&self.frecency)
            .map_err(|error| SearchError::Fff(error.to_string()))?;

        if !self
            .picker
            .wait_for_indexing_complete(Duration::from_secs(30))
        {
            return Err(SearchError::IndexTimeout);
        }

        Ok(())
    }

    pub fn search(
        &self,
        workspace: &Workspace,
        pattern: &str,
        mode: SearchMode,
        target: Option<&FileSelector>,
        page_limit: usize,
        file_offset: usize,
    ) -> Result<MatchSet, SearchError> {
        let selector = target.cloned().unwrap_or_else(FileSelector::all);
        let allowed = workspace
            .select_files(&selector)?
            .iter()
            .map(|path| workspace.relative_display(path))
            .collect::<BTreeSet<_>>();

        let guard = self
            .picker
            .read()
            .map_err(|error| SearchError::Fff(error.to_string()))?;
        let picker = guard.as_ref().ok_or(SearchError::Unavailable)?;

        let query = parse_grep_query(pattern);
        let result = picker.grep(
            &query,
            &GrepSearchOptions {
                mode: mode.into(),
                page_limit,
                file_offset,
                smart_case: false,
                trim_whitespace: false,
                ..Default::default()
            },
        );

        let mut ranges = BTreeSet::new();
        let mut file_hashes = BTreeMap::new();

        for matched in &result.matches {
            let path = result.files[matched.file_index].relative_path(picker);
            if !allowed.contains(&path) {
                continue;
            }

            for (start, end) in &matched.match_byte_offsets {
                ranges.insert(SearchRange {
                    path: path.clone(),
                    start_byte: matched.byte_offset as usize + *start as usize,
                    end_byte: matched.byte_offset as usize + *end as usize,
                    line: matched.line_number,
                });
            }

            if !file_hashes.contains_key(&path) {
                file_hashes.insert(path.clone(), workspace.content_hash(&path)?);
            }
        }

        Ok(MatchSet {
            pattern: pattern.to_string(),
            mode,
            ranges: ranges.into_iter().collect(),
            file_hashes,
            next_file_offset: result.next_file_offset,
        })
    }
}

impl Drop for FffSearchIndex {
    fn drop(&mut self) {
        self.picker.cancel();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transform::execute;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture(name: &str) -> std::path::PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("ffsake-search-{name}-{id}"));
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn match_set_builds_hash_guarded_transform() {
        let root = fixture("matchset");
        fs::write(root.join("a.txt"), "hello hello\n").unwrap();
        let workspace = Workspace::new(&root).unwrap();
        let hash = workspace.content_hash("a.txt").unwrap();

        let set = MatchSet {
            pattern: "hello".into(),
            mode: SearchMode::Literal,
            ranges: vec![
                SearchRange {
                    path: "a.txt".into(),
                    start_byte: 0,
                    end_byte: 5,
                    line: 1,
                },
                SearchRange {
                    path: "a.txt".into(),
                    start_byte: 6,
                    end_byte: 11,
                    line: 1,
                },
            ],
            file_hashes: BTreeMap::from([("a.txt".into(), hash)]),
            next_file_offset: 0,
        };

        let transform = set.replace_transform(&workspace, "world").unwrap();
        execute(&workspace, &transform, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("a.txt")).unwrap(),
            "world world\n"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn resident_fff_search_returns_exact_ranges() {
        let root = fixture("fff");
        fs::write(root.join("a.txt"), "zero needle one needle\n").unwrap();
        let workspace = Workspace::new(&root).unwrap();
        let index = FffSearchIndex::open(&workspace).unwrap();

        let set = index
            .search(
                &workspace,
                "needle",
                SearchMode::Literal,
                Some(&FileSelector::exact("a.txt")),
                100,
                0,
            )
            .unwrap();

        assert_eq!(set.ranges.len(), 2);
        assert_eq!((set.ranges[0].start_byte, set.ranges[0].end_byte), (5, 11));
        assert_eq!((set.ranges[1].start_byte, set.ranges[1].end_byte), (16, 22));

        drop(index);
        fs::remove_dir_all(root).unwrap();
    }
}
